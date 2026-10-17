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
import os
import re
from datetime import datetime, timezone, timedelta

import oa_region

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
        "cert": "参与证书",
        "cert_line": "%s 在 %s 这一轮的 APRSNETCN OA 活动里签到。",
        "cert_cols": ("网格", "路径", "时间"),
        "serial": "编号",
        "duty": "轮值呼号",
        "cols": ("呼号", "国家", "网格", "路径", "状态", "时间"),
        "rf": "射频",
        "is": "网络",
        "status": {"called": "只喊到", "acked": "已回 ACK", "confirmed": "已确认"},
        "empty": "这一轮还没有台站",
        "gridless": "（没有格子）",
        "lit": "有签到的格子",
        "count": "共 %d 台",
        "foot": "数据取自轮值台收到的报文",
        "gen": "生成",
    },
    "en": {
        "board": "APRSNETCN OA check-in table",
        "grid": "APRSNETCN OA grid table",
        "cert": "Certificate of participation",
        "cert_line": "%s checked in on %s during the APRSNETCN OA activity.",
        "cert_cols": ("Grid", "Path", "Time"),
        "serial": "Serial",
        "duty": "Duty callsign",
        "cols": ("Call", "Country", "Grid", "Path", "Status", "Time"),
        "rf": "RF",
        "is": "Network",
        "status": {"called": "Called", "acked": "ACKed", "confirmed": "Confirmed"},
        "empty": "No station in this round yet",
        "gridless": "(no grid)",
        "lit": "Squares with a check-in",
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


def _country(call, lang):
    """这一台的国家：前缀表里查，认不出来给短横。"""
    zh, en = oa_region.region_of(call)
    name = en if str(lang).startswith("en") else zh
    return name or "—"


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
    """脚注一行：左边是出处与生成时间，右边是站名。

    左边连上「编号 …」之后可能装不下，那就把站名挪到上一行右端，
    两段文字照样都留着，别叠在一起。
    """
    small = font(22)
    left = "%s · %s %s（北京时间）" % (words["foot"], words["gen"],
                                  datetime.now(BJ).strftime("%Y-%m-%d %H:%M"))
    if extra:
        left = left + " · " + extra
    mark = "aprsnet.cn/oa"
    mark_w = draw.textlength(mark, font=small)
    y = height - 62
    room = WIDTH - PAD * 2
    if draw.textlength(left, font=small) + 28 + mark_w > room:      # 一行挤不下
        draw.text((WIDTH - PAD - mark_w, y - 34), mark, font=small, fill=GREY)
    else:
        draw.text((WIDTH - PAD - mark_w, y), mark, font=small, fill=GREY)
        room = room - mark_w - 28
    draw.text((PAD, y), _clip(draw, left, small, room), font=small, fill=GREY)


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
    """签到表：一台一行，列是呼号 / 国家 / 网格 / 路径 / 状态 / 时间。"""
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
    # 列宽按最长的一行留出来，最右一列收在 WIDTH-PAD 里（英文的国家、状态与时间更长）
    xs = (72, 222, 362, 448, 690, 806)
    clips = (146, 130, 78, 236, 110, 128)
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
        draw.text((xs[1], y), _clip(draw, _country(station.get("call"), lang), small, clips[1]),
                  font=small, fill=GREY)
        draw.text((xs[2], y), _clip(draw, station.get("grid") or "—", small, clips[2]), font=small, fill=INK)
        draw.text((xs[3], y), _clip(draw, _path_text(station, words), small, clips[3]), font=small, fill=INK)
        draw.text((xs[4], y), _clip(draw, words["status"].get(station.get("status") or "", ""), small,
                                   clips[4]), font=small, fill=GREY)
        draw.text((xs[5], y), _clip(draw, station.get("at") or "", small, clips[5]), font=small, fill=GREY)
    _footer(draw, height, words, words["count"] % len(stations))
    return _png(image)


GRID4 = re.compile(r"^([A-R]{2}[0-9]{2})")

WIDE = 1944                                    # 世界地图那张比别的图宽，一格 9×5
CELL_W = 10
CELL_H = 5
MAP_W = CELL_W * 180
MAP_H = CELL_H * 180
FAINT = (228, 225, 218)


def grid_cell(grid):
    """把报出来的网格收成 4 位那一格：OL62AA 与 ol62cq 都是 OL62。认不出的原样留着。"""
    text = str(grid or "").strip().upper()
    if not text:
        return ""
    hit = GRID4.match(text)
    return hit.group(1) if hit else text


def grid_square(code):
    """4 位网格在世界网格上的位置：(经度序号, 纬度序号)，都是 0..179。认不出返回 None。"""
    text = grid_cell(code)                      # 6 位的也认：先收成 4 位那一格
    if len(text) != 4 or not text[2:].isdigit():
        return None
    field = text[:2]
    if len(field) != 2 or not ("A" <= field[0] <= "R" and "A" <= field[1] <= "R"):
        return None
    lon = (ord(field[0]) - 65) * 10 + int(text[2])
    lat = (ord(field[1]) - 65) * 10 + int(text[3])
    return (lon, lat)


def grid_groups(row):
    """按 4 位那一格归拢：[(网格, [呼号…]), …]，格子按字母序，没写网格的收在最后。"""
    groups = {}
    for station in row.get("stations") or []:
        call = (station.get("call") or "").strip()
        if call:
            key = grid_cell(station.get("grid")) or "—"
            groups.setdefault(key, []).append(call)
    return sorted(groups.items(), key=lambda item: (item[0] == "—", item[0]))


BASE_CACHE = []


def _world_base():
    """世界底图：仓库里 oa/world.png 那一张（与数据面板 canvas 用的是同一份）。

    缺了就只画网格，不影响出图。"""
    if BASE_CACHE:
        return BASE_CACHE[0]
    from PIL import Image
    base = None
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "oa", "world.png")
        raw = Image.open(path).convert("RGBA")
        base = raw if raw.size == (MAP_W, MAP_H) else raw.resize((MAP_W, MAP_H))
    except (IOError, OSError):
        base = None
    BASE_CACHE.append(base)
    return base


def _world_map(draw, x, y, lit):
    """世界网格地图：180×180 个 4 位格子全画出来，有签到的填红。返回地图底边。"""
    label = font(20)
    for i in range(0, 181):                     # 细的格子线
        gx = x + i * CELL_W
        gy = y + i * CELL_H
        line = LINE if i % 10 == 0 else FAINT   # 每 10 格是 field 的界，画深一点
        draw.line([gx, y, gx, y + MAP_H], fill=line, width=1)
        draw.line([x, gy, x + MAP_W, gy], fill=line, width=1)
    for i in range(0, 18):                      # 上下左右标上 field 的字母
        text = chr(65 + i)
        cx = x + i * CELL_W * 10 + (CELL_W * 10 - draw.textlength(text, font=label)) / 2.0
        draw.text((cx, y - 28), text, font=label, fill=GREY)
        draw.text((x - 12 - draw.textlength(text, font=label),
                   y + i * CELL_H * 10 + CELL_H * 5 - 12), text, font=label, fill=GREY)
    for square in lit:                           # 有签到的格子：点亮的范围画大一点，缩到页面上也看得见
        lon, lat = square
        x0 = max(x, x + (lon - 1) * CELL_W)
        x1 = min(x + MAP_W - 1, x + (lon + 2) * CELL_W - 1)
        y0 = max(y, y + (179 - lat - 1) * CELL_H)
        y1 = min(y + MAP_H - 1, y + (179 - lat + 2) * CELL_H - 1)
        draw.rectangle([x0, y0, x1, y1], fill=HOT)
    draw.rectangle([x, y, x + MAP_W, y + MAP_H], outline=INK, width=2)
    return y + MAP_H


def render_grid(row, note="", lang="zh"):
    """网格图：一张世界网格地图，180×180 个 4 位格子全在，有签到的点亮；下面跟着呼号。"""
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    groups = grid_groups(row)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    small = font(24)
    legend = []
    lit = []
    for grid, calls in groups:
        lines = _wrap(probe, " · ".join(calls), small, WIDE - PAD * 2 - 150) or [""]
        legend.append((grid, lines))
        square = grid_square(grid)
        if square is not None:
            lit.append(square)

    top = 218
    map_top = top + 30
    legend_h = 46 + sum(44 + 34 * (len(lines) - 1) for _, lines in legend) if legend else 0
    height = max(560, map_top + MAP_H + 46 + legend_h + 110)
    image = Image.new("RGB", (WIDE, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["grid"], _subtitle(row, note, words), note)
    base = _world_base()
    if base is not None:                         # 陆地压在网格线底下
        image.paste(base, (PAD, map_top), base)
    _world_map(draw, PAD, map_top, lit)

    y = map_top + MAP_H + 46
    if not legend:
        draw.text((PAD, y), words["empty"], font=font(27), fill=GREY)
    else:
        draw.text((PAD, y), words["lit"], font=font(24), fill=GREY)
        y += 46
        for grid, lines in legend:
            draw.text((PAD, y), grid, font=font(27), fill=INK)
            for i, line in enumerate(lines):
                draw.text((PAD + 150, y + i * 34), line, font=small, fill=GREY)
            y += 44 + 34 * (len(lines) - 1)

    _footer(draw, height, words, words["count"] % len(row.get("stations") or []))
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
