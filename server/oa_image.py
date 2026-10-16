#!/usr/bin/env python3
"""把当轮的签到表与签到点画成 PNG。

数据页面上的那两个下载按钮点到的就是这里：`/api/board.png` 是签到表，
`/api/points.png` 是签到点。可以用 `?date=2026-10-17` 指定哪一轮，`?lang=en` 要英文。

画图用 Pillow（装了才画得动；没装时服务器回 503）。中文字体先找系统里常见的几款黑体，
找不到就退回 Pillow 自带的位图字，那时候中文会变成方块 —— 正式机器上请装上
fonts-wqy-zenhei 之类的中文字体。

配色照站点：纸白 #f2f0ea、墨 #15181c、灰 #6f7680、那一点红 #b3141f。
"""
import io
import re
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
        "points": "APRSNETCN OA 签到点",
        "duty": "轮值呼号",
        "cols": ("呼号", "网格", "路径", "状态", "时间"),
        "rf": "射频",
        "is": "网络",
        "status": {"called": "只喊到", "acked": "已回 ACK", "confirmed": "已确认"},
        "empty": "这一轮还没有台站",
        "empty_points": "这一轮还没有可以画的签到点",
        "gridless": "（没有格子）",
        "lon": "经度",
        "lat": "纬度",
        "count": "共 %d 台",
        "foot": "数据取自轮值台收到的报文",
        "gen": "生成",
    },
    "en": {
        "board": "APRSNETCN OA check-in table",
        "points": "APRSNETCN OA check-in points",
        "duty": "Duty callsign",
        "cols": ("Call", "Grid", "Path", "Status", "Time"),
        "rf": "RF",
        "is": "Network",
        "status": {"called": "Called", "acked": "ACKed", "confirmed": "Confirmed"},
        "empty": "No station in this round yet",
        "empty_points": "No check-in point to draw in this round",
        "gridless": "(no grid)",
        "lon": "Longitude",
        "lat": "Latitude",
        "count": "%d station(s)",
        "foot": "The figures come from the messages the duty station received",
        "gen": "Generated",
    },
}

_GRID_RE = re.compile(r"^([A-R]{2})([0-9]{2})([A-X]{2})?$")
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


def grid_center(grid):
    """4 位或 6 位梅登黑德网格 → 格子中心的 (纬度, 经度)；认不出来给 None。"""
    hit = _GRID_RE.match((grid or "").strip().upper())
    if not hit:
        return None
    lon = -180.0 + 20.0 * (ord(hit.group(1)[0]) - ord("A")) + 2.0 * int(hit.group(2)[0])
    lat = -90.0 + 10.0 * (ord(hit.group(1)[1]) - ord("A")) + 1.0 * int(hit.group(2)[1])
    if hit.group(3):
        lon = lon + (ord(hit.group(3)[0]) - ord("A")) / 6.0 + 1 / 12.0
        lat = lat + (ord(hit.group(3)[1]) - ord("A")) / 12.0 + 1 / 24.0
    else:
        lon, lat = lon + 1.0, lat + 0.5
    return (lat, lon)


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


def render_points(row, note="", lang="zh"):
    """签到点：每个格子画一个点，按经纬度标在带刻度的方框里。"""
    from PIL import Image, ImageDraw
    words = TEXT.get(lang, TEXT["zh"])
    stations = row.get("stations") or []
    height = 800
    top, bottom = 218, height - 110
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, words["points"], _subtitle(row, note, words), note)

    box = [PAD, top, WIDTH - PAD, bottom]
    draw.rectangle(box, outline=LINE, width=2)

    spots = []
    for station in stations:
        center = grid_center(station.get("grid"))
        if center:
            spots.append((station.get("call") or "", center[0], center[1]))
    if not spots:
        draw.text((PAD + 30, top + 60), words["empty_points"], font=font(27), fill=GREY)
    else:
        lats = [s[1] for s in spots]
        lons = [s[2] for s in spots]
        lat0, lat1 = min(lats), max(lats)
        lon0, lon1 = min(lons), max(lons)
        span = max(lat1 - lat0, lon1 - lon0, 2.0)       # 一台的时候也要有刻度，别糊成一格
        mid_lat, mid_lon = (lat0 + lat1) / 2.0, (lon0 + lon1) / 2.0
        half = span / 2.0 * 1.15                         # 留出边距，点别贴在框上
        lat0, lat1 = mid_lat - half, mid_lat + half
        lon0, lon1 = mid_lon - half, mid_lon + half
        w, h = box[2] - box[0], box[3] - box[1]

        def px(lon):
            return box[0] + (lon - lon0) / (lon1 - lon0) * w

        def py(lat):
            return box[3] - (lat - lat0) / (lat1 - lat0) * h

        step = 1.0 if span <= 12 else (2.0 if span <= 30 else 10.0)
        tick = font(20)
        g = (int(lon0 // step) + 1) * step
        while g < lon1:
            x = px(g)
            draw.line([x, box[1], x, box[3]], fill=LINE, width=1)
            draw.text((x - draw.textlength("%.0f" % g, font=tick) / 2, box[3] + 8),
                      "%.0f" % g, font=tick, fill=GREY)
            g = g + step
        g = (int(lat0 // step) + 1) * step
        while g < lat1:
            y = py(g)
            draw.line([box[0], y, box[2], y], fill=LINE, width=1)
            draw.text((box[0] - 12 - draw.textlength("%.0f" % g, font=tick), y - 10),
                      "%.0f" % g, font=tick, fill=GREY)
            g = g + step
        draw.text(((box[0] + box[2]) / 2 - 20, box[3] + 34), words["lon"], font=tick, fill=GREY)
        draw.text((box[0], box[1] - 34), words["lat"], font=tick, fill=GREY)

        name = font(22)
        for call, lat, lon in spots:
            x, y = px(lon), py(lat)
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=HOT)
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=PAPER, width=2)
            label = call
            lx = x + 12
            if lx + draw.textlength(label, font=name) > box[2] - 6:
                lx = x - 12 - draw.textlength(label, font=name)
            draw.text((lx, y - 12), label, font=name, fill=INK)
    _footer(draw, height, words, words["count"] % len(stations))
    return _png(image)


if __name__ == "__main__":                              # 手边看一眼：python3 oa_image.py 存两张样图
    import sys
    demo = {"date": "2026-10-17", "call": "BG7LZQ-3", "stations": [
        {"call": "BG7LZQ-2", "grid": "OL62", "via": "is", "digi": [], "at": "10-17 01:08",
         "status": "confirmed"},
        {"call": "BG6TA", "grid": "ON80", "via": "rf", "digi": ["WIDE1-1", "BG7LZQ-10"],
         "at": "10-17 07:01", "status": "acked"},
        {"call": "BH4HGA-9", "grid": "", "via": "is", "digi": [], "at": "10-17 09:26",
         "status": "called"},
    ]}
    for name, fn in (("board", render_board), ("points", render_points)):
        body = fn(demo, note="测试轮次")
        with open("/root/.nanobot/workspace/.scratch/oa_%s.png" % name, "wb") as fh:
            fh.write(body)
        sys.stderr.write("%s %d 字节\n" % (name, len(body)))
