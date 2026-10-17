#!/usr/bin/env python3
"""APRSNETCN OA 后端的数据层。

一轮 = 北京时间的周六。报文先原样进 packets 表，喊到（CQ CNNET）与回复（U CNNET）
再并进 stations 表，一台一行；名单由 stations 导出，格式和页面上 roster.js 读的那份
一样，可以直接喂给活动页与数据面板。

时间一律按 UTC 存，写进名单才换成北京时间。只用标准库。
"""
import csv
import io
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
BJ = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS packets (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       TEXT NOT NULL,
  src      TEXT NOT NULL,
  dst      TEXT NOT NULL DEFAULT '',
  msgto    TEXT NOT NULL DEFAULT '',
  path     TEXT NOT NULL DEFAULT '',
  kind     TEXT NOT NULL DEFAULT 'other',
  text     TEXT NOT NULL DEFAULT '',
  msgid    TEXT NOT NULL DEFAULT '',
  lat      REAL,
  lon      REAL,
  grid     TEXT NOT NULL DEFAULT '',
  msg_grid TEXT NOT NULL DEFAULT '',
  via      TEXT NOT NULL DEFAULT '',
  digi     TEXT NOT NULL DEFAULT '',
  round_id INTEGER,
  raw      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS packets_ts ON packets (ts DESC);

CREATE TABLE IF NOT EXISTS rounds (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  date   TEXT NOT NULL UNIQUE,
  duty   TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 0,
  opened TEXT NOT NULL DEFAULT '',
  closed TEXT NOT NULL DEFAULT '',
  note   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stations (
  round_id INTEGER NOT NULL,
  call     TEXT NOT NULL,
  grid     TEXT NOT NULL DEFAULT '',
  via      TEXT NOT NULL DEFAULT '',
  digi     TEXT NOT NULL DEFAULT '',
  at       TEXT NOT NULL DEFAULT '',
  status   TEXT NOT NULL DEFAULT 'called',
  packets  INTEGER NOT NULL DEFAULT 0,
  first_ts TEXT NOT NULL DEFAULT '',
  last_ts  TEXT NOT NULL DEFAULT '',
  note     TEXT NOT NULL DEFAULT '',
  acked_ts TEXT NOT NULL DEFAULT '',
  confirmed_ts TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (round_id, call)
);

CREATE TABLE IF NOT EXISTS outbox (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  created TEXT NOT NULL,
  src     TEXT NOT NULL DEFAULT '',
  dst     TEXT NOT NULL DEFAULT '',
  line    TEXT NOT NULL,
  msgid   TEXT NOT NULL DEFAULT '',
  sent    TEXT NOT NULL DEFAULT '',
  acked   TEXT NOT NULL DEFAULT '',
  error   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  ts    TEXT NOT NULL DEFAULT ''
);
"""

STATUS_ORDER = {"called": 1, "acked": 2, "confirmed": 3}

STATION_HEAD = ["呼号", "网格", "路径", "中继", "参与时间", "状态", "首次报文", "最后报文",
                "回ACK时间", "确认时间"]
PACKET_HEAD = ["时间", "呼号", "类型", "收件人", "网格", "路径", "中继", "文字", "原报文"]
STATUS_TEXT = {"called": "只喊到", "acked": "已回ACK", "confirmed": "已确认"}


def csv_text(head, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(head)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def station_row(s):
    return [s.get("call", ""), s.get("grid", ""),
            "射频" if s.get("via") == "rf" else ("网络" if s.get("via") == "is" else ""),
            s.get("digi", ""), s.get("at", ""), STATUS_TEXT.get(s.get("status"), s.get("status", "")),
            s.get("first_ts", ""), s.get("last_ts", ""), s.get("acked_ts", ""),
            s.get("confirmed_ts", "")]


def packet_row(p):
    return [p.get("ts", ""), p.get("src", ""), p.get("kind", ""), p.get("msgto", ""),
            p.get("msg_grid") or p.get("grid", ""),
            "射频" if p.get("via") == "rf" else ("网络" if p.get("via") == "is" else ""),
            p.get("digi", ""), p.get("text", ""), p.get("raw", "")]


def now_iso():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def bj(dt):
    return dt.astimezone(BJ)


def parse_iso(text):
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def bj_date(text):
    dt = parse_iso(text)
    return bj(dt).strftime("%Y-%m-%d") if dt else ""


def bj_at(text):
    """页面上的参与时间写法：10-17 15:04（北京时间）。"""
    dt = parse_iso(text)
    return bj(dt).strftime("%m-%d %H:%M") if dt else ""


def utc_at(text):
    """同一个时间点的 UTC 写法，名单上与北京时间并排给。"""
    dt = parse_iso(text)
    return dt.strftime("%m-%d %H:%M") if dt else ""


_CNNET_HEAD = re.compile(r"^\s*(?:(?:CQ|U)\s+)?CNNET\b[ \t]*", re.I)
# 自己那句话前面的呼号、网格与 from 这类连接词，都不算话
_CALL_SHAPE = re.compile(r"^[A-Z]{1,3}[0-9][A-Z]{0,4}(?:-\d{1,2})?$")
_GRID_SHAPE = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2})?$")
_FILLER = ("FROM", "DE", "@", "AT")


def cnnet_tail(text):
    """签到那两行（CQ CNNET / U CNNET）后面跟的那句话，没有就是空串。"""
    return _CNNET_HEAD.sub("", str(text or "")).strip()


def cnnet_note(text, src=""):
    """签到那行里属于用户自己的那句话：开头的呼号、网格与 from 这类词都不算。

    「CQ CNNET BG7XXX from OL62 周末在山上」→「周末在山上」；只写呼号与网格就是空串。
    只看开头三个词，免得把正句吃进去。
    """
    words = cnnet_tail(text).split()
    head = (src or "").strip().upper().split("-")[0]
    cut = 0
    while cut < len(words) and cut < 3:
        word = words[cut].strip(",.;:\uff0c\u3002\u3001").upper()
        if (word in _FILLER or bool(_GRID_SHAPE.match(word)) or bool(_CALL_SHAPE.match(word))
                or (head and word.split("-")[0] == head)):
            cut += 1
            continue
        break
    return " ".join(words[cut:]).strip()


def bj_now_date():
    return bj(datetime.now(UTC)).strftime("%Y-%m-%d")


def seconds_since(text):
    """这个时间点到现在过了多少秒，认不出来就是 None。"""
    dt = parse_iso(text)
    if not dt:
        return None
    return (datetime.now(UTC) - dt).total_seconds()


def bj_weekday(text=None):
    """北京时间的星期几，0 是周一。"""
    dt = datetime.now(UTC) if text is None else (parse_iso(text) or datetime.now(UTC))
    return bj(dt).weekday()


def round_date_of(text):
    """这一行报文算哪一轮：按北京时间归到最近的那个周六。"""
    dt = parse_iso(text) or datetime.now(UTC)
    local = bj(dt)
    shift = {0: 5, 1: 4, 2: 3, 3: 2, 4: 1, 5: 0, 6: -1}[local.weekday()]
    return (local + timedelta(days=shift)).strftime("%Y-%m-%d")


class Store(object):
    def __init__(self, data_dir, default_duty=""):
        self.data_dir = data_dir
        self.default_duty = default_duty or ""
        if data_dir and not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        self.path = os.path.join(data_dir or ".", "oa.db")
        self.lock = threading.RLock()      # 可重入：add_packet 里还会回调 round_id_for
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self._migrate()
            self.db.commit()

    def _migrate(self):
        """老库补列：建库早于新字段时补上，已有的不动。"""
        cols = set(row["name"] for row in self.db.execute("PRAGMA table_info(stations)"))
        for name in ("acked_ts", "confirmed_ts"):
            if name not in cols:
                self.db.execute("ALTER TABLE stations ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % name)
        pcols = set(row["name"] for row in self.db.execute("PRAGMA table_info(packets)"))
        for name in ("msg_grid", "msgid"):
            if name not in pcols:
                self.db.execute("ALTER TABLE packets ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % name)
        ocols = set(row["name"] for row in self.db.execute("PRAGMA table_info(outbox)"))
        for name in ("msgid", "acked"):
            if name not in ocols:
                self.db.execute("ALTER TABLE outbox ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % name)

    def close(self):
        with self.lock:
            self.db.close()

    # ---- 轮次 ----

    def rounds(self):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM rounds ORDER BY date DESC").fetchall()]

    def round_by_date(self, date):
        with self.lock:
            row = self.db.execute("SELECT * FROM rounds WHERE date=?", (date,)).fetchone()
            return dict(row) if row else None

    def active_round(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM rounds WHERE active=1 ORDER BY date DESC").fetchone()
            return dict(row) if row else None

    def ensure_round(self, date, duty=None):
        with self.lock:
            row = self.db.execute("SELECT * FROM rounds WHERE date=?", (date,)).fetchone()
            if row:
                return row["id"]
            cur = self.db.execute(
                "INSERT INTO rounds (date, duty, opened) VALUES (?,?,?)",
                (date, self.default_duty if duty is None else duty, now_iso()))
            self.db.commit()
            return cur.lastrowid

    def open_round(self, date, duty=""):
        """把这一轮设成在收的：收到的新报文都并进它。"""
        rid = self.ensure_round(date)
        with self.lock:
            self.db.execute("UPDATE rounds SET active=0 WHERE active=1")
            self.db.execute("UPDATE rounds SET active=1, closed='', opened=COALESCE(NULLIF(opened,''),?) WHERE id=?",
                            (now_iso(), rid))
            if duty:
                self.db.execute("UPDATE rounds SET duty=? WHERE id=?", (duty, rid))
            self.db.commit()
        return rid

    def close_round(self, date):
        with self.lock:
            self.db.execute("UPDATE rounds SET active=0, closed=? WHERE date=?", (now_iso(), date))
            self.db.commit()

    def set_duty(self, date, call):
        rid = self.ensure_round(date)
        with self.lock:
            self.db.execute("UPDATE rounds SET duty=? WHERE id=?", (call, rid))
            self.db.commit()
        return rid

    # ---- 报文 ----

    def round_id_for(self, ts):
        row = self.active_round()
        if row:
            return row["id"]
        return self.ensure_round(round_date_of(ts))

    def add_packet(self, record):
        """一条报文进库；喊到与回复顺带并进当轮名单。

        签到那行没写出网格、名单里又还没有这一台时，先不往名单里放，
        返回的 incomplete 为真，由服务器回一条无效提示。
        """
        ts = record.get("ts") or now_iso()
        digi = ",".join(record.get("digi") or [])
        items = (ts, record.get("src", ""), record.get("dst", ""), record.get("msgto", ""),
                 record.get("path", ""), record.get("kind", "other"), record.get("text", ""),
                 record.get("msgid", "") or "",
                 record.get("lat"), record.get("lon"), record.get("grid", ""),
                 record.get("msg_grid", "") or "", record.get("via", ""), digi, record.get("raw", ""))
        incomplete = False
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO packets (ts,src,dst,msgto,path,kind,text,msgid,lat,lon,grid,msg_grid,via,digi,raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", items)
            pid = cur.lastrowid
            rid = None
            if record.get("kind") in ("cq", "u"):
                rid = self.round_id_for(ts)
                self.db.execute("UPDATE packets SET round_id=? WHERE id=?", (rid, pid))
                call = record.get("src", "")
                known = call and self.db.execute(
                    "SELECT 1 FROM stations WHERE round_id=? AND call=?", (rid, call)).fetchone()
                if known or record.get("msg_grid") or record.get("grid"):
                    self._touch(rid, call, ts, record,
                                "confirmed" if record.get("kind") == "u" else "called")
                else:
                    incomplete = True
            self.db.commit()
        return {"id": pid, "round_id": rid, "incomplete": incomplete}

    def _touch(self, rid, call, ts, record, status):
        """一台站进了名单：有就更新，没有就新建。空字段不覆盖已有的值。

        轮值呼号自己发的报文（待命、回 ACK）不是参与台，不往名单里放。
        """
        if not call:
            return
        duty = self.db.execute("SELECT duty FROM rounds WHERE id=?", (rid,)).fetchone()
        if duty and duty["duty"] and duty["duty"].strip().upper() == call.strip().upper():
            return
        grid = record.get("msg_grid") or record.get("grid") or ""
        via = record.get("via", "") or ""
        digi = ",".join(record.get("digi") or [])
        row = self.db.execute("SELECT * FROM stations WHERE round_id=? AND call=?", (rid, call)).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO stations (round_id,call,grid,via,digi,at,status,packets,first_ts,last_ts,"
                " acked_ts,confirmed_ts) VALUES (?,?,?,?,?,?,?,1,?,?,?,?)",
                (rid, call, grid, via, digi, bj_at(ts), status, ts, ts,
                 ts if status in ("acked", "confirmed") else "",
                 ts if status == "confirmed" else ""))
            return
        old = STATUS_ORDER.get(row["status"], 0)
        new_status = status if STATUS_ORDER.get(status, 0) > old else row["status"]
        # 路径按喊到那一下算：这一台后面的报文（回过 U、换链路再发）不改它
        new_via = row["via"] or via
        new_digi = row["digi"] or digi
        acked_ts = row["acked_ts"] or (ts if new_status in ("acked", "confirmed") else "")
        confirmed_ts = row["confirmed_ts"] or (ts if new_status == "confirmed" else "")
        self.db.execute(
            "UPDATE stations SET grid=?, via=?, digi=?, status=?, packets=packets+1, last_ts=?,"
            " acked_ts=?, confirmed_ts=? WHERE round_id=? AND call=?",
            (grid or row["grid"], new_via, new_digi, new_status, ts, acked_ts, confirmed_ts,
             rid, call))

    # ---- 删报文、删轮次 ----

    def delete_packet(self, pid):
        """删掉一条报文，并按剩下的重算它那一台在名单上的那一行。

        名单是从报文推出来的，删一条就得把受影响的那一台重算：剩下的还有就按剩下的算，
        一条都不剩就把这一台从名单上撤掉。删不存在的编号返回 None。
        """
        with self.lock:
            row = self.db.execute("SELECT src, round_id FROM packets WHERE id=?",
                                  (int(pid),)).fetchone()
            if row is None:
                return None
            self.db.execute("DELETE FROM packets WHERE id=?", (int(pid),))
            self.db.commit()
            rid, src = row["round_id"], row["src"] or ""
        if rid and src:
            self.rebuild_station(rid, src)
        return {"id": int(pid), "call": src}

    def rebuild_station(self, rid, call):
        """按这一轮还剩下的报文，重算一台在名单上的那一行。

        结果与一条条重新收进来一样：网格取最后报过的，路径取第一次报的，
        回过 U CNNET 算已确认，没回但我们发出去过 ACK 算已回 ACK。剩零条就撤掉这一行。

        轮值呼号不是参与台，跟 `_touch` 一样跳过。
        """
        call = (call or "").strip().upper()
        if not call:
            return None
        with self.lock:
            duty = self.db.execute("SELECT duty FROM rounds WHERE id=?", (rid,)).fetchone()
            if duty and (duty["duty"] or "").strip().upper() == call:
                return None
            rows = self.db.execute(
                "SELECT * FROM packets WHERE round_id=? AND src=? AND kind IN ('cq','u')"
                " ORDER BY id ASC", (rid, call)).fetchall()
            if not rows:
                self.db.execute("DELETE FROM stations WHERE round_id=? AND call=?", (rid, call))
                self.db.commit()
                return None
            grid, via, digi, note = "", "", "", ""
            status = "called"
            for r in rows:
                g = (r["msg_grid"] or r["grid"] or "").strip()
                if g:
                    grid = g
                via = via or (r["via"] or "")
                digi = digi or (r["digi"] or "")
                if r["kind"] == "u":
                    status = "confirmed"
                else:
                    note = cnnet_note(r["text"] or "", call) or note
            acked_ts, confirmed_ts = "", ""
            if status == "confirmed":
                first_u = [r["ts"] for r in rows if r["kind"] == "u"]
                confirmed_ts = first_u[0] if first_u else rows[-1]["ts"]
                acked_ts = confirmed_ts
            else:
                rnd = self.db.execute("SELECT opened FROM rounds WHERE id=?", (rid,)).fetchone()
                sent = self.db.execute(
                    "SELECT MIN(created) AS t FROM outbox WHERE dst=? AND sent<>'' AND created>=?",
                    (call, (rnd["opened"] or "") if rnd else "")).fetchone()
                if sent and sent["t"]:
                    status, acked_ts = "acked", sent["t"]
            at = bj_at(rows[0]["ts"])
            first_ts, last_ts = rows[0]["ts"], rows[-1]["ts"]
            old = self.db.execute("SELECT 1 FROM stations WHERE round_id=? AND call=?",
                                  (rid, call)).fetchone()
            if old:
                self.db.execute(
                    "UPDATE stations SET grid=?, via=?, digi=?, at=?, status=?, packets=?, first_ts=?,"
                    " last_ts=?, note=?, acked_ts=?, confirmed_ts=? WHERE round_id=? AND call=?",
                    (grid, via, digi, at, status, len(rows), first_ts, last_ts, note,
                     acked_ts, confirmed_ts, rid, call))
            else:
                self.db.execute(
                    "INSERT INTO stations (round_id,call,grid,via,digi,at,status,packets,first_ts,last_ts,"
                    " note,acked_ts,confirmed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, call, grid, via, digi, at, status, len(rows), first_ts, last_ts, note,
                     acked_ts, confirmed_ts))
            self.db.commit()
        return {"call": call, "status": status, "packets": len(rows)}

    def delete_round(self, date):
        """把一轮连同它的名单与报文一起删掉。

        删错了一轮就得连根拔掉：只删 rounds 那一行，名单与流水还挂在那个 id 上，
        数字看着不对劲又找不回来。删不存在的日期返回 None。
        """
        row = self.round_by_date(date)
        if not row:
            return None
        rid = row["id"]
        with self.lock:
            n_st = self.db.execute("SELECT COUNT(*) AS n FROM stations WHERE round_id=?",
                                   (rid,)).fetchone()["n"]
            n_pk = self.db.execute("SELECT COUNT(*) AS n FROM packets WHERE round_id=?",
                                   (rid,)).fetchone()["n"]
            self.db.execute("DELETE FROM stations WHERE round_id=?", (rid,))
            self.db.execute("DELETE FROM packets WHERE round_id=?", (rid,))
            self.db.execute("DELETE FROM rounds WHERE id=?", (rid,))
            self.db.commit()
        return {"date": date, "stations": n_st, "packets": n_pk}

    def edit_packet(self, pid, record):
        """改一条报文的原文：按新的一句重写这一行，再把受影响的那一台重算。

        解析在新一句那边做（服务器拿 `oa_aprs.parse_line` 解好再递进来），这里只管写库
        与重算名单。报文是名单的来源，改一条就是重收一条。

        呼号也改了的话，旧的那一台与新的一台都要重算：旧的可能一条都不剩（撤掉），
        新的可能多出一条。没这条报文返回 None。
        """
        with self.lock:
            old = self.db.execute("SELECT src, round_id FROM packets WHERE id=?",
                                  (int(pid),)).fetchone()
            if old is None:
                return None
            digi = ",".join(record.get("digi") or [])
            self.db.execute(
                "UPDATE packets SET ts=?, src=?, dst=?, msgto=?, path=?, kind=?, text=?, msgid=?,"
                " lat=?, lon=?, grid=?, msg_grid=?, via=?, digi=?, raw=? WHERE id=?",
                (record.get("ts") or now_iso(), record.get("src", ""), record.get("dst", ""),
                 record.get("msgto", ""), record.get("path", ""), record.get("kind", "other"),
                 record.get("text", ""), record.get("msgid", "") or "", record.get("lat"),
                 record.get("lon"), record.get("grid", ""), record.get("msg_grid", "") or "",
                 record.get("via", ""), digi, record.get("raw", ""), int(pid)))
            self.db.commit()
            rid, was = old["round_id"], old["src"] or ""
        now_call = record.get("src", "") or ""
        out = {"id": int(pid), "call": now_call, "was": was}
        for call in (was, now_call):
            if rid and call:
                out["station"] = self.rebuild_station(rid, call)
        return out

    def packets(self, limit=200, kind=None, kinds=None):
        """最近几百条报文。`kinds` 是只要这几种（签到那几步），闲聊不进流水。"""
        sql = "SELECT * FROM packets"
        args = []
        if kind:
            sql = sql + " WHERE kind=?"
            args.append(kind)
        elif kinds:
            sql = sql + " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
            args.extend(kinds)
        sql = sql + " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            rows = [dict(r) for r in self.db.execute(sql, tuple(args)).fetchall()]
        for row in rows:            # 库里 digi 是逗号串，发出去一律是列表，跟名单那边一个形状
            row["digi"] = [d for d in (row.get("digi") or "").split(",") if d]
        return rows

    # ---- 名单 ----

    def stations(self, date):
        row = self.round_by_date(date)
        if not row:
            return []
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM stations WHERE round_id=? ORDER BY first_ts ASC, call ASC",
                (row["id"],)).fetchall()
        return [dict(r) for r in rows]

    def get_station(self, date, call):
        row = self.round_by_date(date)
        if not row:
            return None
        with self.lock:
            hit = self.db.execute("SELECT * FROM stations WHERE round_id=? AND call=?",
                                  (row["id"], (call or "").strip().upper())).fetchone()
        return dict(hit) if hit else None

    def last_checkin_ts(self, rid, call):
        """这一轮里这一台最近一次「喊到」（CQ CNNET）的时间，没有就是空串。"""
        with self.lock:
            row = self.db.execute(
                "SELECT MAX(ts) AS t FROM packets WHERE round_id=? AND src=? AND kind='cq'",
                (rid, (call or "").strip().upper())).fetchone()
        return (row["t"] or "") if row is not None else ""

    def last_checkin_msgid(self, rid, call):
        """这一轮里这一台最近一次「喊到」那条报文的编号，没有就是空串。"""
        with self.lock:
            row = self.db.execute(
                "SELECT msgid FROM packets WHERE round_id=? AND src=? AND kind='cq'"
                " ORDER BY id DESC LIMIT 1",
                (rid, (call or "").strip().upper())).fetchone()
        return (row["msgid"] or "") if row is not None else ""

    def set_station(self, date, call, grid=None, via=None, digi=None, at=None,
                    status=None, note=None, create=True):
        """手动写一台站：编辑、补录、标状态都走这里。create=False 时只改已有的，不新建。"""
        rid = self.ensure_round(date)
        if not call:
            raise ValueError("没有呼号")
        call = call.strip().upper()
        ts = now_iso()
        with self.lock:
            row = self.db.execute("SELECT * FROM stations WHERE round_id=? AND call=?", (rid, call)).fetchone()
            chain = "" if digi is None else ",".join([d.strip().upper() for d in digi if d and d.strip()])
            if row is None and create:
                duty = self.db.execute("SELECT duty FROM rounds WHERE id=?", (rid,)).fetchone()
                if duty and (duty["duty"] or "").strip().upper() == call:
                    raise ValueError("轮值呼号不是参与台，不往名单里写")
            if row is None and not create:
                return None
            if row is None:
                self.db.execute(
                    "INSERT INTO stations (round_id,call,grid,via,digi,at,status,packets,first_ts,last_ts,note,"
                    " acked_ts,confirmed_ts) VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?)",
                    (rid, call, grid or "", via or "", chain, at or bj_at(ts),
                     status or "called", ts, ts, note or "",
                     ts if status in ("acked", "confirmed") else "",
                     ts if status == "confirmed" else ""))
            else:
                new_status = status or row["status"]
                acked_ts = row["acked_ts"] or (ts if new_status in ("acked", "confirmed") else "")
                confirmed_ts = row["confirmed_ts"] or (ts if new_status == "confirmed" else "")
                self.db.execute(
                    "UPDATE stations SET grid=?, via=?, digi=?, at=?, status=?, note=?, last_ts=?,"
                    " acked_ts=?, confirmed_ts=? WHERE round_id=? AND call=?",
                    (grid if grid is not None else row["grid"],
                     via if via is not None else row["via"],
                     chain if digi is not None else row["digi"],
                     at if at is not None else row["at"],
                     new_status,
                     note if note is not None else row["note"],
                     ts, acked_ts, confirmed_ts, rid, call))
            self.db.commit()
        return rid

    def mark_acked_station(self, date, call):
        """ACK 发出去以后：名单里还只是「只喊到」的抬到「已回ACK」。

        已经走到「已确认」的不往回掉：那一步是对方自己回了 U CNNET，比我们的 ACK 更靠后。
        """
        row = self.get_station(date, call)
        if row and row["status"] == "called":
            return self.set_station(date, call, status="acked", create=False)
        return None

    def drop_station(self, date, call):
        row = self.round_by_date(date)
        if not row:
            return False
        with self.lock:
            cur = self.db.execute("DELETE FROM stations WHERE round_id=? AND call=?",
                                  (row["id"], call.strip().upper()))
            self.db.commit()
            return cur.rowcount > 0

    def roster(self):
        """导出页面上读的那份 JSON。rounds 新的在前，与 pages 上的写法一致。"""
        out = []
        for rnd in self.rounds():
            stations = []
            last = ""
            with self.lock:
                rows = self.db.execute(
                    "SELECT * FROM stations WHERE round_id=? ORDER BY first_ts ASC, call ASC",
                    (rnd["id"],)).fetchall()
                # 每台自己那句话：签到那两行去掉前缀、再去掉呼号与网格，留最后一回非空的。
                # 同一秒里来的两条时间戳一样，必须再按 id 排：只按 ts 排，同秒的两条
                # 谁先出来不定，后面那句覆盖不到前面那句。
                msgs = {}
                for msg_row in self.db.execute(
                        "SELECT src, text FROM packets WHERE round_id=? AND kind IN ('cq','u')"
                        " ORDER BY ts ASC, id ASC", (rnd["id"],)):
                    note = cnnet_note(msg_row["text"], msg_row["src"])
                    if note:
                        msgs[msg_row["src"]] = note
            for row in rows:
                first = bj_at(row["first_ts"])
                stations.append({
                    "call": row["call"],
                    "grid": row["grid"],
                    "via": row["via"],
                    "digi": [d for d in (row["digi"] or "").split(",") if d],
                    "at": row["at"],
                    "status": row["status"],
                    "packets": row["packets"],
                    "first": first,
                    "last": bj_at(row["last_ts"]),
                    # 参与时间是自己手写的时候（和第一次进名单的时间不是一个点）就不给 UTC
                    "atUtc": utc_at(row["first_ts"]) if row["at"] == first else "",
                    "firstUtc": utc_at(row["first_ts"]),
                    "lastUtc": utc_at(row["last_ts"]),
                    "msg": msgs.get(row["call"], ""),
                })
                if row["last_ts"] > last:
                    last = row["last_ts"]
            out.append({
                "date": rnd["date"],
                "updated": bj_date(last) if last else "",
                "call": rnd["duty"],
                "stations": stations,
            })
        return {"updated": (out[0]["updated"] or None) if out else None,
                "meta": self.meta(), "rounds": out}

    def write_roster(self, path):
        """把名单写到磁盘上（给静态站或备份用），没有变化就不动文件。"""
        data = self.roster()
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if path:
            old = ""
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    old = fh.read()
            except (IOError, OSError):
                old = ""
            if old != text:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, path)
                return True
        return False

    # ---- 维护 ----

    def prune(self, keep_other=2000):
        """区域里的闲聊报文只留最近这些条，喊到与回复一直留着。"""
        with self.lock:
            row = self.db.execute("SELECT MAX(id) AS n FROM packets").fetchone()
            newest = (row["n"] or 0)
            cut = newest - int(keep_other)
            if cut <= 0:
                return 0
            cur = self.db.execute("DELETE FROM packets WHERE kind='other' AND id <= ?", (cut,))
            self.db.commit()
            return cur.rowcount

    # ---- 汇总与归档 ----

    def round_stats(self, date):
        """本轮那几个数字，主控台与归档都用它。"""
        row = self.round_by_date(date)
        out = {"date": date, "stations": 0, "called": 0, "acked": 0, "confirmed": 0,
               "rf": 0, "is": 0, "digis": 0, "packets": 0, "last": ""}
        if not row:
            return out
        with self.lock:
            rows = self.db.execute("SELECT * FROM stations WHERE round_id=?", (row["id"],)).fetchall()
            out["packets"] = self.db.execute("SELECT COUNT(*) AS n FROM packets WHERE round_id=?",
                                             (row["id"],)).fetchone()["n"]
        digis = []
        out["stations"] = len(rows)
        for r in rows:
            out[r["status"]] = out.get(r["status"], 0) + 1
            if r["via"] == "rf":
                out["rf"] = out["rf"] + 1
            elif r["via"] == "is":
                out["is"] = out["is"] + 1
            for d in (r["digi"] or "").split(","):
                if d and d not in digis:
                    digis.append(d)
            if r["last_ts"] > out["last"]:
                out["last"] = r["last_ts"]
        out["digis"] = len(digis)
        return out

    def round_packets(self, date, limit=5000):
        row = self.round_by_date(date)
        if not row:
            return []
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM packets WHERE round_id=? ORDER BY id ASC LIMIT ?",
                (row["id"], int(limit))).fetchall()]

    def archive_round(self, date, out_dir):
        """一轮收档：一份 json（名单加流水）与两份 csv（名单、流水）。返回写出的路径。"""
        row = self.round_by_date(date)
        if not row:
            return []
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        stations = self.stations(date)
        packets = self.round_packets(date)
        data = {
            "date": date,
            "duty": row["duty"],
            "opened": row["opened"],
            "closed": row["closed"] or now_iso(),
            "stats": self.round_stats(date),
            "stations": stations,
            "packets": packets,
        }
        base = os.path.join(out_dir, "round-%s" % date)
        written = []
        with open(base + ".json", "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        written.append(base + ".json")
        with open(base + "-stations.csv", "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(csv_text(STATION_HEAD, [station_row(s) for s in stations]))
        written.append(base + "-stations.csv")
        with open(base + "-packets.csv", "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(csv_text(PACKET_HEAD, [packet_row(p) for p in packets]))
        written.append(base + "-packets.csv")
        return written

    # ---- 发件箱（回 ACK 用） ----

    def queue_line(self, src, dst, line, msgid=""):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO outbox (created,src,dst,line,msgid) VALUES (?,?,?,?,?)",
                (now_iso(), src, dst, line, msgid or ""))
            self.db.commit()
            return cur.lastrowid

    def mark_acked(self, dst, msgid):
        """对方用 `ack<编号>` 收了我们的哪一条：按编号把发件箱那行标上。"""
        if not msgid:
            return 0
        with self.lock:
            cur = self.db.execute(
                "UPDATE outbox SET acked=? WHERE dst=? AND msgid=? AND acked=''",
                (now_iso(), dst, msgid))
            self.db.commit()
            return cur.rowcount

    def unacked(self, dst, since=""):
        """发出去、还没被对方 ack 的那些，早的在前。"""
        sql = "SELECT * FROM outbox WHERE dst=? AND sent<>'' AND acked=''"
        args = [dst]
        if since:
            sql = sql + " AND created>=?"
            args.append(since)
        with self.lock:
            return [dict(r) for r in self.db.execute(sql + " ORDER BY id ASC", tuple(args)).fetchall()]

    def mark_sent(self, oid, error=""):
        with self.lock:
            self.db.execute("UPDATE outbox SET sent=?, error=? WHERE id=?",
                            ("" if error else now_iso(), error, oid))
            self.db.commit()

    def outbox(self, limit=50):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]

    def thread(self, call, limit=200):
        """这一台的往来：它发来的与我们发给它的，按时间排在一起。

        主控台点某一行右端的「对话」就调这个。一来一去都是同一个人跟同一台的
        对话，所以两边合成一条时间线，不分开摆。时间一律用写库时的 ISO 串，
        两边本来就同一种写法，直接排就行。
        """
        call = (call or "").strip()
        if not call:
            return []
        limit = int(limit)
        with self.lock:
            ins = self.db.execute(
                "SELECT id, ts, kind, text, raw, msg_grid, grid, via, digi, msgid FROM packets"
                " WHERE src=? ORDER BY id DESC LIMIT ?", (call, limit)).fetchall()
            outs = self.db.execute(
                "SELECT id, created, line, sent, error, acked, msgid FROM outbox"
                " WHERE dst=? ORDER BY id DESC LIMIT ?", (call, limit)).fetchall()
        lines = []
        for r in ins:
            lines.append({
                "dir": "in", "at": r["ts"], "kind": r["kind"], "text": r["text"] or "",
                "raw": r["raw"] or "", "grid": r["msg_grid"] or r["grid"] or "",
                "via": r["via"] or "", "digi": r["digi"] or "", "msgid": r["msgid"] or "",
            })
        for r in outs:
            lines.append({
                "dir": "out", "at": r["created"], "kind": "ack", "text": r["line"] or "",
                "sent": r["sent"] or "", "error": r["error"] or "", "acked": r["acked"] or "",
                "msgid": r["msgid"] or "",
            })
        lines.sort(key=lambda x: (x.get("at") or "", x.get("dir") or ""))
        return lines

    def outbox_for(self, dst, since=""):
        """发给这一台的那些，早的在前。看它排着队、发失败过几次都靠这个。"""
        sql = "SELECT * FROM outbox WHERE dst=?"
        args = [dst]
        if since:
            sql = sql + " AND created>=?"
            args.append(since)
        with self.lock:
            return [dict(r) for r in self.db.execute(sql + " ORDER BY id ASC", tuple(args)).fetchall()]

    # ---- 设置（主控台上能动的那几个开关） ----

    def get_setting(self, key, default=""):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self.lock:
            self.db.execute(
                "INSERT INTO settings (key,value,ts) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, str(value), now_iso()))
            self.db.commit()
        return str(value)

    def settings(self):
        with self.lock:
            rows = self.db.execute("SELECT key, value FROM settings").fetchall()
        return dict((r["key"], r["value"]) for r in rows)

    # ---- 页面上那两行字（主控自己在操作台上填，跟着名单一起发出去） ----

    # 副标题两语各一条、主控昵称一条。空串就是「没填」，页面见到空串不显示那一位。
    META_KEYS = ("subtitle_zh", "subtitle_en", "op_name")

    def meta(self):
        """活动页上那两行字：副标题（中 / 英）与主控昵称。

        为什么不塞进 `rounds` 里：它们不随周更换，换一轮也不用重填 —— 副标题是这场
        活动叫什么副题，昵称是这个主控叫什么。放在 settings 表里，跟 auto 那几个
        开关同一处，页面上当名单的 meta 一起发出去。
        """
        st = self.settings()
        return {
            "subtitleZh": (st.get("subtitle_zh") or "").strip(),
            "subtitleEn": (st.get("subtitle_en") or "").strip(),
            "opName": (st.get("op_name") or "").strip(),
        }

    def stats(self):
        with self.lock:
            rows = self.db.execute("SELECT kind, COUNT(*) AS n FROM packets GROUP BY kind").fetchall()
            counts = dict((r["kind"], r["n"]) for r in rows)
            total = self.db.execute("SELECT COUNT(*) AS n FROM packets").fetchone()["n"]
            last = self.db.execute("SELECT ts FROM packets ORDER BY id DESC LIMIT 1").fetchone()
        return {"packets": total, "kinds": counts,
                "last_packet": last["ts"] if last else ""}
