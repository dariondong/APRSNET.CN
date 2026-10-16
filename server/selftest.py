#!/usr/bin/env python3
"""不连网的自检：把几行报文喂进去，看解析与名单对不对。

  python3 selftest.py

会话结束后临时目录会自己删掉，不碰真实数据。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oa_aprs
import oa_store

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
]

CHECKS = []


def check(name, got, want):
    ok = got == want
    CHECKS.append(ok)
    print("%s %s：%s" % ("ok  " if ok else "FAIL", name, got))
    if not ok:
        print("      想要：%s" % (want,))


def main():
    work = tempfile.mkdtemp(prefix="oa-selftest-")
    try:
        store = oa_store.Store(work, default_duty="BG7LZQ-2")
        records = []
        for line in LINES:
            record = oa_aprs.parse_line(line)
            if record:
                records.append(record)
                store.add_packet(record)

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
        check("数字：只数喊到与确认那几句", stats["packets"], 5)
        check("数字：最后一条有时间", bool(stats["last"]), True)
        check("没开过的轮数字全是零", store.round_stats("1999-01-02")["stations"], 0)

        arch_dir = os.path.join(work, "archive")
        files = store.archive_round(rnd["date"], arch_dir)
        check("归档写出三份", sorted(os.path.basename(f) for f in files),
              ["round-%s-packets.csv" % rnd["date"], "round-%s-stations.csv" % rnd["date"],
               "round-%s.json" % rnd["date"]])
        with open(os.path.join(arch_dir, "round-%s.json" % rnd["date"]), "r", encoding="utf-8") as fh:
            arch = json.load(fh)
        check("归档里有名单与流水", (len(arch["stations"]), len(arch["packets"])), (3, 5))
        check("归档里的数字", arch["stats"]["confirmed"], 1)
        check("归档写了收轮时间", bool(arch["closed"]), True)
        with open(os.path.join(arch_dir, "round-%s-stations.csv" % rnd["date"]), "rb") as fh:
            check("CSV 带 BOM", fh.read(3), b"\xef\xbb\xbf")
        check("没这一轮就归档不出东西", store.archive_round("1999-01-02", arch_dir), [])

        store.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

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

    print("\n%d 项，过了 %d 项" % (len(CHECKS), sum(1 for c in CHECKS if c)))
    return 0 if all(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
