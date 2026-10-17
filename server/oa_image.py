#!/usr/bin/env python3
"""把当轮的签到表、网格表与每台的奖状画成 PNG。

数据面板上那两个下载按钮点到的就是这里：`/api/board.png` 是签到表，一台一行；
`/api/grid.png` 是网格表，一个网格一行，后面跟着这一格里的呼号。
名单里每一台自己那份奖状是 `/api/cert.png`，参数是 `?call=呼号`。
三张都可以用 `?date=2026-10-17` 指定哪一轮，`?lang=en` 要英文。

画图用 Pillow（装了才画得动；没装时服务器回 503）。中文字体先找系统里常见的几款黑体，
找不到就退回 Pillow 自带的位图字，那时候中文会变成方块 —— 正式机器上请装上
fonts-wqy-zenhei 之类的中文字体。

配色照站点：纸白 #f2f0ea、墨 #15181c、灰 #6f7680、那一点红 #b3141f。
"""
import io
from datetime import datetime, timezone, timedelta

PAPER = (242, 240, 234)
INK = (21, 24, 28)
GREY = (111, 118, 128)
LINE = (206, 202, 194)
HOT = (179, 20, 31)

BJ = timezone(timedelta(hours=8))
PAD = 72
WIDTH = 1080

FONT_PATHS = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/unifont/unifont.ttf",
)

TEXT = {
    "zh": {
        "board": "APRSNETCN OA 签到表",
        "grid": "APRSNETCN OA 网格表",
        "grid_cols": ("网格", "呼号", "台数"),
        "cert": "参与证书",
        "cert_line": "%s 在 %s 这一轮的 APRSNETCN OA 活动里签到。",
        "cert_cols": ("网格", "路径", "时间"),
        "serial": "编号",
        "duty": "轮值呼号",
        "cols": ("呼号", "网格", "路径", "状态", "时间"),
        "rf": "射频",
        "is": "网络",
        "status": {"called": "只喊到", "acked": "已回 ACK", "confirmed": "已确认"},
        "empty": "这一轮还没有台站",
        "gridless": "（没有格子）",
        "count": "共 %d 台",
        "foot": "数据取自轮值台收到的报文",
        "gen": "生成",
    },
    "en": {
        "board": "APRSNETCN OA check-in table",
        "grid": "APRSNETCN OA grid table",
        "grid_cols": ("Grid", "Callsigns", "Stations"),
        "cert": "Certificate of participation",
        "cert_line": "%s checked in on %s during the APRSNETCN OA activity.",
        "cert_cols": ("Grid", "Path", "Time"),
        "serial": "Serial",
        "duty": "Duty callsign",
        "cols": ("Call", "Grid", "Path", "Status", "Time"),
        "rf": "RF",
        "is": "Network",
        "status": {"called": "Called", "acked": "ACKed", "confirmed": "Confirmed"},
        "empty": "No station in this round yet",
        "gridless": "(no grid)",
        "count": "%d station(s)",
        "foot": "The figures come from the messages the duty station received",
        "gen": "Generated",
    },
}

_fonts = {}


def font(size):
    """按字号取字体，取不到就退回 Pillow 自带的那款。"""
    if size in _fonts:
        return _fonts[size]
    from PIL import ImageFont
    picked = None
    for path in FONT_PATHS:
        try:
            picked = ImageFont.truetype(path, size)
            break
        except (IOError, OSError):
            continue
    if picked is None:
        picked = ImageFont.load_default()
    _fonts[size] = picked
    return picked


def _path_text(station, words):
    """那一行的路径：走射频就把中继按顺序写上，走网络就写网络。"""
    digi = [d for d in (station.get("digi") or []) if d]
    if (station.get("via") or "") == "rf":
        return " · ".join([words["rf"]] + [" -> ".join(digi)] if digi else [words["rf"]])
    return words["is"]


def _subtitle(row, note, words):
    parts = [row.get("date") or ""]
    duty = (row.get("call") or "").strip()
    if duty:
        parts.append("%s %s" % (words["duty"], duty))
    if note:
        parts.append(note)
    return " · ".join([p for p in parts if p])


def _clip(draw, text, face, width):
    """太长就切到装得下，末尾留个点。"""
    if not text or draw.textlength(text, font=face) <= width:
        return text
    while text and draw.textlength(text + "…", font=face) > width:
        text = text[:-1]
    return text + "…"


def _wrap(draw, text, face, width, limit=2):
    """一格里呼号多的时候折行：按空格断，最多 limit 行，放不下最后一行留个省略号。"""
    lines = []
    cur = ""
    for word in str(text or "").split():
        probe = (cur + " " + word).strip()
        if cur and draw.textlength(probe, font=face) > width:
            lines.append(cur)
            cur = word
        else:
            cur = probe
    if cur:
        lines.append(cur)
    while lines and lines[-1] in ("\u00b7", ""):
        lines.pop()
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = _clip(draw, lines[-1] + " ", face, width).rstrip() + "\u2026"
    return [line.rstrip(" \u00b7") for line in lines]


def _footer(draw, height, words, extra=""):
    small = font(22)
    left = "%s · %s %s（北京时间）" % (words["foot"], words["gen"],
                                  datetime.now(BJ).strftime("%Y-%m-%d %H:%M"))
    if extra:
        left = left + " · " + extra
    draw.text((PAD, height - 62), left, font=small, fill=GREY)
    mark = "aprsnet.cn/oa"
    draw.text((WIDTH - PAD - draw.textlength(mark, font=small), height - 62),
              mark, font=small, fill=GREY)


def _head(draw, title, subtitle, note):
    """标题、红杠、副标题；返回正文开始的 y。"""
    big = font(46)
    draw.text((PAD, 58), title, font=big, fill=INK)
    draw.rectangle([PAD, 138, PAD + 120, 143], fill=HOT)
    y = 168
    face = font(24)
    small = font(22)
    x = PAD
    parts = [p for p in subtitle.split(" · ") if p]
    for i, part in enumerate(parts):
        label = part + (" · " if i < len(parts) - 1 else "")
        fill = HOT if (note and part == note) else GREY
        drawn = face if fill == HOT else small
        draw.text((x, y), label, font=drawn, fill=fill)
        x = x + draw.textlength(label, font=drawn)
    return 218


def serial_of(call, date):
    """呼号 + 轮次 的 32 位 FNV-1a，写成 8 位十六进制，当奖状编号。同名同轮永远一样。"""
    h = 0x811C9DC5
    for byte in ("%s|%s" % (call, date)).encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return "%08X" % h


def _png(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_board(row, note="", lang="zh"):
    """签到表：一台一行，列是呼号 / 网格 / 路径 / 状态 / 时间。"""
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    stations = row.get("stations") or []
    top = 218
    row_h = 62
    height = max(560, top + 76 + row_h * max(len(stations), 1) + 110)
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["board"], _subtitle(row, note, words), note)

    cols = words["cols"]
    heads = font(23)
    body = font(27)
    small = font(24)
    # 列宽按最长的一行留出来，最右一列收在 WIDTH-PAD 里（英文的状态与时间更长）
    xs = (72, 246, 370, 694, 848)
    clips = (146, 96, 296, 126, 156)
    for x, name in zip(xs, cols):
        draw.text((x, top), name, font=heads, fill=GREY)
    draw.line([PAD, top + 40, WIDTH - PAD, top + 40], fill=INK, width=2)

    if not stations:
        draw.text((PAD, top + 78), words["empty"], font=body, fill=GREY)
    for i, station in enumerate(stations):
        y = top + 66 + i * row_h
        if i % 2 == 1:                                  # 隔一行铺一层极淡的底，读起来不串行
            draw.rectangle([PAD - 12, y - 12, WIDTH - PAD + 12, y + row_h - 20], fill=(238, 235, 228))
        draw.text((xs[0], y), _clip(draw, station.get("call") or "", body, clips[0]), font=body, fill=INK)
        draw.text((xs[1], y), _clip(draw, station.get("grid") or "—", small, clips[1]), font=small, fill=INK)
        draw.text((xs[2], y), _clip(draw, _path_text(station, words), small, clips[2]), font=small, fill=INK)
        draw.text((xs[3], y), _clip(draw, words["status"].get(station.get("status") or "", ""), small,
                                   clips[3]), font=small, fill=GREY)
        draw.text((xs[4], y), _clip(draw, station.get("at") or "", small, clips[4]), font=small, fill=GREY)
    _footer(draw, height, words, words["count"] % len(stations))
    return _png(image)


def grid_groups(row):
    """把这一轮的台站按网格归拢：[(网格, [呼号…]), …]，网格从 A 排到 Z，没网格的排最后。"""
    groups = {}
    for station in row.get("stations") or []:
        call = (station.get("call") or "").strip()
        if call:
            key = (station.get("grid") or "").strip().upper() or "\u2014"
            groups.setdefault(key, []).append(call)
    return sorted(groups.items(), key=lambda item: (item[0] == "\u2014", item[0]))


def render_grid(row, note="", lang="zh"):
    """网格表：一个网格一行，列是网格 / 呼号 / 台数。"""
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    groups = grid_groups(row)
    stations = row.get("stations") or []
    top = 218
    row_h = 62
    height = max(560, top + 76 + row_h * max(len(groups), 1) + 110)
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["grid"], _subtitle(row, note, words), note)

    heads = font(23)
    body = font(27)
    small = font(24)
    xs = (72, 260, 930)
    clips = (150, 640, 78)
    for x, name in zip(xs, words["grid_cols"]):
        draw.text((x, top), name, font=heads, fill=GREY)
    draw.line([PAD, top + 40, WIDTH - PAD, top + 40], fill=INK, width=2)

    if not groups:
        draw.text((PAD, top + 78), words["empty"], font=body, fill=GREY)
    rows = []
    for grid, calls in groups:
        lines = _wrap(draw, " \u00b7 ".join(calls), small, clips[1]) or [""]
        rows.append((grid, lines, len(calls)))
    y = top + 66
    for i, (grid, lines, count) in enumerate(rows):
        box_h = row_h + 34 * (len(lines) - 1)
        if i % 2 == 1:                                  # 隔一行铺一层极淡的底，读起来不串行
            draw.rectangle([PAD - 12, y - 12, WIDTH - PAD + 12, y + box_h - 20], fill=(238, 235, 228))
        draw.text((xs[0], y), _clip(draw, grid, body, clips[0]), font=body, fill=INK)
        for j, line in enumerate(lines):
            draw.text((xs[1], y + j * 34), line, font=small, fill=INK)
        draw.text((xs[2], y), str(count), font=small, fill=GREY)
        y += box_h
    _footer(draw, height, words, words["count"] % len(stations))
    return _png(image)


def render_cert(row, station, note="", lang="zh"):
    """一台一张的参与证书：呼号、哪一轮、网格 / 路径 / 时间，脚上一个编号。"""
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    call = (station.get("call") or "").strip() or "—"
    date = (row.get("date") or "").strip()
    height = 760
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["cert"], _subtitle(row, note, words), note)

    big = font(92)                                   # 主角就是呼号，一根红杠压在下面
    draw.text((PAD, 250), call, font=big, fill=INK)
    draw.rectangle([PAD, 372, PAD + draw.textlength(call, font=big), 376], fill=HOT)

    face = font(26)
    if date:
        draw.text((PAD, 418), words["cert_line"] % (call, date), font=face, fill=INK)

    label = font(22)
    value = font(24)
    facts = (station.get("grid") or "—", _path_text(station, words), station.get("at") or "")
    for i, (name, text) in enumerate(zip(words["cert_cols"], facts)):
        if not text:
            continue
        y = 486 + i * 42
        draw.text((PAD, y), name, font=label, fill=GREY)
        draw.text((PAD + 130, y), _clip(draw, text, value, WIDTH - PAD * 2 - 130), font=value, fill=INK)

    _footer(draw, height, words, "%s %s" % (words["serial"], serial_of(call, date)))
    return _png(image)


if __name__ == "__main__":                              # 手边看一眼：python3 oa_image.py 存几张样图
    import sys
    demo = {"date": "2026-10-17", "call": "BG7LZQ-3", "stations": [
        {"call": "BG7LZQ-2", "grid": "OL62AA", "via": "is", "digi": [], "at": "10-17 01:08",
         "status": "confirmed"},
        {"call": "BG6TA", "grid": "ON80", "via": "rf", "digi": ["WIDE1-1", "BG7LZQ-10"],
         "at": "10-17 07:01", "status": "acked"},
        {"call": "BH4HGA-9", "grid": "", "via": "is", "digi": [], "at": "10-17 09:26",
         "status": "called"},
    ]}
    shots = [("board", render_board(demo, note="测试轮次")),
             ("grid", render_grid(demo, note="测试轮次")),
             ("cert", render_cert(demo, demo["stations"][0], note="测试轮次"))]
    for name, body in shots:
        with open("/root/.nanobot/workspace/.scratch/oa_%s.png" % name, "wb") as fh:
            fh.write(body)
        sys.stderr.write("%s %d 字节\n" % (name, len(body)))
