#!/usr/bin/env python3
"""不连网的自检：把几行报文喂进去，看解析与名单对不对。

  python3 selftest.py

会话结束后临时目录会自己删掉，不碰真实数据。
"""
import json
import os
import shutil
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

        roster = store.roster()
        check("只有一轮", len(roster["rounds"]), 1)
        rnd = roster["rounds"][0]
        check("轮次日期是周六", oa_store.round_date_of(records[0]["ts"]) == rnd["date"], True)
        check("轮值呼号写进名单", rnd["call"], "BG7LZQ-2")
        calls = sorted([s["call"] for s in rnd["stations"]])
        check("名单里三台", calls, ["BG6BBB-7", "BG7XXX-9", "BH4AAA"])
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

        store.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n%d 项，过了 %d 项" % (len(CHECKS), sum(1 for c in CHECKS if c)))
    return 0 if all(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
