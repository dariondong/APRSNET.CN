#!/usr/bin/env python3
"""奖状卡片底图：收稿、审核、投票、选图。

一张底图从传上来，到出现在奖状上，要走三步：

1. 页面上填呼号、姓名、联系方式，勾上授权，选一张图传上来。落成一张 pending 记录，
   图存在数据目录的 `certbg/<id>.png`，外面看不见。
2. 主控台审核：这条记录的联系方式只有那里看得到。通过（approved）才上页面，
   退回（rejected）就留着记录与图，不展示、不参与投票。
3. 页面上投票，导出奖状时按 id 挑图：`/api/cert.png?call=…&bg=<id>`。
   `bg` 空着或者认不出，就用官方经典那张纸。

图统一转成 PNG 存起来：上传给的是 JPEG 也好，画奖状这边只认一种格式。
只用标准库 + Pillow。
"""
import base64
import binascii
import io
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
BJ = timezone(timedelta(hours=8))

MAX_BYTES = 4 * 1024 * 1024          # 解码之后 4 MB
MAX_PIXELS = 12 * 1000 * 1000        # 1200 万像素，再大就缩，别把机器拖住
MIN_SIDE = 600                       # 短边小于这个数，铺到 1080 宽的纸上就糊了
MAX_SUBMIT_PER_HOUR = 3              # 同一个地址一小时的投稿上限
MAX_PENDING = 60                     # 等着审的最多这么多，多了先不接
TEXT_MAX = {"call": 16, "name": 40, "contact": 120}

CALL_RE = re.compile(r"^[A-Z]{1,3}[0-9][A-Z]{0,4}(?:-[A-Z0-9]{1,2})?$")
_DATA_URL = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,(.*)$", re.S)


class CertBgError(Exception):
    """收稿这边不接：把原因写在文案里，页面原样显示给投稿的人。"""


def _text(form, key):
    return str(form.get(key) or "").strip()[:TEXT_MAX.get(key, 200)]


def check_form(form):
    """四个必填项与授权：缺哪一项就说哪一项，别让人猜。"""
    call = _text(form, "call").upper()
    name = _text(form, "name")
    contact = _text(form, "contact")
    if not CALL_RE.match(call):
        raise CertBgError("呼号写得不对，写成 BG7LZQ-2 这样。")
    if len(name) < 1:
        raise CertBgError("姓名要填。")
    if len(contact) < 3:
        raise CertBgError("联系方式要填，手机号、邮箱、QQ 都行。")
    if not form.get("agree"):
        raise CertBgError("要先勾上授权。")
    return call, name, contact


def decode_image(text):
    """把页面给的那串 data URL 拆回一张图，顺便看格式与大小。"""
    from PIL import Image
    raw = str(text or "").strip()
    hit = _DATA_URL.match(raw)
    if hit:
        raw = hit.group(1)
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        raise CertBgError("没有收到图。")
    if len(raw) > MAX_BYTES * 4 // 3 + 1024:
        raise CertBgError("图太大了，控制在 4 MB 以内。")
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise CertBgError("图读不出来，换一张 PNG 试试。")
    if len(blob) > MAX_BYTES:
        raise CertBgError("图太大了，控制在 4 MB 以内。")
    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
    except Exception:
        raise CertBgError("图读不出来，换一张 PNG 试试。")
    if image.format not in ("PNG", "JPEG", "WEBP"):
        raise CertBgError("只收 PNG 或 JPEG。")
    if image.width < MIN_SIDE or image.height < MIN_SIDE:
        raise CertBgError("图太小了，短边至少 %d 像素。" % MIN_SIDE)
    if image.width * image.height > MAX_PIXELS:
        raise CertBgError("图太大了，长宽乘积别超过 1200 万像素。")
    return image.convert("RGB")


def new_id(now=None):
    """一张一个 id：日期打头看着就知道是哪天收到的，后面六位随机。"""
    day = (now or datetime.now(BJ)).strftime("%y%m%d")
    return "bg-%s-%s" % (day, secrets.token_hex(3))


def folder(store):
    """底图放在数据目录下，跟库挨着：备份、搬机器都是一个目录。"""
    path = os.path.join(store.data_dir or ".", "certbg")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def file_of(store, bid):
    return os.path.join(folder(store), "%s.png" % str(bid or "").strip())


def save(store, form, ip="", round_code=""):
    """收一张：查表单、收图、落记录。返回这条记录（还没审，status 是 pending）。"""
    call, name, contact = check_form(form)
    image = decode_image(form.get("image"))
    if store.certbg_recent(ip, 3600) >= MAX_SUBMIT_PER_HOUR:
        raise CertBgError("这个地址一小时传得太多了，过一阵再传。")
    if len(store.certbg_rows("pending")) >= MAX_PENDING:
        raise CertBgError("等着审的太多了，过一阵再传。")
    bid = new_id()
    path = file_of(store, bid)
    image.save(path, "PNG")
    record = {"id": bid, "call": call, "name": name, "contact": contact,
              "agree_ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "file": os.path.basename(path), "w": image.width, "h": image.height,
              "status": "pending", "round": str(round_code or ""), "ip": ip}
    store.certbg_add(record)
    return store.certbg_get(bid)


def load(store, bid):
    """要画的那张图：只认审核通过的，别的当没有。"""
    from PIL import Image
    row = store.certbg_get(bid)
    if not row or row.get("status") != "approved":
        return None
    path = file_of(store, row["id"])
    if not os.path.isfile(path):
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def remove(store, bid):
    """退掉或者下架之后，图也一起删，别留着占地方。"""
    row = store.certbg_get(bid)
    if not row:
        return False
    path = file_of(store, row["id"])
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return store.certbg_delete(row["id"])


def public(row, votes):
    """页面上那张卡片能看到的东西：署名只放呼号与姓名，联系方式与地址一个字都不给。

    图自己的地址由调用方拼：页面拿 imgBase 拼，主控台拼 /api/。
    这里只给 id，两边各自拼 `certbg/image/<id>.png`。
    """
    return {"id": row["id"],
            "call": row.get("call") or "",
            "name": row.get("name") or "",
            "round": row.get("round") or "",
            "at": (row.get("ts") or "")[:10],
            "w": int(row.get("w") or 0),
            "h": int(row.get("h") or 0),
            "votes": int(votes.get(row["id"], 0))}


def pick_top(items, round_code=""):
    """最高票那张：票多的在前，票一样就早传上来的在前。只要本期就把范围缩到本期。"""
    pool = [it for it in items if not round_code or it["round"] == round_code]
    if not pool:
        return ""
    pool.sort(key=lambda it: (-it["votes"], it.get("at") or "", it["id"]))
    return pool[0]["id"]


def listing(store, round_code=""):
    """`/api/certbg` 那份：审核通过的底图 + 票数 + 两张「选哪张」的提示。"""
    rows = store.certbg_rows("approved")
    votes = store.certbg_votes()
    items = [public(row, votes) for row in rows]
    items.sort(key=lambda it: (-it["votes"], it.get("at") or "", it["id"]))
    return {"ok": True, "round": round_code, "items": items,
            "top": pick_top(items),
            "topCurrent": pick_top(items, round_code)}


def queue(store):
    """主控台那份：等着审的 + 上架了的 + 退回的，联系方式只在这一份里。"""
    votes = store.certbg_votes()
    out = {"pending": [], "approved": [], "rejected": [], "votes": votes}
    for key in ("pending", "approved", "rejected"):
        rows = store.certbg_rows(key)
        for row in (rows if key != "rejected" else rows[:20]):
            item = dict(row)
            item["votes"] = int(votes.get(row["id"], 0))
            out[key].append(item)
    return out
