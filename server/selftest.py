#!/usr/bin/env python3
"""不连网的自检：把几行报文喂进去，看解析与名单对不对。

  python3 selftest.py

会话结束后临时目录会自己删掉，不碰真实数据。
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oa_aprs
import oa_server
import oa_store

UTC = timezone.utc

LINES = [
    # 走射频、过了中继，报文里自己报了网格
    "BG7XXX-9>APRS,BG7LZQ-1*,WIDE2-1,qAR,BG7YAB-3:!2233.33N/11400.00E> CQ CNNET OL62",
    # 另一台，直接上网发的（qAC 那条路径）
    "BH4AAA>APRS,TCPIP*,qAC,T2CHILE:!3113.00N/12122.00E> CQ CNNET PM01",
    # 刚才那台回过 U 了
    "BG7XXX-9>APRS,TCPIP*,qAC,T2CHILE::BG7LZQ-2 :U CNNET",
    # 压缩位置
    "BG6BBB-7>APRS,BG7LZQ-1*,qAR,BG7YAB-3:!/5L!!<*e7>7P CQ CNNET",
    # 主控自己的 ACK，不该算台站
    "BG7LZQ-2>APRS,TCPIP*,qAC,T2CHILE::BG7XXX-9 :ACK",
    # 服务器横幅与闲聊报文，都要被跳过或只记 other
    "# aprsc 2.1.11-g5db4e96",
    "BG5CCC>APRS,TCPIP*,qAC,T2X:>今天天气不错",
    # 主控自己那句待命话里有「CQ CNNET」，那是说明不是喊到
    "BG7LZQ-2>APALOC,TCPIP*,qAC,T2YANTAI:>APRSNETCN OA 主控待命 收 CQ CNNET 即回 ACK",
    # 主控自己喊到也不算参与台
    "BG7LZQ-2>APRS,TCPIP*,qAC,T2TAIWAN:!2233.33N/11400.00E> CQ CNNET 试一下",
    # 签到那行没写出网格：不进名单，标成 incomplete（服务器据此回一条无效提示）
    "BG8ZZZ>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET 试一下",
]

CHECKS = []

# 路径识别的几个硬骨头：(整行, 该算射频还是网络, 该记下的中继)
PATHS = [
    # 射频：经网关转上来（qAR / qAr），中继台呼号进中继
    ("BG7XXX-9>APRS,BG7LZQ-1*,WIDE2-1,qAR,BG7YAB-3:!2233.33N/11400.00E>", "rf", ["BG7LZQ-1"]),
    # 只有别名，没经过台站：算射频，但不记中继
    ("N0CALL>APRS,WIDE1-1,WIDE2-1:!2233.33N/11400.00E>", "rf", []),
    # 收发网关（qAO）从射频转上来的：算射频
    ("BG7OXS-3>APALOC,qAO,BG7OXS-10::PD3RON   :ack9383", "rf", []),
    # 网关自己把自己的射频信标转上来，也叫 qAR：算射频
    ("BI2SC-10>APDW18,qAR,BI2SC-10:!3903.72NT12150.78E&", "rf", []),
    # 网口直上：TCPIP* + qAC 算网络
    ("BH4AAA>APRS,TCPIP*,qAC,T2CHILE:!3113.00N/12122.00E>", "is", []),
    # 发送口直上：qAO 后面跟着本台呼号，算网络
    ("BM2MIJ-10>APDW14,qAO,BM2MIJ-10:!2500.79N/12128.07Er", "is", []),
    # 服务器不带转递的 qAO / qAS / qAU：算网络
    ("BA7OFL-12>APZL10,TCPIP*,qAO,AE5PL-JF:>162155z Unattended.", "is", []),
    ("BG7LZQ-2>APRS,qAS,BG7LZQ-3::BG7LZQ-2 :ACK", "is", []),
    # 射频上发的又经网口上传（TCPIP* 与 WIDE 同现）：算射频
    ("BH2WAV-1>APRS2,WIDE1-1,TCPIP*,qAC,BH2WAV-1:!3907.27N/11704.45Ey", "rf", []),
    # 模式标记（DMR 之类）说明走过空中，不是中继台呼号，不记进中继
    ("KB3KAV>APRS,KB3KAV,DMR*,qAR,KB3KAV:=2907.06N/11943.75E", "rf", ["KB3KAV"]),
]


def check(name, got, want):
    ok = got == want
    CHECKS.append(ok)
    print("%s %s：%s" % ("ok  " if ok else "FAIL", name, got))
    if not ok:
        print("      想要：%s" % (want,))


def red_pixels(body, box):
    """一张 PNG 里某块区域上接近那一点红（#b3141f）的像素数。"""
    import io as _io
    from PIL import Image
    img = Image.open(_io.BytesIO(body)).convert("RGB")
    n = 0
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            r, g, b = img.getpixel((x, y))
            if r > 140 and g < 90 and b < 90:
                n += 1
    return n


def main():
    work = tempfile.mkdtemp(prefix="oa-selftest-")
    try:
        store = oa_store.Store(work, default_duty="BG7LZQ-2")
        records = []
        results = []
        for line in LINES:
            record = oa_aprs.parse_line(line)
            if record:
                records.append(record)
                results.append(store.add_packet(record))

        check("横幅被跳过", len(records), len(LINES) - 1)

        first = records[0]
        check("呼号", first["src"], "BG7XXX-9")
        check("喊到认出来", first["kind"], "cq")
        check("走射频", first["via"], "rf")
        check("中继只留真呼号", first["digi"], ["BG7LZQ-1"])
        check("经纬度", (first["lat"], first["lon"]), (22.5555, 114.0))
        check("位置推出网格", oa_aprs.grid_of(first["lat"], first["lon"]), "OL72")

        second = records[1]
        check("上网发的算网络", second["via"], "is")
        check("位置推出网格", second["grid"], "PM01")

        msg = records[2]
        check("报文认得出收件人", msg["msgto"], "BG7LZQ-2")
        check("U 是确认", msg["kind"], "u")

        comp = records[3]
        check("压缩位置有坐标", comp["lat"] is not None and comp["lon"] is not None, True)
        check("压缩位置后面的文字没被切坏", comp["kind"], "cq")
        check("压缩位置的网格", comp["grid"], "FN39")

        ack = records[4]
        check("ACK 是 ack", ack["kind"], "ack")

        chat = records[5]
        check("闲聊报文归 other", chat["kind"], "other")

        duty_beat = records[6]
        check("主控待命那句不算喊到", duty_beat["kind"], "other")
        check("主控自己喊到认得出", records[7]["kind"], "cq")

        # 页面写着「也可以不发 CQ，直接发 CNNET 你的信息」
        silent = oa_aprs.parse_line("BG8ZZZ>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CNNET BG7XXX OL62")
        check("不发 CQ 也认成喊到", silent["kind"], "cq")
        check("不发 CQ 也读出网格", silent["msg_grid"], "OL62")

        roster = store.roster()
        check("只有一轮", len(roster["rounds"]), 1)
        rnd = roster["rounds"][0]
        check("轮次日期是周六", oa_store.round_date_of(records[0]["ts"]) == rnd["date"], True)
        check("轮值呼号写进名单", rnd["call"], "BG7LZQ-2")
        calls = sorted([s["call"] for s in rnd["stations"]])
        check("名单里三台", calls, ["BG6BBB-7", "BG7XXX-9", "BH4AAA"])
        check("主控自己不在名单里", store.stations(rnd["date"]) and
              any(s["call"] == "BG7LZQ-2" for s in store.stations(rnd["date"])), False)
        by_call = dict((s["call"], s) for s in rnd["stations"])
        live = dict((s["call"], s) for s in store.stations(rnd["date"]))
        check("BG7XXX-9 回过 U 就是已确认", live["BG7XXX-9"]["status"], "confirmed")
        check("主控自己的 ACK 不进名单", "BG7LZQ-2" in live, False)
        check("参与时间写法", by_call["BG7XXX-9"]["at"][:2], rnd["date"][5:7])
        check("BH4AAA 走网络", by_call["BH4AAA"]["via"], "is")
        check("名单更新日期", rnd["updated"], rnd["date"])
        check("名单里带状态", by_call["BG7XXX-9"].get("status"), "confirmed")
        check("名单里带报文数", by_call["BG7XXX-9"].get("packets"), 2)
        check("名单里带首次与最后一次时间",
              bool(by_call["BG7XXX-9"].get("first")) and bool(by_call["BG7XXX-9"].get("last")), True)
        utc_first = by_call["BG7XXX-9"].get("firstUtc") or ""
        check("名单里另给一份 UTC 时间",
              bool(utc_first) and utc_first != by_call["BG7XXX-9"].get("first"), True)
        check("参与时间与第一次一致时也给 UTC",
              bool(by_call["BG7XXX-9"].get("atUtc")), True)
        check("只报呼号与网格时没有自己那句话", by_call["BG7XXX-9"].get("msg"), "")
        check("签到后面那句自己的话", oa_store.cnnet_note("CQ CNNET BG6TA from ON80 周末在山上"),
              "周末在山上")
        check("只写呼号与网格就没有自己的话",
              oa_store.cnnet_note("CQ CNNET BG6TA from ON80", "BG6TA"), "")
        check("U 那条后面的话也算", oa_store.cnnet_note("U CNNET 73", "BG6TA"), "73")
        check("不写 from 也认", oa_store.cnnet_note("CNNET BG6TA ON80 信号好", "BG6TA"), "信号好")
        check("没有前缀的整句留着",
              oa_store.cnnet_note("CQ CNNET 我是 BG6TA，周末在山上", "BG6TA"), "我是 BG6TA，周末在山上")
        check("网格从报文里读", by_call["BG7XXX-9"]["grid"], "OL62")
        check("信息里没写网格的不进名单", "BG8ZZZ" in live, False)
        check("没写网格的标成 incomplete", results[-1].get("incomplete"), True)

        out = os.path.join(work, "roster.json")
        check("第一次写盘有变化", store.write_roster(out), True)
        check("第二次写盘零变化", store.write_roster(out), False)
        with open(out, "r", encoding="utf-8") as fh:
            disk = json.load(fh)
        check("写出来的是同一份", disk, roster)

        check("手动加一台", store.set_station(rnd["date"], "BG9ZZZ", grid="OL51", via="rf",
                                            digi=["BG7LZQ-1"], status="confirmed") is not None, True)
        check("删掉它", store.drop_station(rnd["date"], "BG9ZZZ"), True)
        check("删完又只剩三台", len(store.stations(rnd["date"])), 3)
        check("只改不建的写不动新呼号", store.set_station(rnd["date"], "BG0NOPE", status="acked",
                                                      create=False), None)
        check("那台没被建出来", len(store.stations(rnd["date"])), 3)

        stats = store.round_stats(rnd["date"])
        check("数字：三台", stats["stations"], 3)
        check("数字：已确认一台", stats["confirmed"], 1)
        check("数字：只喊到两台", stats["called"], 2)
        check("数字：射频两台", stats["rf"], 2)
        check("数字：网络一台", stats["is"], 1)
        check("数字：一个中继", stats["digis"], 1)
        check("数字：只数喊到与确认那几句", stats["packets"], 6)
        check("数字：最后一条有时间", bool(stats["last"]), True)
        check("没开过的轮数字全是零", store.round_stats("1999-01-02")["stations"], 0)

        arch_dir = os.path.join(work, "archive")
        files = store.archive_round(rnd["date"], arch_dir)
        check("归档写出三份", sorted(os.path.basename(f) for f in files),
              ["round-%s-packets.csv" % rnd["date"], "round-%s-stations.csv" % rnd["date"],
               "round-%s.json" % rnd["date"]])
        with open(os.path.join(arch_dir, "round-%s.json" % rnd["date"]), "r", encoding="utf-8") as fh:
            arch = json.load(fh)
        check("归档里有名单与流水", (len(arch["stations"]), len(arch["packets"])), (3, 6))
        check("归档里的数字", arch["stats"]["confirmed"], 1)
        check("归档写了收轮时间", bool(arch["closed"]), True)
        with open(os.path.join(arch_dir, "round-%s-stations.csv" % rnd["date"]), "rb") as fh:
            check("CSV 带 BOM", fh.read(3), b"\xef\xbb\xbf")
        check("没这一轮就归档不出东西", store.archive_round("1999-01-02", arch_dir), [])

        # 路径怎么算射频、怎么算网络：网关从射频转上来、网口直上、别名与模式标记各一套
        for line, want, want_digi in PATHS:
            rec = oa_aprs.parse_line(line)
            check("路径：%s" % line.split(">", 1)[1].split(":", 1)[0], (rec["via"], rec["digi"]),
                  (want, want_digi))

        store.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 名单里的路径按喊到那一下算：那一台后面换了链路再发，不改这一行
    via_dir = tempfile.mkdtemp(prefix="oa-selftest-via-")
    try:
        via_store = oa_store.Store(via_dir, default_duty="BG7LZQ-2")
        day = oa_store.round_date_of(oa_aprs.now_iso())
        via_store.open_round(day, "BG7LZQ-2")
        for line in ("BH5DDD>APRS,TCPIP*,qAC,T2X:!2233.33N/11400.00E> CQ CNNET OL63",
                     "BH5DDD>APRS,WIDE1-1,qAR,BG7YAB-3::BG7LZQ-2 :U CNNET",
                     "BH6EEE>APRS,BG7LZQ-1*,qAR,BG7YAB-3:!2233.33N/11400.00E> CQ CNNET OL64",
                     "BH6EEE>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :U CNNET"):
            via_store.add_packet(oa_aprs.parse_line(line))
        via_rows = dict((r["call"], r) for r in via_store.stations(day))
        check("网上喊到的，后面走射频也不改成射频", via_rows["BH5DDD"]["via"], "is")
        check("喊到时过的中继留着", via_rows["BH6EEE"]["digi"], "BG7LZQ-1")
        check("喊到走射频的，后面走网络也留着射频", via_rows["BH6EEE"]["via"], "rf")
        check("两台都算确认了", sorted(r["status"] for r in via_store.stations(day)),
              ["confirmed", "confirmed"])
        via_store.close()
    finally:
        shutil.rmtree(via_dir, ignore_errors=True)

    # 老库补列：建库时还没有 acked_ts / confirmed_ts，打开时应自己补上
    old = tempfile.mkdtemp(prefix="oa-selftest-old-")
    try:
        conn = sqlite3.connect(os.path.join(old, "oa.db"))
        conn.executescript(
            "CREATE TABLE stations (id INTEGER PRIMARY KEY, round_id INTEGER, call TEXT,"
            " grid TEXT, via TEXT, digi TEXT, at TEXT, status TEXT, packets INTEGER,"
            " first_ts TEXT, last_ts TEXT);")
        conn.execute("INSERT INTO stations (round_id,call,status) VALUES (1,'BG1OLD','called')")
        conn.commit()
        conn.close()
        legacy = oa_store.Store(old, default_duty="BG7LZQ-2")
        cols = set(r["name"] for r in legacy.db.execute("PRAGMA table_info(stations)"))
        check("老库补上 acked_ts", "acked_ts" in cols, True)
        check("老库补上 confirmed_ts", "confirmed_ts" in cols, True)
        check("老库那一行还在",
              legacy.db.execute("SELECT COUNT(*) AS n FROM stations").fetchone()["n"], 1)
        legacy.close()
        again = oa_store.Store(old, default_duty="BG7LZQ-2")
        check("补列再来一次不多不少",
              sorted(r["name"] for r in again.db.execute("PRAGMA table_info(stations)")), sorted(cols))
        again.close()
    finally:
        shutil.rmtree(old, ignore_errors=True)

    # 全自动模式：开关写进库里、重启还认得；补回的 ACK 隔一分钟一次、一台一轮有上限
    auto_dir = tempfile.mkdtemp(prefix="oa-selftest-auto-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = auto_dir
        conf["OA_TX_PASSCODE"] = "23736"
        rt = oa_server.Runtime(conf)
        check("默认就是全自动", rt.auto_mode, True)
        check("全自动时自动回 ACK 跟着开", rt.auto_ack_on, True)
        check("口令在，发得出去", rt.can_send(), True)
        check("关掉全自动", rt.set_auto_mode(False), False)
        check("关了就不自动回 ACK", rt.auto_ack_on, False)
        check("关了也不自动开轮", rt.auto_round_on, False)
        check("开关写进了库", rt.store.get_setting("auto"), "0")
        rt.store.close()

        again = oa_server.Runtime(conf)
        check("重启还记得关着", again.auto_mode, False)
        again.store.open_round("2026-10-17", "BG7LZQ-2")
        # 这一轮已经开了两个钟头：补回的窗口比这一轮的开始晚，又不在一分钟内
        again.store.db.execute(
            "UPDATE rounds SET opened=? WHERE date=?",
            ((datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-10-17"))
        again.store.db.commit()
        again.store.set_station("2026-10-17", "BG1AAA", status="called")
        again.store.set_station("2026-10-17", "BG2BBB", status="confirmed")
        check("关着的时候不补", again.retry_pending_acks(), 0)
        again.set_auto_mode(True)
        check("打开了", again.auto_mode, True)
        check("只补没回上 ACK 的那台", again.retry_pending_acks(), 1)
        check("补回那条的写法", again.store.outbox_for("BG1AAA")[0]["line"],
              "BG7LZQ-2>APRS::BG1AAA   :ACK")
        check("排着队的不重复入队", again.retry_pending_acks(), 0)

        def fail_last():
            rows = again.store.outbox_for("BG1AAA")
            again.store.mark_sent(rows[-1]["id"], "发不出去")

        def backdate():
            old = (datetime.now(UTC) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
            again.store.db.execute("UPDATE outbox SET created=?", (old,))
            again.store.db.commit()

        fail_last()
        check("刚失败的一分钟内不补", again.retry_pending_acks(), 0)
        backdate()
        check("过一会儿补第二次", again.retry_pending_acks(), 1)
        fail_last()
        backdate()
        check("补第三次", again.retry_pending_acks(), 1)
        fail_last()
        backdate()
        check("补到上限就停", again.retry_pending_acks(), 0)
        check("补不上的那台还在待办里",
              again.store.get_station("2026-10-17", "BG1AAA")["status"], "called")
        again.store.close()
    finally:
        shutil.rmtree(auto_dir, ignore_errors=True)

    # 网格从签到那行报的话里读；没写网格的回一条无效提示，不进名单
    bad_dir = tempfile.mkdtemp(prefix="oa-selftest-bad-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = bad_dir
        conf["OA_TX_PASSCODE"] = "23736"
        rt = oa_server.Runtime(conf)
        today = oa_store.round_date_of(oa_aprs.now_iso())
        rt.store.open_round(today, "BG7LZQ-2")
        rt.on_line(oa_aprs.parse_line(
            "BG8ZZZ-1>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET 忘了写网格"))
        rows = rt.store.outbox_for("BG8ZZZ-1")
        check("没写网格的回了无效提示", bool(rows) and rows[0]["line"].endswith(
            ":INVALID CHECKIN, RESEND: CQ CNNET <info> <GRID>"), True)
        check("没写网格的不进名单", rt.store.get_station(today, "BG8ZZZ-1"), None)
        rt.on_line(oa_aprs.parse_line(
            "BG8ZZZ-1>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET 又忘了"))
        check("同一台同一轮只回一条", len(rt.store.outbox_for("BG8ZZZ-1")), 1)
        rt.on_line(oa_aprs.parse_line(
            "BG9YYY-2>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET from PM01"))
        check("写了网格的进名单", bool(rt.store.get_station(today, "BG9YYY-2")), True)
        check("名单里的网格取自报文",
              rt.store.get_station(today, "BG9YYY-2")["grid"], "PM01")
        check("写了网格的回的是 ACK",
              rt.store.outbox_for("BG9YYY-2")[0]["line"].endswith(":ACK"), True)
        rt.on_line(oa_aprs.parse_line(
            "BG9YYY-2>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :U CNNET"))
        check("回过 U 就是已确认（没再写网格也算）",
              rt.store.get_station(today, "BG9YYY-2")["status"], "confirmed")
        # 发给本台但不是签到那两步：回一条怎么做；不是发给本台的不回
        rt.on_line(oa_aprs.parse_line(
            "BG7AAA-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :你好，在吗"))
        rows = rt.store.outbox_for("BG7AAA-9")
        check("发给本台但不对的回了怎么做",
              rows[0]["line"].startswith("BG7LZQ-2>APRS::BG7AAA-9 :"), True)
        check("那句就是怎么做", bool(rows) and rows[0]["line"].endswith(
            ":APRSNETCN OA: CQ CNNET <INFO> <GRID>, THEN U CNNET"), True)
        check("不对的报文不进名单", rt.store.get_station(today, "BG7AAA-9"), None)
        rt.on_line(oa_aprs.parse_line(
            "BG7AAA-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :还是我"))
        check("同一台同一轮同一句只回一条", len(rt.store.outbox_for("BG7AAA-9")), 1)
        rt.on_line(oa_aprs.parse_line(
            "BG7AAA-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET OL62"))
        check("改成对的就回 ACK",
              rt.store.outbox_for("BG7AAA-9")[-1]["line"].endswith(":ACK"), True)
        rt.on_line(oa_aprs.parse_line(
            "BG6BBB-5>APRS,TCPIP*,qAC,T2X::BD9ZZZ   :你好"))
        check("发给别人的不回", rt.store.outbox_for("BG6BBB-5"), [])
        rt.on_line(oa_aprs.parse_line(
            "BG6BBB-5>APRS,TCPIP*,qAC,T2X:>一句广播"))
        check("没有收件人的广播不回", rt.store.outbox_for("BG6BBB-5"), [])
        rt.on_line(oa_aprs.parse_line(
            "BG6BBB-5>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :ack9383"))
        check("机器回的 ACK 不回", rt.store.outbox_for("BG6BBB-5"), [])
        rt.store.close()
    finally:
        shutil.rmtree(bad_dir, ignore_errors=True)

    # 收报文的过滤串跟着当轮主控呼号走：换了一轮换了呼号，发给新呼号的报文才收得到
    filt_dir = tempfile.mkdtemp(prefix="oa-selftest-filter-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = filt_dir
        conf["OA_APRS_FILTER"] = "a/54/73/18/135 g/BG7LZQ-2"
        conf["OA_ROUND_DUTY"] = "BG7LZQ-2"
        rt = oa_server.Runtime(conf)
        check("没开轮时按配置的呼号收", rt.duty_filter(), "a/54/73/18/135 g/BG7LZQ-2")
        rt.store.open_round("2026-10-17", "BG7LZQ-3")
        check("开了轮就换成当轮呼号", rt.duty_filter(), "a/54/73/18/135 g/BG7LZQ-3")
        check("过滤串里只有一个 g/", rt.duty_filter().count("g/"), 1)
        rt.conf["OA_APRS_FILTER"] = "a/54/73/18/135"
        check("配置里没写 g/ 就补上", rt.duty_filter(), "a/54/73/18/135 g/BG7LZQ-3")
        check("没连链路时跟呼号不报错", rt.follow_duty(), None)
        rt.store.close()
    finally:
        shutil.rmtree(filt_dir, ignore_errors=True)

    # 签到表 / 签到点那张图，以及数据面板上的两个下载按钮
    try:
        import oa_image
    except ImportError:
        oa_image = None
        print("没装 Pillow，跳过网格换算那几项")
    if oa_image is not None:
        grouped = oa_image.grid_groups({"stations": [
            {"call": "BG6TA", "grid": "ON80"},
            {"call": "BG7LZQ-2", "grid": "ol62"},
            {"call": "BA4IZZ", "grid": ""},
            {"call": "BG7LZQ-3", "grid": "OL62"},
            {"call": "BH4HGA-9"},
        ]})
        check("网格表按网格归拢，呼号按名单顺序", grouped, [
            ("OL62", ["BG7LZQ-2", "BG7LZQ-3"]), ("ON80", ["BG6TA"]),
            ("\u2014", ["BA4IZZ", "BH4HGA-9"])])
        check("网格只算前 4 位，后两位并进同一格", oa_image.grid_cell("ol62cQ"), "OL62")
        check("认不出的网格原样留着", oa_image.grid_cell("  x "), "X")
        check("按 field 分成一张张格网",
              oa_image.grid_fields(grouped), [
                  ("OL", {(6, 2): ["BG7LZQ-2", "BG7LZQ-3"]}),
                  ("ON", {(8, 0): ["BG6TA"]})])
        grid = oa_image.render_grid({"date": "2026-10-17", "call": "BG7LZQ-3", "stations": [
            {"call": "BG7LZQ-2", "grid": "OL62"}, {"call": "BG6TA", "grid": "ON80"}]})
        check("网格表画得出 PNG", grid[:8], b"\x89PNG\r\n\x1a\n")
        cert = oa_image.render_cert({"date": "2026-10-17", "call": "BG7LZQ-3"},
                                    {"call": "BG7LZQ-2", "grid": "OL62AA", "via": "is",
                                     "digi": [], "at": "10-17 01:08"})
        check("奖状画得出 PNG", cert[:8], b"\x89PNG\r\n\x1a\n")
        check("奖状编号同台同轮不变", oa_image.serial_of("BG7LZQ-2", "2026-10-17"),
              oa_image.serial_of("BG7LZQ-2", "2026-10-17"))
        check("奖状编号换了轮次就变",
              oa_image.serial_of("BG7LZQ-2", "2026-10-18") !=
              oa_image.serial_of("BG7LZQ-2", "2026-10-17"), True)

    # 名单里找一台：整串优先，去掉 SSID 只有唯一一台时才认
    two = {"stations": [{"call": "BG7LZQ-2"}, {"call": "BG7LZQ-3"}]}
    check("整串找得到那一台", oa_server.pick_station(two, "bg7lzq-3"), {"call": "BG7LZQ-3"})
    check("同一个基呼号有两台就不猜", oa_server.pick_station(two, "BG7LZQ"), None)
    check("只有一台时去掉 SSID 也认",
          oa_server.pick_station({"stations": [{"call": "BG7LZQ-2"}]}, "BG7LZQ"),
          {"call": "BG7LZQ-2"})
    check("名单里没这台给 None", oa_server.pick_station(two, "N0CALL"), None)

    img_dir = tempfile.mkdtemp(prefix="oa-selftest-image-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = img_dir
        conf["OA_SITE_ROOT"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rt = oa_server.Runtime(conf)
        rt.store.add_packet(oa_aprs.parse_line(LINES[0]))
        rounds = rt.store.roster()["rounds"]
        check("名单里有一轮", len(rounds), 1)
        check("名单里有一台", len(rounds[0]["stations"]) if rounds else 0, 1)
        check("默认没有测试字样", conf.get("OA_TEST_NOTE"), "")
        check("默认也没有英文测试字样", conf.get("OA_TEST_NOTE_EN"), "")
        httpd = oa_server.ThreadingHTTPServer(("127.0.0.1", 0), oa_server.Handler)
        httpd.runtime = rt
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        try:
            for what in ("board", "grid"):
                try:
                    res = urllib.request.urlopen("%s/api/%s.png" % (base, what), timeout=10)
                    body = res.read()
                    disp = res.headers.get("Content-Disposition") or ""
                    check("%s.png 回 200" % what, res.status, 200)
                    check("%s.png 是 PNG" % what, body[:8], b"\x89PNG\r\n\x1a\n")
                    check("%s.png 的文件名带轮次" % what,
                          "aprsnetcn-oa-" in disp and "-%s.png" % what in disp, True)
                except Exception as exc:
                    check("%s.png 拿得到" % what, "出错：%s" % exc, "拿得到")
            cert = urllib.request.urlopen("%s/api/cert.png?call=BG7XXX-9" % base, timeout=10)
            check("奖状回 200", cert.status, 200)
            check("奖状是 PNG", cert.read()[:8], b"\x89PNG\r\n\x1a\n")
            check("奖状文件名带呼号与轮次",
                  "aprsnetcn-oa-BG7XXX-9-" in (cert.headers.get("Content-Disposition") or ""), True)
            for query, want in (("?call=N0CALL", 404), ("?call=BG6BBB-7", 404), ("", 400)):
                try:
                    res = urllib.request.urlopen("%s/api/cert.png%s" % (base, query), timeout=10)
                    code = res.status
                except urllib.error.HTTPError as exc:
                    code = exc.code
                check("奖状 %s 回 %d" % (query or "（没带呼号）", want), code, want)
            loose = urllib.request.urlopen("%s/api/cert.png?call=BG7XXX" % base, timeout=10)
            check("只写呼号也认（去掉 SSID）", loose.status, 200)
            check("去掉 SSID 找到的是名单里的那台",
                  "aprsnetcn-oa-BG7XXX-9-" in (loose.headers.get("Content-Disposition") or ""), True)
            page = urllib.request.urlopen("%s/data" % base, timeout=10).read().decode("utf-8")
            check("页面里名单指本机", 'data-roster="/api/roster"' in page, True)
            check("页面里下载按钮指本机", 'data-image-base="/api/"' in page, True)
            check("页面里有网格表的位子", 'id="oaGrid"' in page, True)
            # 脚本版本号从磁盘上那份页面里取，升级 ?v= 时这里不用跟着改
            src = open(os.path.join(conf["OA_SITE_ROOT"], "oa/data.html"), encoding="utf-8").read()
            ver = re.search(r'roster\.js\?v=(\d+)', src)
            check("页面里脚本走绝对路径", 'src="/oa/roster.js?v=%s"' % ver.group(1) in page, True)
        finally:
            httpd.shutdown()
            httpd.server_close()
        rt.store.close()
    finally:
        shutil.rmtree(img_dir, ignore_errors=True)

    print("\n%d 项，过了 %d 项" % (len(CHECKS), sum(1 for c in CHECKS if c)))
    return 0 if all(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
