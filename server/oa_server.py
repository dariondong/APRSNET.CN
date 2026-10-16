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
    "OA_BAD_TEXT": "INVALID CHECKIN, RESEND: CQ CNNET <info> <GRID>",
    "OA_HELP_TEXT": "APRSNETCN OA: CQ CNNET <INFO> <GRID>, THEN U CNNET",
    "OA_ACK_MAX_PER_MINUTE": "12",
    "OA_ROUND_DUTY": "",
    "OA_TEST_NOTE": "",
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
        self.ack_per_minute = as_int(conf.get("OA_ACK_MAX_PER_MINUTE"), 12)
        self._sendq = queue.Queue()
        self._sent_at = []
        self._send_lock = threading.Lock()
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
            if kind in ("cq", "u"):
                if result.get("incomplete"):
                    self.note_hint(record, self.conf["OA_BAD_TEXT"])   # 没写网格
                elif kind == "cq":
                    self.maybe_auto_ack(record, result.get("round_id"))
            elif kind != "ack" and self.to_duty(record):
                self.note_hint(record, self.conf["OA_HELP_TEXT"])      # 不是签到那两步
        except Exception as exc:
            sys.stderr.write("回提示出错：%s\n" % exc)

    # ---- 发报文 ----

    def can_send(self):
        return bool(self.conf["OA_TX_PASSCODE"])

    def rate_ok(self):
        """一分钟内最多发这么多条，免得哪一阵子热闹起来往外刷。"""
        now = time.time()
        with self._send_lock:
            self._sent_at = [t for t in self._sent_at if now - t < 60.0]
            if len(self._sent_at) >= self.ack_per_minute:
                return False
            self._sent_at.append(now)
            return True

    def queue_send(self, src, dst, line, date=""):
        """排队发一条。没配口令就只记进发件箱，主控自己发。"""
        oid = self.store.queue_line(src, dst, line)
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
        while not self._stop.is_set():
            try:
                job = self._sendq.get(timeout=1)
            except queue.Empty:
                continue
            if not self.rate_ok():
                self.store.mark_sent(job["id"], "一分钟里发得太多了，这条没发")
                continue
            try:
                oa_aprs.send_once(self.conf["OA_APRS_HOST"], self.conf["OA_APRS_PORT"],
                                  self.duty_call(), self.conf["OA_TX_PASSCODE"], job["line"])
                self.store.mark_sent(job["id"], "")
                if job["date"] and job["dst"]:
                    self.store.set_station(job["date"], job["dst"], status="acked", create=False)
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

    def ack_line(self, call, duty=""):
        """回给某台站的那行报文：呼号列补到 9 格。"""
        text = self.conf["OA_ACK_TEXT"] or "ACK"
        return "%s>APRS::%s:%s" % (duty or self.duty_call(), call.ljust(9), text)

    def hint_line(self, call, text, duty=""):
        """回给某个台站的那句提示：呼号列补到 9 格。"""
        return "%s>APRS::%s:%s" % (duty or self.duty_call(), call.ljust(9), text)

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
        self.queue_send(duty, src, self.hint_line(src, hint, duty), date=active["date"])

    def maybe_auto_ack(self, record, rid):
        """有人喊到就回一条 ACK，一台一轮只回一次。"""
        if not self.auto_ack_on or not self.can_send():
            return
        active = self.store.active_round()
        if not active or (rid is not None and active["id"] != rid):
            return
        src = (record.get("src") or "").strip().upper()
        duty = self.duty_call()
        if not src or not duty or src == duty:
            return
        station = self.store.get_station(active["date"], src)
        if not station or station["status"] != "called":
            return
        self.queue_send(duty, src, self.ack_line(src, duty), date=active["date"])

    def retry_pending_acks(self):
        """全自动时补漏：没回成的 ACK（当时被限速、或者发失败）过一分钟再试，

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
            if station["status"] != "called" or call == duty:
                continue
            rows = self.store.outbox_for(call, active["opened"] or "")
            if any((not r["sent"]) and (not r["error"]) for r in rows):
                continue                                    # 还排着队，等它发
            if sum(1 for r in rows if r["error"]) >= self.ack_retry:
                continue                                    # 已经补够次数了
            if rows:
                gap = oa_store.seconds_since(rows[-1]["sent"] or rows[-1]["created"])
                if gap is not None and gap < 60.0:
                    continue                                # 一分钟里只补一次
            self.queue_send(duty, call, self.ack_line(call, duty), date=active["date"])
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
        if path in ("/api/board.png", "/api/points.png"):
            self._image("board" if path.startswith("/api/board") else "points", query)
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
                "stats": self.rt.store.round_stats(date),
                "stations": self.rt.store.stations(date),
                "packets": self.rt.store.packets(limit=200),
                "outbox": self.rt.store.outbox(limit=20),
            })
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
            self.rt.store.set_station(
                date, call,
                grid=None if body.get("grid") is None else str(body.get("grid")).strip().upper(),
                via=None if body.get("via") in (None, "") else str(body.get("via")).strip(),
                digi=None if digi is None else digi,
                at=None if body.get("at") in (None, "") else str(body.get("at")).strip(),
                status=None if body.get("status") in (None, "") else str(body.get("status")).strip(),
                note=None if body.get("note") is None else str(body.get("note")).strip())
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
        if path == "/api/admin/ack":
            if not self._need_token():
                return
            date = str(body.get("date") or "").strip() or oa_store.bj_now_date()
            call = str(body.get("call") or "").strip().upper()
            text = str(body.get("text") or "ACK").strip() or "ACK"
            duty = str(body.get("duty") or self.rt.conf["OA_ROUND_DUTY"]).strip().upper()
            if not call or not duty:
                self._fail(400, "缺呼号：回谁、以谁的名义回")
                return
            line = "%s>APRS::%s:%s" % (duty, call.ljust(9), text)
            oid = self.rt.queue_send(duty, call, line, date=date)
            if not self.rt.can_send():
                self.rt.store.set_station(date, call, status="acked", create=False)
            self._json({"ok": True, "queued": self.rt.can_send(), "line": line, "id": oid})
            return
        if path == "/api/admin/settings":
            if not self._need_token():
                return
            if "auto" in body:
                self.rt.set_auto_mode(bool(body.get("auto")))
            self._json({"ok": True, "flags": self.rt.flags()})
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

    def _image(self, what, query):
        """签到表 / 签到点的 PNG。数据面板上那两个下载按钮点到这里。

        ?date=2026-10-17 指定哪一轮，不给就是最新一轮；?lang=en 要英文。
        名单读不出来就画一张空表；没装 Pillow 回 503。
        """
        try:
            import oa_image
        except ImportError:
            self._fail(503, "服务端没装 Pillow，画不了图")
            return
        steps = {"board": oa_image.render_board, "points": oa_image.render_points}
        lang = (query.get("lang") or ["zh"])[0].strip().lower()
        lang = "en" if lang.startswith("en") else "zh"
        date = (query.get("date") or [""])[0].strip()
        rounds = []
        try:
            rounds = self.rt.store.roster().get("rounds") or []
        except Exception as exc:                   # 库有毛病也得回一张看得懂的图
            sys.stderr.write("名单读不出来：%s\n" % exc)
        row = None
        for rnd in rounds:
            if not date or str(rnd.get("date")) == date:
                row = rnd
                break
        if row is None:
            row = {"date": date, "call": "", "stations": []}
        note = self.rt.conf.get("OA_TEST_NOTE") or ""
        try:
            body = steps[what](row, note=note, lang=lang)
        except Exception as exc:
            self._fail(500, "画不出来：%s" % exc)
            return
        name = "aprsnetcn-oa-%s-%s.png" % (row.get("date") or "empty", what)
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
