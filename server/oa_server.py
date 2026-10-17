#!/usr/bin/env python3
"""APRSNETCN OA 的后端。

做三件事：
  1. 收 APRS-IS 的报文，认 CQ CNNET / U CNNET，把台站并进当轮名单；
  2. 把活动页与数据面板发给用户看，页面上的名单接本机的 /api/roster，
     格式和静态那份 roster.json 一样，页面的脚本一行都不用改；
  3. 给主控一个操作台 /admin：报文流水、名单增删改、回 ACK、导出 roster.json。

用法：
  python3 oa_server.py serve              # 接收 + HTTP，给 systemd 用
  python3 oa_server.py export [路径]      # 只导出一次名单
  python3 oa_server.py feed 文件          # 把文件里的报文集进来（离线试）
  python3 oa_server.py listen [秒数]      # 只连 APRS-IS，把认出来的报文打到终端

配置从环境变量来，也可以给 --env 一个 KEY=VALUE 的文件，清单见 oa-server.env.example。
"""
import argparse
import hmac
import json
import os
import queue
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oa_aprs
import oa_store

DEFAULTS = {
    "OA_DATA_DIR": "/var/lib/oa-server",
    "OA_HOST": "127.0.0.1",
    "OA_PORT": "8090",
    "OA_SITE_ROOT": "",
    "OA_ADMIN_TOKEN": "",
    "OA_APRS_HOST": "rotate.aprs2.net",
    "OA_APRS_PORT": "14580",
    "OA_APRS_LOGIN": "",
    "OA_APRS_FILTER": "a/54/73/18/135 g/BG7LZQ-2",
    "OA_TX_PASSCODE": "",
    "OA_AUTO_ACK": "1",
    "OA_ACK_TEXT": "ACK",
    "OA_CQ_REPLY": "Got {grid}, you are #{rank} on the roster. 73",
    "OA_TNX_TEXT": "73 TNX!",
    "OA_BAD_TEXT": "INVALID CHECKIN, RESEND: CQ CNNET <CALL> <GRID>",
    "OA_HELP_TEXT": "APRSNETCN OA: CQ CNNET <CALL> <GRID>, THEN U CNNET",
    "OA_ACK_MAX_PER_MINUTE": "30",
    "OA_ROUND_DUTY": "",
    "OA_TEST_NOTE": "",
    "OA_TEST_NOTE_EN": "",
    "OA_AUTO_ROUND": "1",
    "OA_AUTO": "1",
    "OA_ACK_RETRY": "3",
    "OA_ARCHIVE_DIR": "",
    "OA_PUBLISH_ROSTER": "",
    "OA_KEEP_OTHER": "2000",
}

PAGES = {
    "/": "oa/index.html",
    "/oa": "oa/index.html",
    "/oa/": "oa/index.html",
    "/oa/index.html": "oa/index.html",
    "/en": "oa/en/index.html",
    "/en/": "oa/en/index.html",
    "/oa/en": "oa/en/index.html",
    "/oa/en/": "oa/en/index.html",
    "/oa/en/index.html": "oa/en/index.html",
    "/oa/data.html": "oa/data.html",
    "/oa/en/data.html": "oa/en/data.html",
    "/data": "oa/data.html",
    "/en/data": "oa/en/data.html",
    "/data.html": "oa/data.html",
    "/en/data.html": "oa/en/data.html",
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".xml": "application/xml; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def load_env(path):
    if not path:
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def config_from_env():
    return dict((key, os.environ.get(key, value)) for key, value in DEFAULTS.items())


def flag(value):
    """1 / yes / true 算开，空着与 0 / no / false 算关。"""
    return (value or "").strip().lower() not in ("", "0", "no", "false")


def as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def line_text(line):
    """一行报文里那句话：去掉前面那段地址与收件人，也去掉末尾的消息编号。"""
    head, sep, info = (line or "").partition(":")
    if not sep:
        return (line or "").strip()
    if info[:1] == ":" and len(info) >= 11:
        info = info[11:]
    return info.split("{", 1)[0].strip()


def pick_station(row, want):
    """名单里找那一台：先按整个呼号找，找不到再按去掉 SSID 的呼号找，只有一台时才认。"""
    want = (want or "").strip().upper()
    if not want:
        return None
    stations = row.get("stations") or []
    for st in stations:
        if (st.get("call") or "").strip().upper() == want:
            return st
    base = want.split("-")[0]
    near = [st for st in stations
            if (st.get("call") or "").strip().upper().split("-")[0] == base]
    return near[0] if len(near) == 1 else None


class Runtime(object):
    def __init__(self, conf):
        self.conf = conf
        self.started = oa_aprs.now_iso()
        self.store = oa_store.Store(conf["OA_DATA_DIR"], conf["OA_ROUND_DUTY"])
        root = conf["OA_SITE_ROOT"] or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.site_root = os.path.realpath(root)
        self.link = None
        self._link_duty = ""
        self.publish_path = conf["OA_PUBLISH_ROSTER"] or ""
        self.archive_dir = conf["OA_ARCHIVE_DIR"] or os.path.join(conf["OA_DATA_DIR"] or ".", "archive")
        # 三个开关都走「库里的设置优先，没设过就看环境变量」。全自动打开时，另外两个跟着开。
        self._auto_env = flag(conf.get("OA_AUTO"))
        self._auto_ack = flag(conf.get("OA_AUTO_ACK"))
        self._auto_round = flag(conf.get("OA_AUTO_ROUND"))
        self._auto_set = self.store.get_setting("auto")
        self.ack_retry = as_int(conf.get("OA_ACK_RETRY"), 3)
        self.ack_per_minute = as_int(conf.get("OA_ACK_MAX_PER_MINUTE"), 30)
        self._sendq = queue.Queue()
        self._sent_at = []
        self._send_lock = threading.Lock()
        self._sender = None
        self._msg_lock = threading.Lock()
        self._cq_replied = {}                       # 呼号 → 上一句回话的时间
        self._stop = threading.Event()

    # ---- 全自动 ----

    @property
    def auto_mode(self):
        """全自动：开轮收轮、回 ACK、补漏都不用人守。主控台上能当场切。"""
        if self._auto_set:
            return self._auto_set == "1"
        return self._auto_env

    def set_auto_mode(self, on):
        self._auto_set = self.store.set_setting("auto", "1" if on else "0")
        return self.auto_mode

    @property
    def auto_ack_on(self):
        return self.auto_mode and self._auto_ack

    @property
    def auto_round_on(self):
        return self.auto_mode and self._auto_round

    def flags(self):
        return {"auto": self.auto_mode, "auto_ack": self.auto_ack_on,
                "auto_round": self.auto_round_on, "ack_retry": self.ack_retry,
                "ack_per_minute": self.ack_per_minute, "publish_roster": bool(self.publish_path),
                "settings": self.store.settings()}

    def duty_filter(self):
        """过滤串里的 g/ 跟着当轮轮值呼号走。

        换一轮换了主控呼号时，发给新呼号的报文才收得到；其余那几段（区域、b/ 之类）照旧。
        """
        base = (self.conf["OA_APRS_FILTER"] or "").strip()
        duty = self.duty_call()
        if not duty:
            return base
        items = [x for x in base.split() if not x.lower().startswith("g/")]
        items.append("g/" + duty)
        return " ".join(items)

    def start_aprs(self):
        login = self.conf["OA_APRS_LOGIN"]
        if not login:
            sys.stderr.write("没有 OA_APRS_LOGIN，只开 HTTP，不连 APRS-IS\n")
            return
        self._link_duty = self.duty_call()
        self.link = oa_aprs.AprsIsLink(
            self.conf["OA_APRS_HOST"], self.conf["OA_APRS_PORT"],
            login=login, passcode="-1", filter_text=self.duty_filter(),
            on_line=self.on_line)
        self.link.start()
        sys.stderr.write("收报文的过滤串：%s\n" % self.duty_filter())

    def follow_duty(self):
        """当轮换了个主控呼号：重连一次，把收件人那段换过去。"""
        if not self.link:
            return
        duty = self.duty_call()
        if duty == self._link_duty:
            return
        self.link.stop()
        self.start_aprs()
        sys.stderr.write("主控换成 %s，重连接收\n" % (duty or "（空）"))

    def on_line(self, record):
        try:
            result = self.store.add_packet(record)
        except Exception as exc:                     # 一条报文出错不能断接收
            sys.stderr.write("收报文出错：%s\n" % exc)
            return
        # 发给轮值呼号的报文，对的那一步走流程，不对的告诉他该发什么。
        # 不是发给本台的（区域里的位置报文之类）不回，免得往天上刷。
        kind = record.get("kind")
        try:
            if kind in ("ack", "rej"):
                # 机器回的确认只记投递：哪一条送到了，标在发件箱里，不再回话。
                # 回一句就成了两边互相确认，没完地往天上刷。
                if record.get("ack_id"):
                    self.store.mark_acked((record.get("src") or "").strip().upper(),
                                          record["ack_id"])
                return
            # 每一条发给本台的报文都先回一条机器 ack：喊到、回 U、普通一句话都一样
            if kind in ("cq", "u") or self.to_duty(record):
                self.ack_message(record, result.get("round_id"))
            if kind in ("cq", "u"):
                if result.get("incomplete"):
                    self.note_hint(record, self.conf["OA_BAD_TEXT"])   # 没写网格
                elif kind == "cq":
                    self.maybe_auto_ack(record, result.get("round_id"))
                else:
                    self.maybe_auto_tnx(record, result.get("round_id"))
            elif self.to_duty(record):
                self.note_hint(record, self.conf["OA_HELP_TEXT"])      # 不是签到那两步
        except Exception as exc:
            sys.stderr.write("回提示出错：%s\n" % exc)

    # ---- 发报文 ----

    def can_send(self):
        return bool(self.conf["OA_TX_PASSCODE"])

    def take_send_slot(self):
        """等一个发送名额：有名额就立刻发，满了就等到腾出名额，不丢这条。

        一分钟 ack_per_minute 条是上限，也是这一分钟里能连着发出去的量：
        报文一块来的时候前面那一批不停顿地回，超过上限的那几条隔几秒就轮到。
        """
        while not self._stop.is_set():
            now = time.time()
            with self._send_lock:
                self._sent_at = [t for t in self._sent_at if now - t < 60.0]
                if len(self._sent_at) < self.ack_per_minute:
                    self._sent_at.append(now)
                    return True
                wait = 60.0 - (now - self._sent_at[0])
            self._stop.wait(max(0.2, min(wait + 0.05, 2.0)))
        return False

    def sender_link(self, duty):
        """当前这一轮的主控那条只发连接；换了主控呼号就换一条。"""
        if self._sender is not None and self._sender.login == duty:
            return self._sender
        if self._sender is not None:
            self._sender.close()
        self._sender = oa_aprs.SenderLink(
            self.conf["OA_APRS_HOST"], self.conf["OA_APRS_PORT"],
            login=duty, passcode=self.conf["OA_TX_PASSCODE"])
        return self._sender

    def queue_send(self, src, dst, line, date="", msgid=""):
        """排队发一条。没配口令就只记进发件箱，主控自己发。"""
        oid = self.store.queue_line(src, dst, line, msgid=msgid)
        if not self.can_send():
            self.store.mark_sent(oid, "只记不发：没配 OA_TX_PASSCODE")
            return oid
        job = {"id": oid, "date": date, "dst": dst, "line": line}
        self._sendq.put(job)
        return oid

    def start_sender(self):
        if not self.can_send():
            sys.stderr.write("没有 OA_TX_PASSCODE，只收不发\n")
            return
        thread = threading.Thread(target=self._send_loop, name="oa-send")
        thread.daemon = True
        thread.start()
        sys.stderr.write("发报文的通道开着，全自动 %s，自动回 ACK %s\n"
                         % ("开" if self.auto_mode else "关", "开" if self.auto_ack_on else "关"))

    def _send_loop(self):
        try:                                  # 先把发送那条连接登好，第一条 ACK 不用等握手
            self.sender_link(self.duty_call()).warm()
        except Exception as exc:
            sys.stderr.write("先连发送连接没成：%s\n" % exc)
        while not self._stop.is_set():
            try:
                job = self._sendq.get(timeout=1)
            except queue.Empty:
                continue
            if not self.take_send_slot():
                return
            try:
                self.sender_link(self.duty_call()).send(job["line"])
                self.store.mark_sent(job["id"], "")
                if job["date"] and job["dst"] and self.is_ack_line(job["line"]):
                    self.store.mark_acked_station(job["date"], job["dst"])
                sys.stderr.write("发出：%s\n" % job["line"][:110])
            except Exception as exc:
                self.store.mark_sent(job["id"], str(exc)[:200])
                sys.stderr.write("发不出去：%s\n" % exc)

    # ---- 自动开轮收轮 ----

    def auto_round(self):
        """按北京时间自己看着：周六开这一轮，周六过完就收轮归档。"""
        if not self.auto_round_on:
            return
        today = oa_store.bj_now_date()
        active = self.store.active_round()
        if oa_store.bj_weekday() == 5:                   # 周六：当天的轮在收
            if not active or active["date"] != today:
                self.store.open_round(today, self.conf["OA_ROUND_DUTY"])
                sys.stderr.write("自动开轮：%s\n" % today)
        elif active:                                     # 周六过完了，收轮
            self.close_and_archive(active["date"])

    def close_and_archive(self, date):
        """收一轮并把这一轮写成档案，已经收过又已经归档过就不重复。"""
        row = self.store.round_by_date(date)
        if not row:
            return []
        mark = os.path.join(self.archive_dir, "round-%s.json" % date)
        if row["closed"] and os.path.exists(mark):
            return []
        self.store.close_round(date)
        written = self.store.archive_round(date, self.archive_dir)
        sys.stderr.write("收轮归档：%s\n" % ", ".join(written))
        return written

    def duty_call(self):
        active = self.store.active_round()
        if active and active["duty"]:
            return active["duty"].strip().upper()
        return (self.conf["OA_ROUND_DUTY"] or "").strip().upper()

    def msg_line(self, call, text, duty="", msgid=""):
        """发出去的一行：呼号列补到 9 格，末尾 `{编号`。"""
        body = text if not msgid else "%s{%s" % (text, msgid)
        return "%s>APRS::%s:%s" % (duty or self.duty_call(), call.ljust(9), body)

    def ack_line(self, call, text, duty=""):
        """确认那一行：`:ADDRESSEE:ack<编号`，编号后面**不再挂自己的消息编号**。

        按 APRS101，确认就是「ack + 对方那条报文末尾的编号」，冒号后面整段都是
        确认。挂上自己的 `{编号` 虽然不违反「编号可选」，但收端只要按「冒号后整段
        = ack+编号」对号，这条确认就对不上，界面上那条消息永远显示不出「已送达」。
        别的台发的确认也是这么写的（`...::BG7LZQ-3 :ack40`）。
        """
        return "%s>APRS::%s:%s" % (duty or self.duty_call(), call.ljust(9), text)

    def next_msgid(self):
        """下一条报文的编号：两位数字轮着用，对方的 `ack<编号>` 引的就是它。"""
        with self._msg_lock:
            cur = as_int(self.store.get_setting("msg_seq", "0"), 0)
            cur = (cur % 99) + 1
            self.store.set_setting("msg_seq", cur)
        return "%02d" % cur

    def send_msg(self, call, text, date="", duty="", msgid="", with_id=True):
        """发一句给某台，记进发件箱。

        `with_id=False` 是给确认用的：确认那句不挂自己的消息编号（见 ack_line）。
        """
        duty = (duty or self.duty_call()).strip().upper()
        call = (call or "").strip().upper()
        if with_id:
            msgid = msgid or self.next_msgid()
            line = self.msg_line(call, text, duty, msgid)
        else:
            msgid = ""
            line = self.ack_line(call, text, duty)
        oid = self.queue_send(duty, call, line, date=date, msgid=msgid)
        return oid, line

    def to_duty(self, record):
        """这条是不是发给轮值呼号的（看报文里那个收件人）。"""
        duty = self.duty_call()
        return bool(duty) and (record.get("msgto") or "").strip().upper() == duty

    def note_hint(self, record, text):
        """给对台回一条提示，让他知道该发什么。

        只在当轮里回，同一台同一轮同一句话只回一条，免得它一直重发的时候一直答。
        """
        if not self.auto_ack_on or not self.can_send():
            return
        active = self.store.active_round()
        duty = self.duty_call()
        if not active or not duty:
            return
        src = (record.get("src") or "").strip().upper()
        if not src or src == duty:
            return
        hint = (text or "").strip()
        if not hint:
            return
        mark = hint[:12]
        for row in self.store.outbox_for(src, active["opened"] or ""):
            if mark and mark in (row["line"] or ""):
                return
        self.send_msg(src, hint, date=active["date"])

    def ack_text(self, ack_id=""):
        """确认用哪句：对方那条带编号就回 `ack<编号>`，没有就回配的那句。"""
        if ack_id:
            return "ack%s" % ack_id
        return self.conf["OA_ACK_TEXT"] or "ACK"

    def is_ack_line(self, line):
        """这行是不是回给对方的确认：配的那句 ACK，或者机器那种 `ack<编号>`。"""
        text = line_text(line).lower()
        return text == self.ack_text().lower() or text.startswith("ack")

    def our_acks(self, call, since=""):
        """我们回给这一台的那些 ACK；提示（怎么做 / 重发）那几条不算。"""
        want = self.ack_text().lower()
        out = []
        for r in self.store.outbox_for(call, since):
            text = line_text(r["line"]).lower()
            if text == want or text.startswith("ack"):
                out.append(r)
        return out

    def ack_pending(self, active, call):
        """这一台还有一条没回成的 ACK 吗。

        名单里还是「只喊到」，或者最近一次喊到在我们成功回过它之后，都要回。
        同一台隔一会儿再喊一次是又一份喊到，照样回；它自己回 U CNNET 不算喊到。
        """
        call = (call or "").strip().upper()
        station = self.store.get_station(active["date"], call)
        if not station:
            return False
        acks = self.our_acks(call, active["opened"] or "")
        if any((not r["sent"]) and (not r["error"]) for r in acks):
            return False                                # 有一条 ACK 还排着队，等它发
        if station["status"] == "called":
            return True
        last_ok = ""
        for r in acks:
            if r["sent"] and r["sent"] > last_ok:
                last_ok = r["sent"]
        checkin = self.store.last_checkin_ts(active["id"], call)
        return bool(checkin) and checkin > last_ok

    def ack_message(self, record, rid=None):
        """每一条发给本台的报文都回一条机器 ack：同一个编号只回一次。

        对方那条带编号就回 `ack<编号>`（不带自己的编号）；没编号的没得引，
        回配的那句 `OA_ACK_TEXT`，一台一轮一次。
        """
        if not self.auto_ack_on or not self.can_send():
            return False
        active = self.store.active_round()
        if not active or (rid is not None and active["id"] != rid):
            return False
        src = (record.get("src") or "").strip().upper()
        duty = self.duty_call()
        if not src or not duty or src == duty:
            return False
        text = self.ack_text((record.get("msgid") or "").strip())
        want = text.strip().lower()
        for row in self.our_acks(src, active["opened"] or ""):
            if line_text(row["line"]).strip().lower() == want:
                return False                            # 这个编号已经回过
        self.send_msg(src, text, date=active["date"], with_id=False)
        return True

    def maybe_auto_ack(self, record, rid):
        """喊到之后跟一句人话：机器确认已经由 ack_message 回过，这里只跟那一句。"""
        if not self.auto_ack_on or not self.can_send():
            return
        active = self.store.active_round()
        if not active or (rid is not None and active["id"] != rid):
            return
        src = (record.get("src") or "").strip().upper()
        duty = self.duty_call()
        if not src or not duty or src == duty:
            return
        self.maybe_cq_reply(active, src, record)

    def cq_reply_text(self, active, call, record):
        """喊到回了确认之后再跟一句人话：收到的是哪个网格、排在名单第几位。

        确认是给机器看的，这一句是给人的：只回一个 `ack1234` 等于没答理人。
        回话用英文：报文是往天上发的，中文到多数台与手持机上是乱码。
        """
        tpl = (self.conf.get("OA_CQ_REPLY") or "").strip()
        if not tpl or tpl.lower() in ("-", "0", "off", "no"):
            return ""                                 # 不想要这一句就把那一项留空
        grid = (record.get("msg_grid") or record.get("grid") or "").strip().upper()
        calls = [s["call"] for s in self.store.stations(active["date"])]
        rank = calls.index(call) + 1 if call in calls else 0
        text = tpl.replace("{grid}", grid).replace("{rank}", str(rank) if rank else "")
        if not grid:                                # 没写网格的把那半句接平，不留一个空逗号
            text = text.replace("Got ,", "Got it,").replace("收到 ，", "收到，").replace("收到 ,", "收到,")
        return text.strip()

    def maybe_cq_reply(self, active, call, record):
        """跟的那句话：同一台短时间里只跟一句，手机把同一条重发两遍也只回一次。"""
        now = time.time()
        if now - self._cq_replied.get(call, 0.0) < 30.0:
            return False
        text = self.cq_reply_text(active, call, record)
        if not text:
            return False
        self._cq_replied[call] = now
        self.send_msg(call, text, date=active["date"])
        return True

    def maybe_auto_tnx(self, record, rid):
        """一台走完 U CNNET 回一句 73：一台一轮一份。"""
        if not self.auto_ack_on or not self.can_send():
            return
        active = self.store.active_round()
        if not active or (rid is not None and active["id"] != rid):
            return
        src = (record.get("src") or "").strip().upper()
        duty = self.duty_call()
        if not src or not duty or src == duty:
            return
        # U 那条的确认已经由 ack_message 回过，这里只发那句 73
        tnx = self.conf.get("OA_TNX_TEXT") or "73 TNX!"
        mark = tnx[:8]
        for row in self.store.outbox_for(src, active["opened"] or ""):
            if mark and mark in (row["line"] or ""):
                return                                      # 这一轮已经回过 73
        self.send_msg(src, tnx, date=active["date"])

    def retry_pending_acks(self):
        """全自动时补漏：没发出去的 ACK（连不上、服务器不收）过一分钟再试，

        一台一轮最多补 OA_ACK_RETRY 次。还在排队等发的不重复入队。
        """
        if not self.auto_mode or not self.auto_ack_on or not self.can_send():
            return 0
        if self.ack_retry <= 0:
            return 0
        active = self.store.active_round()
        duty = self.duty_call()
        if not active or not duty:
            return 0
        done = 0
        for station in self.store.stations(active["date"]):
            call = station["call"]
            if call == duty:
                continue
            if not self.ack_pending(active, call):
                continue
            acks = self.our_acks(call, active["opened"] or "")
            if sum(1 for r in acks if r["error"]) >= self.ack_retry:
                continue                                    # 已经补够次数了
            if acks:
                gap = oa_store.seconds_since(acks[-1]["sent"] or acks[-1]["created"])
                if gap is not None and gap < 60.0:
                    continue                                # 一分钟里只补一次
            self.send_msg(call, self.ack_text(self.store.last_checkin_msgid(active["id"], call)),
                          date=active["date"], with_id=False)
            done = done + 1
        if done:
            sys.stderr.write("补回 ACK %d 条\n" % done)
        return done

    def start_maintenance(self):
        """后台一件小事：清掉攒下的闲聊报文，需要时把名单写一份到磁盘。"""
        try:
            keep = int(self.conf.get("OA_KEEP_OTHER") or 2000)
        except ValueError:
            keep = 2000

        def loop():
            last = None
            tick = 0
            while not self._stop.is_set():
                tick = tick + 1
                try:
                    self.auto_round()
                except Exception as exc:
                    sys.stderr.write("自动开轮出错：%s\n" % exc)
                try:
                    self.follow_duty()
                except Exception as exc:
                    sys.stderr.write("跟主控呼号出错：%s\n" % exc)
                if tick % 4 == 1:                # 大约每分钟清一次，报文淌得快也不用每 15 秒动库
                    try:
                        dropped = self.store.prune(keep)
                        if dropped:
                            sys.stderr.write("清掉 %d 条闲聊报文\n" % dropped)
                    except Exception as exc:
                        sys.stderr.write("清理出错：%s\n" % exc)
                if tick % 4 == 3:                # 大约每分钟补一次没回成的 ACK
                    try:
                        self.retry_pending_acks()
                    except Exception as exc:
                        sys.stderr.write("补回 ACK 出错：%s\n" % exc)
                if self.publish_path:
                    try:
                        text = json.dumps(self.store.roster(), ensure_ascii=False, indent=2) + "\n"
                        if text != last:
                            self.store.write_roster(self.publish_path)
                            last = text
                    except Exception as exc:
                        sys.stderr.write("写名单出错：%s\n" % exc)
                self._stop.wait(15)

        thread = threading.Thread(target=loop, name="oa-maintain")
        thread.daemon = True
        thread.start()

    def health(self):
        active = self.store.active_round()
        data = self.store.stats()
        link = self.link.status if self.link else {"state": "off", "lines": 0, "last": "", "error": ""}
        return {
            "ok": True,
            "started": self.started,
            "aprs": link,
            "active_round": active["date"] if active else "",
            "duty": active["duty"] if active else self.conf["OA_ROUND_DUTY"],
            "filter": self.duty_filter(),
            "rounds": len(self.store.rounds()),
            "tx": self.can_send(),
            "auto": self.auto_mode,
            "auto_ack": self.auto_ack_on and self.can_send(),
            "ack_per_minute": self.ack_per_minute,
            "ack_retry": self.ack_retry,
            "auto_round": self.auto_round_on,
            "archive_dir": self.archive_dir,
            "packets": data["packets"],
            "kinds": data["kinds"],
            "last_packet": data["last_packet"],
        }

    def stop(self):
        self._stop.set()
        if self.link:
            self.link.stop()
        if self._sender is not None:                 # 只发那条连接也收掉
            self._sender.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "oa-server/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s %s\n" % (time.strftime("%H:%M:%S"), self.address_string(), fmt % args))

    @property
    def rt(self):
        return self.server.runtime

    # ---- 回包 ----

    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", extra)

    def _fail(self, code, message):
        self._json({"ok": False, "error": message}, code)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        raw = self.rfile.read(size) if size else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _token(self):
        given = self.headers.get("X-OA-Token") or ""
        if not given:
            hit = re.search(r"(?:^|;\s*)oa_token=([^;]+)", self.headers.get("Cookie") or "")
            if hit:
                given = hit.group(1)
        if not given:
            given = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        return given

    def _need_token(self):
        want = self.rt.conf.get("OA_ADMIN_TOKEN") or ""
        if not want:
            self._fail(503, "服务端没设 OA_ADMIN_TOKEN，管理接口关着")
            return False
        if not hmac.compare_digest(self._token(), want):
            self._fail(403, "口令不对")
            return False
        return True

    # ---- GET ----

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if path in PAGES:
            self._page(PAGES[path])
            return
        if path == "/admin" or path == "/admin/":
            self._file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html"),
                       cookie="oa_token=%s; Path=/; HttpOnly" % self._token() if self._token() else None)
            return
        if path == "/api/roster":
            self._json(self.rt.store.roster(), 200, {"Access-Control-Allow-Origin": "*"})
            return
        if path in ("/api/board.png", "/api/grid.png"):
            self._image("board" if path.startswith("/api/board") else "grid", query)
            return
        if path == "/api/points.png":              # 旧地址：签到点那张图已经换成网格表
            qs = urlparse(self.path).query
            self._send(302, b"", "text/plain; charset=utf-8",
                       {"Location": "/api/grid.png" + (("?" + qs) if qs else "")})
            return
        if path == "/api/cert.png":
            self._cert(query)
            return
        if path == "/api/health":
            self._json(self.rt.health())
            return
        if path == "/api/packets":
            if not self._need_token():
                return
            limit = int((query.get("limit") or ["200"])[0] or 200)
            kind = (query.get("kind") or [""])[0] or None
            self._json({"ok": True, "packets": self.rt.store.packets(limit=min(limit, 1000), kind=kind)})
            return
        if path == "/api/admin/state":
            if not self._need_token():
                return
            active = self.rt.store.active_round()
            date = (query.get("date") or [""])[0] or (active["date"] if active else oa_store.bj_now_date())
            self._json({
                "ok": True,
                "health": self.rt.health(),
                "round": self.rt.store.round_by_date(date),
                "rounds": self.rt.store.rounds(),
                "meta": self.rt.store.meta(),
                "stats": self.rt.store.round_stats(date),
                "stations": self.rt.store.stations(date),
                "packets": self.rt.store.packets(limit=200),
                "outbox": self.rt.store.outbox(limit=20),
            })
            return
        if path == "/api/admin/thread":
            if not self._need_token():
                return
            call = (query.get("call") or [""])[0]
            self._json({"ok": True, "call": call,
                        "lines": self.rt.store.thread(call, limit=200)})
            return
        if path == "/api/admin/export":
            if not self._need_token():
                return
            date = (query.get("date") or [""])[0] or oa_store.bj_now_date()
            fmt = (query.get("format") or ["json"])[0].lower()
            what = (query.get("what") or ["roster"])[0].lower()
            if fmt == "csv":
                if what == "packets":
                    rows = self.rt.store.round_packets(date)
                    body = oa_store.csv_text(oa_store.PACKET_HEAD,
                                             [oa_store.packet_row(p) for p in rows])
                    name = "round-%s-packets.csv" % date
                else:
                    rows = self.rt.store.stations(date)
                    body = oa_store.csv_text(oa_store.STATION_HEAD,
                                             [oa_store.station_row(s) for s in rows])
                    name = "round-%s-stations.csv" % date
                self._send(200, ("\ufeff" + body).encode("utf-8"), "text/csv; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="%s"' % name})
                return
            body = json.dumps(self.rt.store.roster(), ensure_ascii=False, indent=2) + "\n"
            self._send(200, body, "application/json; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="roster-%s.json"' % date})
            return
        self._static(path)

    # ---- POST ----

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/inject":
            if not self._need_token():
                return
            line = str(body.get("line") or "").strip()
            record = oa_aprs.parse_line(line) if line else None
            if not record:
                self._fail(400, "这行不是报文")
                return
            self.rt.on_line(record)                  # 走同一条路：进来的报文都交给它
            self._json({"ok": True, "record": record})
            return
        if path == "/api/admin/round":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                self._fail(400, "日期要写成 2026-10-17")
                return
            duty = str(body.get("duty") or "").strip().upper()
            rid = self.rt.store.open_round(date, duty)
            self._json({"ok": True, "round": self.rt.store.round_by_date(date), "id": rid})
            return
        if path == "/api/admin/close":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip()
            written = self.rt.close_and_archive(date)
            self._json({"ok": True, "archived": written})
            return
        if path == "/api/admin/duty":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip() or oa_store.bj_now_date()
            call = str(body.get("call") or "").strip().upper()
            self.rt.store.set_duty(date, call)
            self._json({"ok": True, "date": date, "call": call})
            return
        if path == "/api/admin/station":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip() or oa_store.bj_now_date()
            call = str(body.get("call") or "").strip().upper()
            if not call:
                self._fail(400, "要写呼号")
                return
            digi = body.get("digi")
            if isinstance(digi, str):
                digi = [d for d in digi.split(",")]
            # 空着的那几格不动库里已有的值：台上补一台站时表单多半是空的
            grid = str(body.get("grid") or "").strip().upper()
            chain = [d.strip().upper() for d in (digi or []) if d and d.strip()]
            try:
                self.rt.store.set_station(
                    date, call,
                    grid=grid or None,
                    via=None if body.get("via") in (None, "") else str(body.get("via")).strip(),
                    digi=chain or None,
                    at=None if body.get("at") in (None, "") else str(body.get("at")).strip(),
                    status=None if body.get("status") in (None, "") else str(body.get("status")).strip(),
                    note=None if body.get("note") is None else str(body.get("note")).strip())
            except ValueError as exc:
                self._fail(400, str(exc))
                return
            self._json({"ok": True, "stations": self.rt.store.stations(date)})
            return
        if path == "/api/admin/station/delete":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip()
            call = str(body.get("call") or "").strip().upper()
            ok = self.rt.store.drop_station(date, call)
            self._json({"ok": ok, "stations": self.rt.store.stations(date)})
            return
        if path == "/api/admin/packet":
            if not self._need_token():
                return
            try:
                pid = int(body.get("id"))
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "没写要改哪一条"})
                return
            raw = str(body.get("raw") or "").strip()
            if not raw:
                self._json({"ok": False, "error": "空的一行，什么都没改"})
                return
            record = oa_aprs.parse_line(raw)
            if not record:
                self._json({"ok": False, "error": "这一行不像 APRS 报文，什么都没改"})
                return
            res = self.rt.store.edit_packet(pid, record)
            if res is None:
                self._json({"ok": False, "error": "没有这条报文"})
                return
            self._json({"ok": True, "packet": res})
            return
        if path == "/api/admin/packet/delete":
            if not self._need_token():
                return
            try:
                pid = int(body.get("id"))
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "没写要删哪一条"})
                return
            gone = self.rt.store.delete_packet(pid)
            if gone is None:
                self._json({"ok": False, "error": "没有这条报文"})
                return
            self._json({"ok": True, "call": gone["call"]})
            return
        if path == "/api/admin/round/delete":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip()
            gone = self.rt.store.delete_round(date)
            if gone is None:
                self._json({"ok": False, "error": "没有这一轮"})
                return
            self._json({"ok": True, "deleted": gone})
            return
        if path == "/api/admin/ack":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip() or oa_store.bj_now_date()
            call = str(body.get("call") or "").strip().upper()
            text = str(body.get("text") or "ACK").strip() or "ACK"
            duty = str(body.get("duty") or self.rt.duty_call()).strip().upper()
            if not call or not duty:
                self._fail(400, "缺呼号：回谁、以谁的名义回")
                return
            is_ack = bool(re.match(r"^(ack|rej)[A-Za-z0-9]{1,5}$", text, re.I))
            oid, line = self.rt.send_msg(call, text, date=date, duty=duty,
                                         with_id=not is_ack)
            if not self.rt.can_send():
                self.rt.store.set_station(date, call, status="acked", create=False)
            self._json({"ok": True, "queued": self.rt.can_send(), "line": line, "id": oid})
            return
        if path == "/api/admin/settings":
            if not self._need_token():
                return
            if "auto" in body:
                self.rt.set_auto_mode(bool(body.get("auto")))
            # 页面上那两行字：副标题（中 / 英）与主控昵称，空串就是抹掉
            for key in oa_store.Store.META_KEYS:
                if key in body:
                    self.rt.store.set_setting(key, str(body.get(key) or "").strip())
            self._json({"ok": True, "flags": self.rt.flags(),
                        "meta": self.rt.store.meta()})
            return
        if path == "/api/admin/archive":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip() or oa_store.bj_now_date()
            written = self.rt.store.archive_round(date, self.rt.archive_dir)
            self._json({"ok": bool(written), "files": written, "dir": self.rt.archive_dir})
            return
        self._fail(404, "没有这个接口")

    # ---- 文件 ----

    def _round_of(self, query):
        """要哪一轮：`?date=2026-10-17` 指定，不给就取最新一轮；顺带定页面语言。

        返回 (lang, date, row)；没有这一轮时 row 是 None。
        """
        lang = (query.get("lang") or ["zh"])[0].strip().lower()
        lang = "en" if lang.startswith("en") else "zh"
        date = (query.get("date") or [""])[0].strip()
        rounds = []
        try:
            rounds = self.rt.store.roster().get("rounds") or []
        except Exception as exc:                   # 库有毛病也得回一张看得懂的图
            sys.stderr.write("名单读不出来：%s\n" % exc)
        for rnd in rounds:
            if not date or str(rnd.get("date")) == date:
                return lang, date, rnd
        return lang, date, None

    def _note(self, lang):
        """副标题后面那句小字（红）；英文单独一份，空着就沿用中文那句。"""
        note = self.rt.conf.get("OA_TEST_NOTE") or ""
        if lang == "en":
            note = self.rt.conf.get("OA_TEST_NOTE_EN") or note
        return note

    def _oa_image(self):
        """拿画图那份模块；没装 Pillow 就回 503 并给 None。"""
        try:
            import oa_image
        except ImportError:
            self._fail(503, "服务端没装 Pillow，画不了图")
            return None
        return oa_image

    def _image(self, what, query):
        """签到表 / 网格表的 PNG。数据面板上那两个下载按钮点到这里。

        ?date=2026-10-17 指定哪一轮，不给就是最新一轮；?lang=en 要英文。
        名单读不出来就画一张空表；没装 Pillow 回 503。
        """
        oa_image = self._oa_image()
        if oa_image is None:
            return
        steps = {"board": oa_image.render_board, "grid": oa_image.render_grid}
        lang, date, row = self._round_of(query)
        if row is None:
            row = {"date": date, "call": "", "stations": []}
        try:
            body = steps[what](row, note=self._note(lang), lang=lang)
        except Exception as exc:
            self._fail(500, "画不出来：%s" % exc)
            return
        name = "aprsnetcn-oa-%s-%s.png" % (row.get("date") or "empty", what)
        self._send(200, body, "image/png", {
            "Content-Disposition": 'attachment; filename="%s"' % name,
        })

    def _cert(self, query):
        """一台一份的参与证书 PNG：?call=BG7LZQ-2&date=2026-10-17&lang=en。

        呼号先按整个找；找不到再按去掉 SSID 的找，只有唯一一台时才认。
        没有这一轮、或者这一轮里没有这台，回 404；没带呼号回 400。
        """
        oa_image = self._oa_image()
        if oa_image is None:
            return
        want = (query.get("call") or [""])[0].strip()
        if not want:
            self._fail(400, "缺呼号：?call=BG7LZQ-2")
            return
        lang, date, row = self._round_of(query)
        if row is None:
            self._fail(404, "没有 %s 这一轮的名单" % (date or "最新"))
            return
        station = pick_station(row, want)
        if station is None:
            self._fail(404, "这一轮没有 %s 这一台" % want)
            return
        try:
            body = oa_image.render_cert(row, station, note=self._note(lang), lang=lang)
        except Exception as exc:
            self._fail(500, "画不出来：%s" % exc)
            return
        name = "aprsnetcn-oa-%s-%s.png" % (station.get("call") or want, row.get("date") or "empty")
        self._send(200, body, "image/png", {
            "Content-Disposition": 'attachment; filename="%s"' % name,
        })

    def _page(self, rel):
        path = os.path.join(self.rt.site_root, rel)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                html = fh.read()
        except (IOError, OSError):
            self._fail(404, "站上找不到 %s" % rel)
            return
        # 静态那份 data-roster 指的是反代上的 /api/roster；本机自己发页面时直接指本机。
        html = re.sub(r'data-roster="[^"]*"', 'data-roster="/api/roster"', html)
        html = re.sub(r'data-image-base="[^"]*"', 'data-image-base="/api/"', html)
        # 页面在静态站上住在 oa/ 目录里，脚本写成相对路径就行；在 /、/en、/data 这些
        # 短地址上，相对路径会指到根上去，改成绝对路径才找得着。
        html = re.sub(r'(<script src=")(?:\.\./)*(roster\.js|clock\.js)', r'\1/oa/\2', html)
        self._send(200, html, "text/html; charset=utf-8")

    def _file(self, path, cookie=None):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except (IOError, OSError):
            self._fail(404, "没有这个文件")
            return
        extra = {"Set-Cookie": cookie} if cookie else None
        self._send(200, body, MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream"), extra)

    def _static(self, path):
        root = self.rt.site_root
        target = os.path.realpath(os.path.join(root, path.lstrip("/")))
        if not (target == root or target.startswith(root + os.sep)):
            self._fail(403, "越界")
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        self._file(target)


def serve(conf):
    rt = Runtime(conf)
    rt.start_aprs()
    rt.start_sender()
    rt.start_maintenance()
    sys.stderr.write("全自动模式：%s\n" % ("开" if rt.auto_mode else "关"))
    httpd = ThreadingHTTPServer((conf["OA_HOST"], int(conf["OA_PORT"])), Handler)
    httpd.runtime = rt
    sys.stderr.write("oa-server 起来：http://%s:%s/ ，名单 /api/roster ，操作台 /admin\n"
                     % (conf["OA_HOST"], conf["OA_PORT"]))
    stopping = threading.Event()

    def bye(signum, frame):
        if stopping.is_set():
            return
        stopping.set()
        sys.stderr.write("收工\n")
        rt.stop()
        threading.Thread(target=httpd.shutdown).start()

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        rt.stop()
        rt.store.close()


def main(argv):
    parser = argparse.ArgumentParser(description="APRSNETCN OA 后端")
    parser.add_argument("--env", default=os.environ.get("OA_ENV", ""), help="KEY=VALUE 的配置文件")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="接收 + HTTP")
    exporter = sub.add_parser("export", help="导出一次名单")
    exporter.add_argument("path", nargs="?", help="写到哪个文件，不给就打到标准输出")
    feeder = sub.add_parser("feed", help="把文件里的报文集进来")
    feeder.add_argument("file")
    listener = sub.add_parser("listen", help="只连 APRS-IS 打日志")
    listener.add_argument("seconds", nargs="?", type=int, default=30)
    args = parser.parse_args(argv)

    load_env(args.env)
    conf = config_from_env()

    if args.cmd == "export":
        store = oa_store.Store(conf["OA_DATA_DIR"], conf["OA_ROUND_DUTY"])
        data = store.roster()
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if args.path:
            with open(args.path, "w", encoding="utf-8") as fh:
                fh.write(text)
            sys.stderr.write("写到 %s：%d 轮\n" % (args.path, len(data["rounds"])))
        else:
            sys.stdout.write(text)
        store.close()
        return 0

    if args.cmd == "feed":
        store = oa_store.Store(conf["OA_DATA_DIR"], conf["OA_ROUND_DUTY"])
        count = 0
        with open(args.file, "r", encoding="utf-8") as fh:
            for line in fh:
                record = oa_aprs.parse_line(line)
                if not record:
                    continue
                store.add_packet(record)
                count = count + 1
                sys.stdout.write("%-9s %-6s %-9s %s\n" % (record["src"], record["kind"],
                                                          record["via"], record["text"][:60]))
        sys.stdout.write("共集进 %d 条\n" % count)
        store.close()
        return 0

    if args.cmd == "listen":
        def show(record):
            sys.stdout.write("%s %-9s %-4s %-6s %-5s %-6s %s\n" % (
                record["ts"], record["src"], record["kind"], record["via"],
                record["grid"], ",".join(record["digi"])[:24], record["text"][:50]))
            sys.stdout.flush()

        link = oa_aprs.AprsIsLink(conf["OA_APRS_HOST"], conf["OA_APRS_PORT"],
                                  login=conf["OA_APRS_LOGIN"] or "N0CALL", passcode="-1",
                                  filter_text=conf["OA_APRS_FILTER"], on_line=show)
        link.start()
        try:
            time.sleep(max(1, args.seconds))
        except KeyboardInterrupt:
            pass
        sys.stderr.write("状态：%s，收到 %d 行，最后 %s\n" % (
            link.status["state"], link.status["lines"], link.status["last"] or "（还没有）"))
        link.stop()
        return 0

    serve(conf)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
