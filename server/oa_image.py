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
from datetime import datetime, date, timezone, timedelta

import oa_region
import oa_tocall

PAPER = (242, 240, 234)
INK = (21, 24, 28)
GREY = (111, 118, 128)
LINE = (206, 202, 194)
HOT = (179, 20, 31)

BJ = timezone(timedelta(hours=8))
PAD = 72
WIDTH = 1080
# 签到表比别的图多一列设备，纸也宽一点；设备名太长的封在这个宽度里，格里折两行
BOARD_W = 1260
DEVICE_MAX = 260

# 编号用的两套字母：B36 拼呼号与网格，B32（Crockford，去掉 I L O U）写最终那串
B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# 编号是几段定长 32 进制拼起来的：呼号 7、SSID 1、天数 3、分钟 3、四位网格 3、后两位 2、长度 1
SERIAL_SPAN = (7, 1, 3, 3, 3, 2, 1)
SERIAL_LEN = sum(SERIAL_SPAN)
SERIAL_EPOCH = date(2020, 1, 1)

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
        "cert_cols": ("国家", "网格", "路径", "时间"),
        "serial": "编号",
        "duty": "轮值呼号",
        "cols": ("呼号", "设备", "国家", "网格", "路径", "状态", "时间"),
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
        "cert_cols": ("Country", "Grid", "Path", "Time"),
        "serial": "Serial",
        "duty": "Duty callsign",
        "cols": ("Call", "Device", "Country", "Grid", "Path", "Status", "Time"),
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


def _fit_lines(draw, text, face, width, limit=2):
    """一行装不下就在空格处断开，最多 limit 行；真装不下才切尾巴留点。"""
    text = str(text or "")
    if not text or draw.textlength(text, font=face) <= width:
        return [text]
    lines = []
    cur = ""
    for word in text.split(" "):
        probe = (cur + " " + word).strip()
        if cur and draw.textlength(probe, font=face) > width:
            lines.append(cur)
            cur = word
        else:
            cur = probe
    if cur:
        lines.append(cur)
    while len(lines) > limit:
        tail = " ".join(lines[limit - 1:])
        lines = lines[:limit - 1] + [_clip(draw, tail, face, width)]
    return [_clip(draw, line, face, width) for line in lines if line]


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


def _footer(draw, height, words, extra="", width=None):
    """脚注：左边是出处与生成时间，右边是站名。

    一行装不下就折成两行，站名始终贴着最后一行的右端，两段文字都留着。
    """
    width = width or WIDTH
    small = font(22)
    left = "%s · %s %s（北京时间）" % (words["foot"], words["gen"],
                                  datetime.now(BJ).strftime("%Y-%m-%d %H:%M"))
    if extra:
        left = left + " · " + extra
    mark = "aprsnet.cn/oa"
    mark_w = draw.textlength(mark, font=small)
    y = height - 62
    room = width - PAD * 2 - mark_w - 28
    lines = _fit_lines(draw, left, small, room, 2)
    y0 = y - 34 * (len(lines) - 1)
    for i, line in enumerate(lines):
        draw.text((PAD, y0 + i * 34), line, font=small, fill=GREY)
    draw.text((width - PAD - mark_w, y0 + 34 * (len(lines) - 1)), mark, font=small, fill=GREY)


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


def serial_of(call, date=None, at="", grid=""):
    """编号：把呼号、场次、时间、网格算成几段定长的 32 进制，直接拼成 20 位。

    每段的位置与进制都是固定的（见 SERIAL_SPAN），拆回去只要读数字，
    不用名单、不用问服务器、不用任何文件。
    """
    head, ssid = _split_call(call)
    call_val = 0
    for ch in head[:6]:                                # 呼号按 36 进制当数看，不补齐
        call_val = call_val * 36 + B36.index(ch)
    clock = re.search(r"(\d{1,2}):(\d{1,2})", str(at or ""))
    minutes = (int(clock.group(1)) * 60 + int(clock.group(2))) if clock else 0
    g4, g6, glen = _grid_code(grid)
    return "".join([
        _b32(call_val, SERIAL_SPAN[0]),
        _b32(ssid, SERIAL_SPAN[1]),
        _b32(_days_of(date), SERIAL_SPAN[2]),
        _b32(minutes, SERIAL_SPAN[3]),
        _b32(g4, SERIAL_SPAN[4]),
        _b32(g6, SERIAL_SPAN[5]),
        _b32(glen, SERIAL_SPAN[6]),
    ])


def read_serial(code):
    """把编号拆回去：呼号、场次、时间、网格。只用算术，任何文件都不需要。"""
    text = "".join(ch for ch in str(code or "").upper() if ch in B32 or ch in "OIL")
    if len(text) != SERIAL_LEN:
        return None
    vals = []
    pos = 0
    for width in SERIAL_SPAN:
        val = 0
        for ch in text[pos:pos + width]:
            v = B32.find(ch)
            if v < 0:
                v = {"O": 0, "I": 1, "L": 1}.get(ch, -1)
            if v < 0:
                return None
            val = val * 32 + v
        vals.append(val)
        pos += width
    call_val, ssid, days, minutes, g4, g6, glen = vals
    letters = ""
    for i in range(6):
        letters = B36[call_val % 36] + letters
        call_val //= 36
    call = letters.lstrip("0") or "0"
    if ssid:
        call = "%s-%d" % (call, ssid)
    grid = ""
    if glen == 1:
        grid = _grid_text(g4, None)
    elif glen == 2:
        grid = _grid_text(g4, g6)
    return {"call": call, "date": _date_of(days),
            "at": "%02d:%02d" % (minutes // 60, minutes % 60), "grid": grid}


def _split_call(call):
    """呼号拆成「字母部分」和 SSID；没有 SSID 就是 0。"""
    text = str(call or "").strip().upper()
    head, _, tail = text.partition("-")
    head = "".join(ch for ch in head if ch in B36)
    ssid = int(tail) if tail.isdigit() and 0 <= int(tail) <= 15 else 0
    return head, ssid


def _days_of(day, epoch=SERIAL_EPOCH):
    """场次离 2020-01-01 多少天，收到 0..4095 里。"""
    text = str(day or "").strip()
    try:
        y, m, d = (int(part) for part in text.split("-"))
        return min(4095, max(0, (date(y, m, d) - epoch).days))
    except (ValueError, TypeError):
        return 0


def _date_of(days, epoch=SERIAL_EPOCH):
    try:
        day = epoch + timedelta(days=int(days))
    except (ValueError, OverflowError):
        return ""
    return day.isoformat()


def _grid_code(grid):
    """网格写成两个整数：四位那一格、后面两位，以及长度标记。"""
    text = "".join(ch for ch in str(grid or "").strip().upper() if ch in B36)
    if len(text) >= 4 and re.match(r"^[A-R]{2}[0-9]{2}$", text[:4]):
        a, b, d1, d2 = text[0], text[1], text[2], text[3]
        g4 = (((ord(a) - 65) * 18 + (ord(b) - 65)) * 10 + int(d1)) * 10 + int(d2)
        if len(text) >= 6 and "A" <= text[4] <= "X" and "A" <= text[5] <= "X":
            return g4, (ord(text[4]) - 65) * 24 + (ord(text[5]) - 65), 2
        return g4, 0, 1
    return 0, 0, 0


def _grid_text(g4, g6):
    d2 = g4 % 10
    g4 //= 10
    d1 = g4 % 10
    g4 //= 10
    b = g4 % 18
    a = g4 // 18
    text = "%s%s%d%d" % (chr(65 + a), chr(65 + b), d1, d2)
    if g6 is not None:
        text += "%s%s" % (chr(65 + g6 // 24), chr(65 + g6 % 24))
    return text


def _b32(value, width):
    """一段定长的 32 进制：不够宽的左边补 0。"""
    value = max(0, int(value))
    out = ""
    while value:
        out = B32[value % 32] + out
        value //= 32
    return out.rjust(width, "0")[-width:]


def _png(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_board(row, note="", lang="zh"):
    """签到表：一台一行，列是呼号 / 设备 / 国家 / 网格 / 路径 / 状态 / 时间。

    设备看这一台最近一次喊到的目的呼号：官方表里认得就写设备名，认不出就把呼号写上去。
    """
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    stations = row.get("stations") or []
    top = 218
    row_h = 62
    height = max(560, top + 76 + row_h * max(len(stations), 1) + 110)
    image = Image.new("RGB", (BOARD_W, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["board"], _subtitle(row, note, words), note)

    cols = words["cols"]
    heads = font(23)
    body = font(27)
    small = font(24)
    faces = (body, small, small, small, small, small, small)
    cells = []
    for station in stations:
        cells.append((
            str(station.get("call") or ""),
            oa_tocall.device_of(station.get("tocall")),
            _country(station.get("call"), lang),
            str(station.get("grid") or "\u2014"),
            _path_text(station, words),
            str(words["status"].get(station.get("status") or "", "")),
            str(station.get("at") or ""),
        ))
    # 列宽按这份名单里最长的一条来排：宁可某一列宽一点，也不把字切成省略号
    want = [0] * len(cols)
    for line in cells:
        for i, cell in enumerate(line):
            want[i] = max(want[i], draw.textlength(cell, font=faces[i]))
    for i, name in enumerate(cols):
        want[i] = max(want[i], draw.textlength(name, font=heads))
    want[1] = min(want[1], DEVICE_MAX)               # 设备名可以很长，这一列封顶
    gap = 24
    room = BOARD_W - PAD * 2 - gap * (len(cols) - 1)
    need = sum(want)
    if need <= room:                                 # 富余的都给路径那一列
        want[4] += room - need
    else:                                            # 真挤不下：先压设备（格里可以折行），再压路径、国家、状态、时间
        over = need - room
        for i, floor in ((1, 170), (4, 210), (2, 100), (5, 110), (6, 140), (4, 150), (3, 70)):
            take = min(over, max(0.0, want[i] - floor))
            want[i] -= take
            over -= take
            if over <= 0:
                break
    xs = []
    x = PAD
    for width in want:
        xs.append(int(round(x)))
        x += width + gap
    clips = list(want)
    for x, name in zip(xs, cols):
        draw.text((x, top), name, font=heads, fill=GREY)
    draw.line([PAD, top + 40, BOARD_W - PAD, top + 40], fill=INK, width=2)

    if not stations:
        draw.text((PAD, top + 78), words["empty"], font=body, fill=GREY)
    for i, line in enumerate(cells):
        y = top + 66 + i * row_h
        if i % 2 == 1:                                  # 隔一行铺一层极淡的底，读起来不串行
            draw.rectangle([PAD - 12, y - 12, BOARD_W - PAD + 12, y + 46], fill=(238, 235, 228))
        for j, cell in enumerate(line):
            face = faces[j]
            fill = INK if j in (0, 3, 4) else GREY
            if j in (1, 4):                             # 设备与路径最长：一行装不下就在本行里折成两行
                for k, part in enumerate(_fit_lines(draw, cell, face, clips[j], 2)):
                    draw.text((xs[j], y + k * 29), part, font=face, fill=fill)
            else:
                draw.text((xs[j], y), _clip(draw, cell, face, clips[j]), font=face, fill=fill)
    _footer(draw, height, words, words["count"] % len(stations), BOARD_W)
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
    """一台一张的参与证书：一个大呼号压一根红杠，下面一张规规矩矩的字段表，右上是编号。"""
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    call = (station.get("call") or "").strip() or "\u2014"
    date = (row.get("date") or "").strip()
    # 字段表：一行一条，细线隔开。一行装不下就折成两行，整张纸跟着长高，不切字
    facts = (_country(call, lang), station.get("grid") or "\u2014",
             _path_text(station, words), station.get("at") or "")
    value = font(27)
    label = font(21)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8), PAPER))
    rows = [(name, _fit_lines(probe, text, value, WIDTH - PAD * 2 - 150, 2))
            for name, text in zip(words["cert_cols"], facts)]
    height = 800 + sum(34 * (len(lines) - 1) for _, lines in rows)
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["cert"], _subtitle(row, note, words), note)

    serial = "%s %s" % (words["serial"], serial_of(call, date, station.get("at"),
                                                  station.get("grid")))
    plate = font(21)
    room = WIDTH - PAD - (PAD + draw.textlength(words["cert"], font=font(46)) + 32)
    while plate.size > 13 and draw.textlength(serial, font=plate) > room:
        plate = font(plate.size - 2)                  # 编号放不下就收小字，别压到标题
    plate_w = draw.textlength(serial, font=plate)
    draw.text((WIDTH - PAD - plate_w - 16, 68), serial, font=plate, fill=GREY)

    big = font(96)                                    # 主角就是呼号
    draw.text((PAD, 246), call, font=big, fill=INK)
    call_w = min(draw.textlength(call, font=big), WIDTH - PAD * 2)
    draw.rectangle([PAD, 384, PAD + call_w, 388], fill=HOT)

    face = font(26)
    if date:
        draw.text((PAD, 408), words["cert_line"] % (call, date), font=face, fill=INK)

    y = 470
    draw.line([PAD, y, WIDTH - PAD, y], fill=INK, width=2)
    for name, lines in rows:
        draw.text((PAD, y + 14), name, font=label, fill=GREY)
        for i, line in enumerate(lines):
            draw.text((PAD + 150, y + 7 + i * 34), line, font=value, fill=INK)
        y += 48 + 34 * (len(lines) - 1)
        draw.line([PAD, y, WIDTH - PAD, y], fill=LINE, width=1)

    _footer(draw, height, words)
    return _png(image)


if __name__ == "__main__":                              # 手边看一眼：python3 oa_image.py 存几张样图
    import sys
    demo = {"date": "2026-10-17", "call": "BG7LZQ-3", "stations": [
        {"call": "BG7LZQ-2", "grid": "OL62AA", "via": "is", "digi": [], "at": "10-17 01:08",
         "status": "confirmed", "tocall": "APALOC"},
        {"call": "BG6TA", "grid": "ON80", "via": "rf", "digi": ["WIDE1-1", "BG7LZQ-10"],
         "at": "10-17 07:01", "status": "acked", "tocall": "APDR16"},
        {"call": "BH4HGA-9", "grid": "", "via": "is", "digi": [], "at": "10-17 09:26",
         "status": "called", "tocall": "APSTKA"},
        {"call": "BA7LUT-12", "grid": "OL63", "via": "is", "digi": [], "at": "10-17 11:40",
         "status": "acked", "tocall": "ZZ9ZZZ"},
    ]}
    shots = [("board", render_board(demo, note="测试轮次")),
             ("grid", render_grid(demo, note="测试轮次")),
             ("cert", render_cert(demo, demo["stations"][0], note="测试轮次"))]
    for name, body in shots:
        with open("/root/.nanobot/workspace/.scratch/oa_%s.png" % name, "wb") as fh:
            fh.write(body)
        sys.stderr.write("%s %d 字节\n" % (name, len(body)))
