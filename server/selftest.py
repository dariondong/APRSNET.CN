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
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oa_aprs
import oa_region
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


def said(line):
    """一行报文里那句话：去掉呼号列与末尾的消息编号，只比文字。"""
    return oa_server.line_text(line)


def acks(rt, call):
    """回给这一台的确认那几行；跟的那句人话不算。"""
    return [r for r in rt.store.outbox_for(call) if said(r["line"]).lower().startswith("ack")]


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
        # 下面这些报文都是签到：先开当轮，签到才并得进去。没开轮时只留报文，
        # 不新开一轮，名单上一个字都不动。
        store.open_round(oa_store.round_date_of(oa_aprs.now_iso()), "BG7LZQ-2")
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
        check("没填那几行字时元数据都是空的", roster["meta"],
              {"code": "", "nameZh": "", "nameEn": "",
               "subtitleZh": "", "subtitleEn": "",
               "explainZh": "", "explainEn": "", "opName": ""})
        check("编号只收数字字母", store.set_setting("activity_code", " #26-10 17ab "), "261017ab")
        store.set_setting("activity_name_zh", " 测试轮次 ")
        store.set_setting("activity_name_en", "Test round")
        store.set_setting("explain_zh", "北京时间周六整天")
        store.set_setting("explain_en", "All day Saturday, Beijing time")
        store.set_setting("op_name", "达里安")
        meta = store.roster()["meta"]
        check("副标题由编号与名称拼出来", (meta["subtitleZh"], meta["subtitleEn"]),
              ("#261017ab 测试轮次", "#261017ab Test round"))
        check("名单顶上带着编号", store.roster()["code"], "261017ab")
        check("名称、解释标题与昵称跟着名单发出去",
              (meta["nameZh"], meta["explainZh"], meta["opName"]),
              ("测试轮次", "北京时间周六整天", "达里安"))
        check("旧副标题拆成编号与名称", oa_store.split_headline("#261017 测试轮次"),
              ("261017", "测试轮次"))
        check("没编号就把整行当名称", oa_store.split_headline("测试轮次"), ("", "测试轮次"))
        legacy_dir = os.path.join(tempfile.mkdtemp(), "legacy")
        old = oa_store.Store(legacy_dir)
        old._put_setting("subtitle_zh", "#261017 测试轮次")
        old._put_setting("subtitle_en", "Test round")
        old.db.commit()
        old.close()
        reopened = oa_store.Store(legacy_dir)
        check("老库那行副标题拆完编号还在", reopened.meta()["code"], "261017")
        check("老库拆完副标题照旧", reopened.meta()["subtitleZh"], "#261017 测试轮次")
        check("老库英文名称也拆下来了", reopened.meta()["nameEn"], "Test round")
        reopened.close()
        # 每一轮记下开的时候的编号：换了编号，老那轮还认得自己属于哪一场
        code_dir = os.path.join(tempfile.mkdtemp(), "codes")
        coded = oa_store.Store(code_dir)
        coded.set_setting("activity_code", "261017")
        coded.ensure_round("2026-10-17", "BG0TST")
        coded.set_setting("activity_code", "261024")
        coded.ensure_round("2026-10-24", "BG0TST")
        check("新开的一轮记下当时的编号", coded.round_by_date("2026-10-24")["code"], "261024")
        check("换了编号也不改写老那轮", coded.round_by_date("2026-10-17")["code"], "261017")
        coded.close()
        # 页面文字每轮一套：写哪一轮就是哪一轮，另一轮不动
        tdir = os.path.join(tempfile.mkdtemp(), "texts")
        texts = oa_store.Store(tdir)
        texts.ensure_round("2026-10-17", "BG0TST",
                           text={"activity_code": "261017", "activity_name_zh": "第一场",
                                 "explain_zh": "第一次"})
        texts.ensure_round("2026-10-24", "BG0TST",
                           text={"activity_code": "261024", "activity_name_zh": "第二场",
                                 "explain_en": "Second"})
        check("这一轮写的是自己那套", texts.round_text("2026-10-24")["subtitleZh"], "#261024 第二场")
        check("上一轮还是上一轮的", texts.round_text("2026-10-17")["subtitleZh"], "#261017 第一场")
        check("英文名空着沿用中文", texts.round_text("2026-10-24")["nameEn"], "第二场")
        texts.set_round_text("2026-10-24", {"explain_zh": "第二次"})
        check("写解释不碰名称", texts.round_text("2026-10-24")["subtitleZh"], "#261024 第二场")
        check("写解释只改这一轮", texts.round_text("2026-10-17")["explainZh"], "第一次")
        check("写解释的这一轮换了", texts.round_text("2026-10-24")["explainZh"], "第二次")
        hist = [r for r in texts.meta_history(10) if r["key"] == "explain_zh"]
        check("改页面文字记下哪一轮", hist[0]["round_date"], "2026-10-24")
        two = texts.roster()
        check("名单每轮带自己那份字", [x["meta"]["subtitleZh"] for x in two["rounds"]],
              ["#261024 第二场", "#261017 第一场"])
        check("名单顶上那套是最新一轮", two["meta"]["subtitleZh"], "#261024 第二场")
        check("没这一轮就写不进去", texts.set_round_text("2026-11-01", {"op_name": "谁"}), None)
        texts.close()
        store.set_setting("op_name", "")
        check("抹掉就是不显示", store.meta()["opName"], "")
        # 改过的每一行都记一条，新的在前；抹成空、动开关都不记
        store.set_setting("explain_zh", "周六在空中")
        log = store.meta_history(50)
        check("改一行记一条", len(log), 7)
        check("新的在前", log[0]["value"], "周六在空中")
        check("换成新的一句后旧那句还在",
              [r["value"] for r in log if r["key"] == "explain_zh"],
              ["周六在空中", "北京时间周六整天"])
        store.set_setting("explain_zh", "")
        store.set_setting("auto", "1")
        check("抹成空与开关不记", len(store.meta_history(50)), 7)
        check("解释标题抹掉就是空串", store.meta()["explainZh"], "")
        # 后面还要比「写出来的名单与内存里的是同一份」，先把几件改回空的
        store.set_setting("activity_code", "")
        store.set_setting("activity_name_zh", "")
        store.set_setting("activity_name_en", "")
        store.set_setting("explain_en", "")
        check("几件都抹掉后元数据回到空", store.meta(),
              {"code": "", "nameZh": "", "nameEn": "",
               "subtitleZh": "", "subtitleEn": "",
               "explainZh": "", "explainEn": "", "opName": ""})
        rnd = roster["rounds"][0]
        check("轮次日期是周六", oa_store.round_date_of(records[0]["ts"]) == rnd["date"], True)
        check("轮值呼号写进名单", rnd["call"], "BG7LZQ-2")
        calls = sorted([s["call"] for s in rnd["stations"]])
        check("名单里三台", calls, ["BG6BBB-7", "BG7XXX-9", "BH4AAA"])
        # 主控台那张轮次表直接用这两个数：台站数与签到条数
        row = [r for r in store.rounds() if r["date"] == rnd["date"]][0]
        check("轮次那一行带台站数", row["stations"], 3)
        check("轮次那一行带签到数", row["checkins"],
              len([p for p in store.packets(limit=50) if p["kind"] in ("cq", "u")]))
        check("主控自己不在名单里", store.stations(rnd["date"]) and
              any(s["call"] == "BG7LZQ-2" for s in store.stations(rnd["date"])), False)
        by_call = dict((s["call"], s) for s in rnd["stations"])
        live = dict((s["call"], s) for s in store.stations(rnd["date"]))
        check("BG7XXX-9 回过 U 就是已确认", live["BG7XXX-9"]["status"], "confirmed")
        check("主控自己的 ACK 不进名单", "BG7LZQ-2" in live, False)
        check("参与时间写法", by_call["BG7XXX-9"]["at"][:2], rnd["date"][5:7])
        check("BH4AAA 走网络", by_call["BH4AAA"]["via"], "is")
        check("名单更新日期", rnd["updated"],
              oa_store.bj_date(max(r["ts"] for r in records if r["kind"] in ("cq", "u"))))
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

        # 删一条报文：名单跟着重算；一条不剩就把这一台从名单上撤掉
        u_line = [p for p in store.packets(limit=50)
                  if p["src"] == "BG7XXX-9" and p["kind"] == "u"][0]
        check("删掉一条，返回是哪一台", store.delete_packet(u_line["id"])["call"], "BG7XXX-9")
        live = dict((s["call"], s) for s in store.stations(rnd["date"]))
        check("删掉那条确认，这一台退回只喊到", live["BG7XXX-9"]["status"], "called")
        check("删掉一条报文，这一台的条数跟着少", live["BG7XXX-9"]["packets"], 1)
        check("删掉那条确认的报文不见了",
              any(p["id"] == u_line["id"] for p in store.packets(limit=50)), False)
        check("删不存在的报文不算数", store.delete_packet(999999), None)
        for p in [p for p in store.packets(limit=50) if p["src"] == "BH4AAA"]:
            store.delete_packet(p["id"])
        check("删光了这一台就从名单上撤掉",
              "BH4AAA" in [s["call"] for s in store.stations(rnd["date"])], False)

        # 删一轮：连名单与报文一起拔
        gone = store.delete_round(rnd["date"])
        check("删一轮报出连带走几台几条", (gone["stations"] >= 1, gone["packets"] >= 1),
              (True, True))
        check("删完这一轮就没了", store.round_by_date(rnd["date"]), None)
        check("删完名单也空了", store.stations(rnd["date"]), [])
        check("没这一轮就删不出东西", store.delete_round("1999-01-02"), None)

        # 后面那几条比对的是三台那一轮的名单，删完重收一份回来
        for line in LINES:
            record = oa_aprs.parse_line(line)
            if record:
                store.add_packet(record)
        check("删掉那一轮以后重收，不会自己又长出一轮", store.rounds(), [])
        store.open_round(rnd["date"], "BG7LZQ-2")
        for line in LINES:
            record = oa_aprs.parse_line(line)
            if record:
                store.add_packet(record)
        check("重新收一份回来还是三台", len(store.stations(rnd["date"])), 3)

        # 改一条报文：按新的一句重解析，名单跟着重算
        target = [p for p in store.packets(limit=50) if p["src"] == "BH4AAA"][0]
        fixed = oa_aprs.parse_line("BH4AAA>APRS,TCPIP*,qAC,T2CHILE:!3113.00N/12122.00E> CQ CNNET PN01")
        res = store.edit_packet(target["id"], fixed)
        check("改一条报文，重算的是这一台", (res["call"], res["was"]), ("BH4AAA", "BH4AAA"))
        live = dict((s["call"], s) for s in store.stations(rnd["date"]))
        check("改过的网格写进名单", live["BH4AAA"]["grid"], "PN01")
        check("改不存在的报文不算数", store.edit_packet(999999, fixed), None)
        moved = oa_aprs.parse_line("BH4ZZZ>APRS,TCPIP*,qAC,T2CHILE:!3113.00N/12122.00E> CQ CNNET PN01")
        store.edit_packet(target["id"], moved)
        calls = [s["call"] for s in store.stations(rnd["date"])]
        check("改呼号：旧的撤掉、新的进来", ("BH4AAA" in calls, "BH4ZZZ" in calls), (False, True))

        # 路径怎么算射频、怎么算网络：网关从射频转上来、网口直上、别名与模式标记各一套
        for line, want, want_digi in PATHS:
            rec = oa_aprs.parse_line(line)
            check("路径：%s" % line.split(">", 1)[1].split(":", 1)[0], (rec["via"], rec["digi"]),
                  (want, want_digi))

        # 已经签过到的那一台再签一次：还是同一行，这一次的网格与那句话盖掉前一次的
        again = oa_store.Store(os.path.join(work, "again"), default_duty="BG7LZQ-2")
        try:
            again.open_round(oa_store.round_date_of(oa_aprs.now_iso()), "BG7LZQ-2")
            for line in ("BG6TA>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET BG6TA OL62 周末在山上{21",
                         "BG6TA>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET BG6TA PM01 改到城里了{22"):
                again.add_packet(oa_aprs.parse_line(line))
            rows = again.roster()["rounds"][0]["stations"]
            check("再签一次还是一个人", len(rows), 1)
            check("再签一次的网格盖掉前一次", rows[0]["grid"], "PM01")
            check("再签一次的那句话盖掉前一次", rows[0]["msg"], "改到城里了")
        finally:
            again.close()

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
        # 再签一次换了链路：路径按后一次算，不沿用第一次的
        via_store.add_packet(oa_aprs.parse_line(
            "BH6EEE>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET OL65"))
        again = dict((r["call"], r) for r in via_store.stations(day))["BH6EEE"]
        check("再签一次走网络就换成网络", again["via"], "is")
        check("再签一次没中继就清掉中继", again["digi"], "")
        via_store.add_packet(oa_aprs.parse_line(
            "BH6EEE>APRS,BG7LZQ-1*,qAR,BG7YAB-3::BG7LZQ-2 :CQ CNNET OL66"))
        again = dict((r["call"], r) for r in via_store.stations(day))["BH6EEE"]
        check("再签一次走射频又换成射频", (again["via"], again["digi"]), ("rf", "BG7LZQ-1"))
        # 主控台改报文或删报文后会重算那一行，口径得跟上
        via_store.rebuild_station(via_store.active_round()["id"], "BH6EEE")
        rebuilt = dict((r["call"], r) for r in via_store.stations(day))["BH6EEE"]
        check("重算名单也按最近一次喊到", (rebuilt["via"], rebuilt["digi"]), ("rf", "BG7LZQ-1"))
        # 同一条报文从另一条链路又收一遍（网上发的被转到射频，另一台网关再收上来）：
        # 不算好几次喊到，也不把网络翻成射频
        via_store.add_packet(oa_aprs.parse_line(
            "BH7ZZZ-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET PM01"))
        via_store.add_packet(oa_aprs.parse_line(
            "BH7ZZZ-9>APRS,WIDE1-1,qAR,BG7YAB-3::BG7LZQ-2 :CQ CNNET PM01"))
        echo_row = dict((r["call"], r) for r in via_store.stations(day))["BH7ZZZ-9"]
        check("同一条又收一遍不翻成射频", echo_row["via"], "is")
        check("那一条也不多算报文", echo_row["packets"], 1)
        # 真是再签一次（话不一样）就按新的算
        via_store.add_packet(oa_aprs.parse_line(
            "BH7ZZZ-9>APRS,BG7YAB-3*,qAR,BG7LZQ-1::BG7LZQ-2 :CQ CNNET PM01 on the hill"))
        again2 = dict((r["call"], r) for r in via_store.stations(day))["BH7ZZZ-9"]
        check("再签一次换了链路就换成射频", (again2["via"], again2["digi"]), ("rf", "BG7YAB-3"))
        check("再签一次报文数加一", again2["packets"], 2)
        via_store.rebuild_station(via_store.active_round()["id"], "BH7ZZZ-9")
        rebuilt2 = dict((r["call"], r) for r in via_store.stations(day))["BH7ZZZ-9"]
        check("重算时同一条的重复也不算", rebuilt2["packets"], 2)
        check("重算后路径还是最近一次那条", (rebuilt2["via"], rebuilt2["digi"]), ("rf", "BG7YAB-3"))
        check("两台都算确认了",
              [via_rows["BH5DDD"]["status"], via_rows["BH6EEE"]["status"]],
              ["confirmed", "confirmed"])
        via_store.close()
    finally:
        shutil.rmtree(via_dir, ignore_errors=True)

    # 没有轮次在收的时候收到的签到：报文留下，不新开一轮，名单上不加人
    idle_dir = tempfile.mkdtemp(prefix="oa-selftest-idle-")
    try:
        idle = oa_store.Store(idle_dir, default_duty="BG7LZQ-2")
        idle_cq = oa_aprs.parse_line(
            "BH0IDL>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET OL62")
        idle_res = idle.add_packet(idle_cq)
        check("没开轮时收到签到不新开一轮", idle.rounds(), [])
        check("没开轮时名单是空的", idle.stations(oa_store.round_date_of(idle_cq["ts"])), [])
        check("没开轮时那条报文还留着",
              idle.db.execute("SELECT COUNT(*) AS n FROM packets").fetchone()["n"], 1)
        check("没开轮时告诉调用方 no_round", idle_res.get("no_round"), True)
        idle_day = oa_store.round_date_of(idle_cq["ts"])
        idle.open_round(idle_day, "BG7LZQ-2")
        idle_res = idle.add_packet(idle_cq)
        check("开了轮，签到并进当轮", idle_res.get("round_id"),
              idle.round_by_date(idle_day)["id"])
        check("开了轮，名单上多一台", len(idle.stations(idle_day)), 1)
        check("开了轮就不再算 no_round", idle_res.get("no_round"), False)
        idle.close()
    finally:
        shutil.rmtree(idle_dir, ignore_errors=True)

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
        pcols = set(r["name"] for r in legacy.db.execute("PRAGMA table_info(packets)"))
        check("老库给报文表补上 msg_grid", "msg_grid" in pcols, True)
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
        check("补回那条的写法", said(again.store.outbox_for("BG1AAA")[0]["line"]), "ACK")
        check("回的那条不挂编号（标准 ack）",
              again.store.outbox_for("BG1AAA")[0]["line"].endswith(":ACK"), True)
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

    # 同一台隔一会儿再喊一次：又回一条；同一条还没发出去时不重复入队
    again_dir = tempfile.mkdtemp(prefix="oa-selftest-again-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = again_dir
        conf["OA_TX_PASSCODE"] = "23736"
        rt = oa_server.Runtime(conf)
        today = oa_store.round_date_of(oa_aprs.now_iso())
        rt.store.open_round(today, "BG7LZQ-3")
        first = "BG5CCC-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG5CCC-7 from OL51"
        rt.on_line(oa_aprs.parse_line(first))
        rows = rt.store.outbox_for("BG5CCC-7")
        check("第一次喊到回一条确认", len(acks(rt, "BG5CCC-7")), 1)
        check("第一次回的写法", said(rows[0]["line"]), "ACK")
        check("回的那条地址对", rows[0]["line"] == "BG7LZQ-3>APRS::BG5CCC-7 :ACK", True)
        check("确认后面跟一句人话", said(rows[1]["line"]),
              "Got OL51, you are #1 on the roster. https://aprsnet.cn/oa")
        check("网址那句后面再提一句关键词", said(rows[2]["line"]),
              "Send U CNNET to confirm.")
        # 那句里的 {url} 换成 OA_SITE_URL；没配就整段不占位，不留一个空尾巴
        rec = {"msg_grid": "OL51"}
        active = rt.store.active_round()
        rt.conf["OA_SITE_URL"] = "https://oa.aprsnet.cn/"
        check("站点地址可改，末尾斜杠不要",
              rt.cq_reply_text(active, "BG5CCC-7", rec),
              "Got OL51, you are #1 on the roster. https://oa.aprsnet.cn")
        rt.conf["OA_SITE_URL"] = ""
        check("没配站点就不留空位",
              rt.cq_reply_text(active, "BG5CCC-7", rec),
              "Got OL51, you are #1 on the roster.")
        rt.conf["OA_SITE_URL"] = oa_server.DEFAULTS["OA_SITE_URL"]
        rt.conf["OA_CQ_REPLY"] = "Got {grid}."
        check("模板里不写 {url} 就不多出地址",
              rt.cq_reply_text(active, "BG5CCC-7", rec), "Got OL51.")
        rt.conf["OA_CQ_REPLY"] = oa_server.DEFAULTS["OA_CQ_REPLY"]
        rt.on_line(oa_aprs.parse_line(first))
        check("没编号的同一条不重复确认", len(acks(rt, "BG5CCC-7")), 1)
        check("排队时不重发那两句", len(rt.store.outbox_for("BG5CCC-7")), 3)

        def mark_sent_old():
            old = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rt.store.db.execute("UPDATE outbox SET sent=? WHERE sent=''", (old,))
            rt.store.db.commit()

        mark_sent_old()
        rt.store.set_station(today, "BG5CCC-7", status="acked", create=False)
        rt.on_line(oa_aprs.parse_line(
            "BG5CCC-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG5CCC-7 from OL52{21"))
        check("回过之后再喊一次照样回", len(acks(rt, "BG5CCC-7")), 2)
        check("第二次回的引他这条的编号", said(acks(rt, "BG5CCC-7")[-1]["line"]), "ack21")
        check("紧跟着不再跟第二句", len(rt.store.outbox_for("BG5CCC-7")), 4)
        mark_sent_old()
        rt.on_line(oa_aprs.parse_line("BG5CCC-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :U CNNET{22"))
        check("回过 U CNNET 只回一句 73",
              said(rt.store.outbox_for("BG5CCC-7")[-1]["line"]), "73 TNX!")
        check("U 那条也回确认", said(rt.store.outbox_for("BG5CCC-7")[-2]["line"]), "ack22")
        check("那句就是 73", len(rt.store.outbox_for("BG5CCC-7")), 6)

        # 对话历史：这一台发来的与我们回给的，合成一条时间线
        lines = rt.store.thread("BG5CCC-7")
        check("对话里两边都有", sorted(set(x["dir"] for x in lines)), ["in", "out"])
        check("对话按时间排", lines == sorted(lines, key=lambda x: x["at"]), True)
        check("对话里的来件是它的报文",
              any(x["dir"] == "in" and "CNNET" in x["text"] for x in lines), True)
        check("对话里的去件都是回它的",
              all(x["text"].startswith("BG7LZQ-3>APRS::BG5CCC-7")
                  for x in lines if x["dir"] == "out"), True)
        check("没写呼号就没有对话", rt.store.thread(""), [])
        rt.on_line(oa_aprs.parse_line(
            "BG5CCC-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :U CNNET"))
        check("再回一次 U 不重复回 73", len(rt.store.outbox_for("BG5CCC-7")), 6)
        mark_sent_old()
        rt.on_line(oa_aprs.parse_line(
            "BG5CCC-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG5CCC-7 from OL53{23"))
        check("第三回喊到又回一条", len(acks(rt, "BG5CCC-7")), 4)
        last = rt.store.outbox_for("BG5CCC-7")[-1]
        rt.store.mark_sent(last["id"], "发不出去")
        old = (datetime.now(UTC) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rt.store.db.execute("UPDATE outbox SET created=? WHERE id=?", (old, last["id"]))
        rt.store.db.commit()
        check("没回成的那条还在补漏里", rt.retry_pending_acks(), 1)
        # 那句话的节流：同一台短时间里只跟一句
        rec53 = oa_aprs.parse_line(
            "BG5CCC-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG5CCC-7 from OL53")
        check("手上有条没发出去的句子就不再跟",
              rt.maybe_cq_reply(rt.store.active_round(), "BG5CCC-7", rec53), False)
        rt._cq_replied.clear()
        check("隔一会儿再喊一次又跟一句",
              rt.maybe_cq_reply(rt.store.active_round(), "BG5CCC-7", rec53), True)
        check("紧接着再来一条不再跟",
              rt.maybe_cq_reply(rt.store.active_round(), "BG5CCC-7", rec53), False)
        # 提示那句留空就只跟回话那句；配了就是两句一起出去
        rt.conf["OA_CONFIRM_TEXT"] = ""
        rt._cq_replied.clear()
        before = len(rt.store.outbox_for("BG5CCC-7"))
        check("提示那句留空也照样跟回话",
              rt.maybe_cq_reply(rt.store.active_round(), "BG5CCC-7", rec53), True)
        check("提示留空就只多一条",
              len(rt.store.outbox_for("BG5CCC-7")) - before, 1)
        rt.conf["OA_CONFIRM_TEXT"] = oa_server.DEFAULTS["OA_CONFIRM_TEXT"]
        rt._cq_replied.clear()
        before = len(rt.store.outbox_for("BG5CCC-7"))
        check("配了提示就两句一起出去",
              rt.maybe_cq_reply(rt.store.active_round(), "BG5CCC-7", rec53), True)
        check("两句一起发两条",
              len(rt.store.outbox_for("BG5CCC-7")) - before, 2)
        rt.store.close()
    finally:
        shutil.rmtree(again_dir, ignore_errors=True)

    # 消息编号：对方那条带编号就回 ack<编号>；对方 ack 回来，发件箱那行记上投递
    mid_dir = tempfile.mkdtemp(prefix="oa-selftest-mid-")
    try:
        rec = oa_aprs.parse_line("BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET OL62{12")
        check("报文末尾的编号读出来", rec["msgid"], "12")
        check("`{12_` 这种不用回确认的也读出编号",
              oa_aprs.parse_line("BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET OL62{12_")["msgid"],
              "12")
        check("没写编号就是空串",
              oa_aprs.parse_line("BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET OL62")["msgid"], "")
        acked_rec = oa_aprs.parse_line("BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :ack9383")
        check("对方回的确认认出编号", (acked_rec["kind"], acked_rec["ack_id"]), ("ack", "9383"))
        check("不收那种也算一种确认",
              oa_aprs.parse_line("BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :rej12")["kind"], "rej")

        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = mid_dir
        conf["OA_TX_PASSCODE"] = "23736"
        rt = oa_server.Runtime(conf)
        today = oa_store.round_date_of(oa_aprs.now_iso())
        rt.store.open_round(today, "BG7LZQ-3")
        rt.on_line(oa_aprs.parse_line(
            "BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET OL62{12"))
        rows = rt.store.outbox_for("BG5DDD-3")
        check("回的确认引对方那条的编号", said(rows[0]["line"]), "ack12")
        # 标准写法：冒号后面整段就是 `ack<编号>`，不再挂自己的消息编号。
        check("确认那句后面不挂编号", rows[0]["line"].endswith(":ack12"), True)
        check("确认不带自己的编号", rows[0]["msgid"], "")
        check("跟的那句带自己的编号", bool(rows[1]["msgid"]), True)
        check("确认那句不占号", rt.store.get_setting("msg_seq") or "0", "2")
        check("报文表里也存下编号", rt.store.packets()[0]["msgid"], "12")
        check("回一句确认再跟两句人话", len(rt.store.outbox_for("BG5DDD-3")), 3)
        # 走完 U CNNET 会再发一句 73，那句**带**自己的编号：对方的 ack 引的就是它。
        rt.on_line(oa_aprs.parse_line(
            "BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :U CNNET{13"))
        rows = rt.store.outbox_for("BG5DDD-3")
        numbered = [r for r in rows if r["msgid"] and said(r["line"]).startswith("73")]
        check("73 那句带自己的编号", len(numbered), 1)
        rt.on_line(oa_aprs.parse_line(
            "BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :ack%s" % numbered[0]["msgid"]))
        after = [r for r in rt.store.outbox_for("BG5DDD-3")
                 if r["msgid"] == numbered[0]["msgid"]]
        check("对方 ack 回来记下投递", bool(after[0]["acked"]), True)
        rt.on_line(oa_aprs.parse_line("BG5DDD-3>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :ack99"))
        check("没那编号的 ack 不动别的", len(rt.store.outbox_for("BG5DDD-3")), len(rows))
        # 名单状态：ACK 只把「只喊到」抬上来，已经确认的不往回掉
        rt.store.set_station(today, "BG1OLD-1", status="called")
        rt.store.mark_acked_station(today, "BG1OLD-1")
        check("回 ACK 后名单抬到已回ACK",
              rt.store.get_station(today, "BG1OLD-1")["status"], "acked")
        rt.store.set_station(today, "BG1OLD-2", status="confirmed")
        rt.store.mark_acked_station(today, "BG1OLD-2")
        check("已确认的不往回掉",
              rt.store.get_station(today, "BG1OLD-2")["status"], "confirmed")
        rt.store.close()
    finally:
        shutil.rmtree(mid_dir, ignore_errors=True)

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
        check("没写网格的先回一条确认", said(rows[0]["line"]), "ACK")
        check("没写网格的回了无效提示", bool(rows) and said(rows[1]["line"]) ==
              "INVALID CHECKIN, RESEND: CQ CNNET <CALL> <GRID>", True)
        check("没写网格的不进名单", rt.store.get_station(today, "BG8ZZZ-1"), None)
        rt.on_line(oa_aprs.parse_line(
            "BG8ZZZ-1>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET 又忘了"))
        check("同一条不重复回", len(rt.store.outbox_for("BG8ZZZ-1")), 2)
        rt.on_line(oa_aprs.parse_line(
            "BG9YYY-2>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET from PM01"))
        check("写了网格的进名单", bool(rt.store.get_station(today, "BG9YYY-2")), True)
        check("名单里的网格取自报文",
              rt.store.get_station(today, "BG9YYY-2")["grid"], "PM01")
        check("报文表里也存下签到报的网格", rt.store.packets()[0]["msg_grid"], "PM01")
        check("写了网格的回的是 ACK",
              said(rt.store.outbox_for("BG9YYY-2")[0]["line"]), "ACK")
        rt.on_line(oa_aprs.parse_line(
            "BG9YYY-2>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :U CNNET"))
        check("回过 U 就是已确认（没再写网格也算）",
              rt.store.get_station(today, "BG9YYY-2")["status"], "confirmed")
        # 发给本台但不是签到那两步：回一条怎么做；不是发给本台的不回
        rt.on_line(oa_aprs.parse_line(
            "BG7AAA-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :你好，在吗"))
        rows = rt.store.outbox_for("BG7AAA-9")
        check("发给本台但不对的先回确认", said(rows[0]["line"]), "ACK")
        check("发给本台但不对的也回了怎么做",
              rows[1]["line"].startswith("BG7LZQ-2>APRS::BG7AAA-9 :"), True)
        check("那句就是怎么做", len(rows) > 1 and said(rows[1]["line"]) ==
              "APRSNETCN OA: CQ CNNET <CALL> <GRID>, THEN U CNNET", True)
        check("不对的报文不进名单", rt.store.get_station(today, "BG7AAA-9"), None)
        rt.on_line(oa_aprs.parse_line(
            "BG7AAA-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :还是我"))
        check("同一台同一轮同一句只回一条", len(rt.store.outbox_for("BG7AAA-9")), 2)
        rt.on_line(oa_aprs.parse_line(
            "BG7AAA-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-2 :CQ CNNET OL62{31"))
        check("改成对的就回 ACK",
              said(rt.store.outbox_for("BG7AAA-9")[-3]["line"]), "ack31")
        # 名次取决于前面那几台是哪一秒进名单的，只比句式，不钉死数字
        said_back = said(rt.store.outbox_for("BG7AAA-9")[-2]["line"])
        check("ACK 后面跟一句人话",
              said_back.startswith("Got OL62, you are #") and
              said_back.endswith(" on the roster. https://aprsnet.cn/oa"), True)
        check("网址后面跟着关键词那句",
              said(rt.store.outbox_for("BG7AAA-9")[-1]["line"]),
              "Send U CNNET to confirm.")
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
        check("4 位网格在世界地图上的位置", oa_image.grid_square("OL62"), (146, 112))
        check("格子小写、带后两位也认", oa_image.grid_square("ol62cq"), (146, 112))
        check("认不出的网格没有位置", oa_image.grid_square("\u2014"), None)
        grid = oa_image.render_grid({"date": "2026-10-17", "call": "BG7LZQ-3", "stations": [
            {"call": "BG7LZQ-2", "grid": "OL62"}, {"call": "BG6TA", "grid": "ON80"}]})
        check("网格表画得出 PNG", grid[:8], b"\x89PNG\r\n\x1a\n")
        cert = oa_image.render_cert({"date": "2026-10-17", "call": "BG7LZQ-3"},
                                    {"call": "BG7LZQ-2", "grid": "OL62AA", "via": "is",
                                     "digi": [], "at": "10-17 01:08"})
        check("奖状画得出 PNG", cert[:8], b"\x89PNG\r\n\x1a\n")
        # 抬头那两行：主控台填过的副标题（红字）与解释标题（灰字）都得印上去
        from io import BytesIO as _BytesIO
        from PIL import Image as _Image
        portrait = {"date": "2026-10-17", "call": "BG7LZQ-3", "stations": []}
        plain = oa_image.render_board(portrait)
        filled = oa_image.render_board(portrait, note="测试轮次", meta={
            "subtitle": "测试轮次", "explain": "北京时间周六整天的空中签到"})

        def _count(body, y, want):
            img = _Image.open(_BytesIO(body)).convert("RGB")
            px = img.load()
            n = 0
            for x in range(oa_image.PAD, oa_image.PAD + 300):
                r, g, b = px[x, y]
                if want == "red" and r > 120 and r - g > 60 and r - b > 60:
                    n += 1
                if want == "grey" and abs(r - g) < 26 and abs(g - b) < 26 and 70 < r < 200:
                    n += 1
            return n

        check("签到表抬头印上副标题（红字）", _count(filled, 178, "red") > 10, True)
        check("没填副标题就不多那一行", _count(plain, 178, "red"), 0)
        check("解释标题印在副标题下面", _count(filled, 226, "grey") > 10, True)
        tall = oa_image.render_board(portrait, meta={
            "subtitle": "测试轮次",
            "explain": "北京时间的周六整天都算，报文发到轮值呼号就行，不用报名，也不用到现场"})
        check("解释标题写长了也画得出", tall[:8], b"\x89PNG\r\n\x1a\n")
        cert_meta = oa_image.render_cert(portrait, {"call": "BG7LZQ-2", "grid": "OL62AA"},
                                         meta={"subtitle": "测试轮次",
                                               "explain": "北京时间周六整天的空中签到"})
        check("奖状跟着长高",
              _Image.open(_BytesIO(cert_meta)).size[1] > _Image.open(_BytesIO(cert)).size[1], True)
        check("编号是 20 位",
              len(oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 01:08", "OL62")), 20)
        check("编号拆回去就是那四样",
              oa_image.read_serial(oa_image.serial_of("BG7LZQ-2", "2026-10-17",
                                                      "10-17 01:08", "OL62")),
              {"call": "BG7LZQ-2", "date": "2026-10-17", "at": "01:08", "grid": "OL62"})
        check("六位网格也拆得回",
              oa_image.read_serial(oa_image.serial_of("BH4HGA-9", "2026-10-17",
                                                      "10-17 09:26", "PM01RD")),
              {"call": "BH4HGA-9", "date": "2026-10-17", "at": "09:26", "grid": "PM01RD"})
        check("缺了时间与网格也拆得回",
              oa_image.read_serial(oa_image.serial_of("N0CALL", "2026-10-17")),
              {"call": "N0CALL", "date": "2026-10-17", "at": "00:00", "grid": ""})
        check("手写的 O 当 0 认",
              oa_image.read_serial("OMM91RP22DH024SQT001")["call"], "BG7LZQ-2")
        check("不够长度不给拆", oa_image.read_serial("ABC"), None)
        check("奖状编号同台同场次同时间同网格不变",
              oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 01:08", "OL62AA"),
              oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 01:08", "OL62AA"))
        check("奖状编号换了场次就变",
              oa_image.serial_of("BG7LZQ-2", "2026-10-18") !=
              oa_image.serial_of("BG7LZQ-2", "2026-10-17"), True)
        check("奖状编号换了时间就变",
              oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 01:08") !=
              oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 02:08"), True)
        check("奖状编号换了网格就变",
              oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 01:08", "OL62AA") !=
              oa_image.serial_of("BG7LZQ-2", "2026-10-17", "10-17 01:08", "OL63AA"), True)
        check("签到表里的国家", oa_image._country("BG7LZQ-2", "zh"), "中国")
        check("国家跟着页面语言走",
              (oa_image._country("JA1ABC", "zh"), oa_image._country("JA1ABC", "en")),
              ("日本", "Japan"))
        check("认不出的呼号给短横", oa_image._country("TR7AAA", "zh"), "\u2014")

    # 国家表与页面上那份一致：两边各改一边就会对不上（只比前缀与两个名字，国旗只在页面上用）
    js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "oa", "roster.js")
    pairs = None
    try:
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        block = js.split("var REGIONS = [", 1)[1].split("\n  ];", 1)[0]
        pairs = set(re.findall(r'\["([^"]+)", "([^"]+)", "([^"]+)"', block))
    except (IOError, OSError, IndexError):
        pairs = None
    if pairs is not None:
        check("国家表与页面那份一致", set(oa_region.REGIONS) == pairs, True)
        check("国家表条数", len(oa_region.REGIONS), len(pairs))
        check("带 SSID 也认", oa_region.region_of("bg7lzq-9"), ("中国", "China"))
        check("长前缀先匹配", oa_region.region_of("VR2ABC")[1], "Hong Kong")

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
        conf["OA_ADMIN_TOKEN"] = "selftest-token"
        rt = oa_server.Runtime(conf)
        rt.store.open_round(oa_store.round_date_of(oa_aprs.now_iso()), "BG7LZQ-2")
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

            def post(path, payload):
                req = urllib.request.Request(
                    "%s%s?token=%s" % (base, path, conf["OA_ADMIN_TOKEN"]),
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                try:
                    body = urllib.request.urlopen(req, timeout=10).read()
                except urllib.error.HTTPError as exc:
                    body = exc.read()
                return json.loads(body.decode("utf-8"))

            rdate = rounds[0]["date"]
            # 流水只列签到那几步：刚区里飘来一条位置报文，也不该把签到那行挤出去
            rt.store.add_packet(oa_aprs.parse_line(
                "BG0XYZ-9>APRS,TCPIP*,qAC,T2X:!3905.45N/07223.83WbMMDVM voice"))
            state = json.loads(urllib.request.urlopen(
                "%s/api/admin/state?token=%s" % (base, conf["OA_ADMIN_TOKEN"]),
                timeout=10).read().decode("utf-8"))
            flow = [p["kind"] for p in state["packets"]]
            check("流水里没有闲聊报文", "other" in flow, False)
            check("流水里有签到报文", ("cq" in flow or "u" in flow), True)
            # 流水那几行的中继是列表：浏览器那边直接 join，写成字符串就抛错，
            # 后面几块（名单、发件箱）跟着画不出来。
            check("流水里报文的中继是列表",
                  all(isinstance(p["digi"], list) for p in state["packets"]), True)
            post("/api/admin/station", {"date": rdate, "call": "BG3POST-9", "grid": "OL62",
                                        "digi": "BG7LZQ-1,BG7YAB-3"})
            post("/api/admin/station", {"date": rdate, "call": "BG3POST-9", "grid": "",
                                        "digi": [], "status": "acked"})
            posted = rt.store.get_station(rdate, "BG3POST-9")
            check("表单空着不改已有的网格", posted["grid"], "OL62")
            check("表单空着不改已有的中继", posted["digi"], "BG7LZQ-1,BG7YAB-3")
            check("表单改得动状态", posted["status"], "acked")
            post("/api/admin/station", {"date": rdate, "call": "BG3POST-9", "grid": "pm01"})
            check("表单写了新网格就换", rt.store.get_station(rdate, "BG3POST-9")["grid"], "PM01")
            rt.store.open_round(rdate, "BG7LZQ-3")
            refused = post("/api/admin/station", {"date": rdate, "call": "BG7LZQ-3"})
            check("台上加轮值呼号回一句说明", refused.get("error"),
                  "轮值呼号不是参与台，不往名单里写")
            check("轮值呼号没进名单", rt.store.get_station(rdate, "BG7LZQ-3"), None)
            acked = post("/api/admin/ack", {"date": rdate, "call": "BG3POST-9"})
            check("回 ACK 用当轮主控呼号", acked["line"].startswith("BG7LZQ-3>APRS::BG3POST-9:ACK{"),
                  True)
            check("台上手发的那条也带编号", acked["line"].endswith("{01") or
                  acked["line"].endswith("{02"), True)
            # 主控台没在「看哪一轮」里挑过时，删台那条不带日期：改删当轮，不能静默不动
            deleted = post("/api/admin/station/delete", {"call": "BG3POST-9"})
            check("没写轮次就删当轮那一台", deleted.get("ok"), True)
            check("删的是当轮", deleted.get("date"), rdate)
            check("删完名单里没有了", rt.store.get_station(rdate, "BG3POST-9"), None)
            missing = post("/api/admin/station/delete", {"date": rdate, "call": "BG3POST-9"})
            check("再删一台不在的回一句没成", missing.get("ok"), False)
        finally:
            httpd.shutdown()
            httpd.server_close()
        rt.store.close()
    finally:
        shutil.rmtree(img_dir, ignore_errors=True)

    # 没在活动时间内（没开轮）收到的签到：只回一句不在活动时间内，不回到名单
    closed_dir = tempfile.mkdtemp(prefix="oa-selftest-closed-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = closed_dir
        conf["OA_ROUND_DUTY"] = "BG7LZQ-3"
        conf["OA_TX_PASSCODE"] = "-1"
        conf["OA_AUTO"] = "1"
        conf["OA_AUTO_ACK"] = "1"
        rt = oa_server.Runtime(conf)
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG6ZZZ-9 OL62{31"))
        rows = rt.store.outbox_for("BG6ZZZ-9")
        check("没开轮时回一句不在活动时间内", said(rows[-1]["line"]), conf["OA_CLOSED_TEXT"])
        check("没开轮时不回 ack", any("ack31" in (r["line"] or "") for r in rows), False)
        check("没开轮时只回这一句", len(rows), 1)
        check("没开轮时名单不加人", rt.store.stations(oa_store.bj_now_date()), [])
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-9>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :U CNNET{32"))
        check("同一台半小时只回一条", len(rt.store.outbox_for("BG6ZZZ-9")), 1)
        check("没开轮时报文照收进流水", len(rt.store.packets(limit=10)), 2)
        rt.conf["OA_CLOSED_TEXT"] = ""
        rt._closed_sent.clear()
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-8>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG6ZZZ-8 OL62{33"))
        check("那一句留空就不回", rt.store.outbox_for("BG6ZZZ-8"), [])
        rt.conf["OA_CLOSED_TEXT"] = oa_server.DEFAULTS["OA_CLOSED_TEXT"]
        # 不标准的报文：把签到格式发回去，而不是那句不在活动时间内
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-7>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :hello there{34"))
        check("不是签到那两步就回格式",
              said(rt.store.outbox_for("BG6ZZZ-7")[-1]["line"]), conf["OA_HELP_TEXT"])
        check("回格式时不回 ack",
              any("ack34" in (r["line"] or "") for r in rt.store.outbox_for("BG6ZZZ-7")), False)
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-6>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET{35"))
        check("签到没写网格就回无效提示",
              said(rt.store.outbox_for("BG6ZZZ-6")[-1]["line"]), conf["OA_BAD_TEXT"])
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-5>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :BLAH BLAH{36"))
        check("没发过的那种话也回格式",
              said(rt.store.outbox_for("BG6ZZZ-5")[-1]["line"]), conf["OA_HELP_TEXT"])
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-5>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :BLAH BLAH{37"))
        check("同一台同一句照样节流", len(rt.store.outbox_for("BG6ZZZ-5")), 1)
        rt.on_line(oa_aprs.parse_line(
            "BG6ZZZ-5>APRS,TCPIP*,qAC,T2X::BG7LZQ-3 :CQ CNNET BG6ZZZ-5 OL62{38"))
        check("换了那一句就不受上一句的节流",
              said(rt.store.outbox_for("BG6ZZZ-5")[-1]["line"]), conf["OA_CLOSED_TEXT"])
        rt.store.close()
    finally:
        shutil.rmtree(closed_dir, ignore_errors=True)

    # 发报那条连接登不上时，管理页顶上要看得见：health 里带着发报状态
    tx_dir = tempfile.mkdtemp(prefix="oa-selftest-tx-")
    try:
        conf = dict(oa_server.DEFAULTS)
        conf["OA_DATA_DIR"] = tx_dir
        conf["OA_ROUND_DUTY"] = "BH6RIZ-6"
        conf["OA_TX_PASSCODE"] = "23736"
        rt = oa_server.Runtime(conf)
        check("没开那条件时发报状态是没开", rt.health()["tx_link"]["state"], "off")
        rt.conf["OA_APRS_HOST"] = "127.0.0.1"      # 连不上：当场看它把状态记成什么
        rt.conf["OA_APRS_PORT"] = "1"
        rt.start_sender()
        for _ in range(60):
            time.sleep(0.1)
            if rt._tx_state["state"] != "off":
                break
        check("登不上就记成发不出去", rt._tx_state["state"], "fail")
        check("失败原因写进状态里", bool(rt._tx_state["error"]), True)
        check("状态里带着用哪个呼号登的", rt.health()["tx_link"]["login"], "BH6RIZ-6")
        # 主控台上能把发报口令换掉：换的就是当轮那个呼号的
        check("没填过时用环境变量那串", rt.tx_passcode, "23736")
        rt.store.set_setting("tx_passcode", "20130")
        check("管理台填的盖过环境变量", rt.tx_passcode, "20130")
        check("填了口令就能发", rt.can_send(), True)
        check("health 里说已设口令", rt.health()["tx_set"], True)
        rt.store.set_setting("tx_passcode", "-")
        check("填 - 就是只记不发", rt.tx_passcode, "")
        check("只记不发时 tx 是假", rt.can_send(), False)
        rt.store.set_setting("tx_passcode", "")
        check("清空就回到环境变量那串", rt.tx_passcode, "23736")
        rt.stop()
        rt.store.close()
    finally:
        shutil.rmtree(tx_dir, ignore_errors=True)

    print("\n%d 项，过了 %d 项" % (len(CHECKS), sum(1 for c in CHECKS if c)))
    return 0 if all(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
