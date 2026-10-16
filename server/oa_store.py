#!/usr/bin/env python3
"""APRSNETCN OA 后端的数据层。

一轮 = 北京时间的周六。报文先原样进 packets 表，喊到（CQ CNNET）与回复（U CNNET）
再并进 stations 表，一台一行；名单由 stations 导出，格式和页面上 roster.js 读的那份
一样，可以直接喂给活动页与数据面板。

时间一律按 UTC 存，写进名单才换成北京时间。只用标准库。
"""
import json
import os
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
  lat      REAL,
  lon      REAL,
  grid     TEXT NOT NULL DEFAULT '',
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
  PRIMARY KEY (round_id, call)
);

CREATE TABLE IF NOT EXISTS outbox (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  created TEXT NOT NULL,
  src     TEXT NOT NULL DEFAULT '',
  dst     TEXT NOT NULL DEFAULT '',
  line    TEXT NOT NULL,
  sent    TEXT NOT NULL DEFAULT '',
  error   TEXT NOT NULL DEFAULT ''
);
"""

STATUS_ORDER = {"called": 1, "acked": 2, "confirmed": 3}


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


def bj_now_date():
    return bj(datetime.now(UTC)).strftime("%Y-%m-%d")


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
            self.db.commit()

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
        """一条报文进库；喊到与回复顺带并进当轮名单。"""
        ts = record.get("ts") or now_iso()
        digi = ",".join(record.get("digi") or [])
        items = (ts, record.get("src", ""), record.get("dst", ""), record.get("msgto", ""),
                 record.get("path", ""), record.get("kind", "other"), record.get("text", ""),
                 record.get("lat"), record.get("lon"), record.get("grid", ""),
                 record.get("via", ""), digi, record.get("raw", ""))
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO packets (ts,src,dst,msgto,path,kind,text,lat,lon,grid,via,digi,raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", items)
            pid = cur.lastrowid
            rid = None
            if record.get("kind") in ("cq", "u"):
                rid = self.round_id_for(ts)
                self.db.execute("UPDATE packets SET round_id=? WHERE id=?", (rid, pid))
                self._touch(rid, record.get("src", ""), ts, record,
                            "confirmed" if record.get("kind") == "u" else "called")
            self.db.commit()
        return {"id": pid, "round_id": rid}

    def _touch(self, rid, call, ts, record, status):
        """一台站进了名单：有就更新，没有就新建。空字段不覆盖已有的值。

        轮值呼号自己发的报文（待命、回 ACK）不是参与台，不往名单里放。
        """
        if not call:
            return
        duty = self.db.execute("SELECT duty FROM rounds WHERE id=?", (rid,)).fetchone()
        if duty and duty["duty"] and duty["duty"].strip().upper() == call.strip().upper():
            return
        grid = record.get("grid", "") or ""
        via = record.get("via", "") or ""
        digi = ",".join(record.get("digi") or [])
        row = self.db.execute("SELECT * FROM stations WHERE round_id=? AND call=?", (rid, call)).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO stations (round_id,call,grid,via,digi,at,status,packets,first_ts,last_ts)"
                " VALUES (?,?,?,?,?,?,?,1,?,?)",
                (rid, call, grid, via, digi, bj_at(ts), status, ts, ts))
            return
        old = STATUS_ORDER.get(row["status"], 0)
        new_status = status if STATUS_ORDER.get(status, 0) > old else row["status"]
        new_via = via or row["via"]
        new_digi = digi or row["digi"]
        if row["via"] == "rf" and via == "is":
            new_via = "rf"                     # 喊到那一下走的是射频，后面回消息走网络也不改
            new_digi = row["digi"] or digi
        self.db.execute(
            "UPDATE stations SET grid=?, via=?, digi=?, status=?, packets=packets+1, last_ts=?"
            " WHERE round_id=? AND call=?",
            (grid or row["grid"], new_via, new_digi, new_status, ts, rid, call))

    def packets(self, limit=200, kind=None):
        sql = "SELECT * FROM packets"
        args = []
        if kind:
            sql = sql + " WHERE kind=?"
            args.append(kind)
        sql = sql + " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, tuple(args)).fetchall()]

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

    def set_station(self, date, call, grid=None, via=None, digi=None, at=None,
                    status=None, note=None):
        """手动写一台站：编辑、补录、标状态都走这里。"""
        rid = self.ensure_round(date)
        if not call:
            raise ValueError("没有呼号")
        call = call.strip().upper()
        ts = now_iso()
        with self.lock:
            row = self.db.execute("SELECT * FROM stations WHERE round_id=? AND call=?", (rid, call)).fetchone()
            chain = "" if digi is None else ",".join([d.strip().upper() for d in digi if d and d.strip()])
            if row is None:
                self.db.execute(
                    "INSERT INTO stations (round_id,call,grid,via,digi,at,status,packets,first_ts,last_ts,note)"
                    " VALUES (?,?,?,?,?,?,?,0,?,?,?)",
                    (rid, call, grid or "", via or "", chain, at or bj_at(ts),
                     status or "called", ts, ts, note or ""))
            else:
                self.db.execute(
                    "UPDATE stations SET grid=?, via=?, digi=?, at=?, status=?, note=?, last_ts=?"
                    " WHERE round_id=? AND call=?",
                    (grid if grid is not None else row["grid"],
                     via if via is not None else row["via"],
                     chain if digi is not None else row["digi"],
                     at if at is not None else row["at"],
                     status or row["status"],
                     note if note is not None else row["note"],
                     ts, rid, call))
            self.db.commit()
        return rid

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
            for row in rows:
                stations.append({
                    "call": row["call"],
                    "grid": row["grid"],
                    "via": row["via"],
                    "digi": [d for d in (row["digi"] or "").split(",") if d],
                    "at": row["at"],
                })
                if row["last_ts"] > last:
                    last = row["last_ts"]
            out.append({
                "date": rnd["date"],
                "updated": bj_date(last) if last else "",
                "call": rnd["duty"],
                "stations": stations,
            })
        return {"updated": (out[0]["updated"] or None) if out else None, "rounds": out}

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

    # ---- 发件箱（回 ACK 用） ----

    def queue_line(self, src, dst, line):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO outbox (created,src,dst,line) VALUES (?,?,?,?)",
                (now_iso(), src, dst, line))
            self.db.commit()
            return cur.lastrowid

    def mark_sent(self, oid, error=""):
        with self.lock:
            self.db.execute("UPDATE outbox SET sent=?, error=? WHERE id=?",
                            ("" if error else now_iso(), error, oid))
            self.db.commit()

    def outbox(self, limit=50):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]

    def stats(self):
        with self.lock:
            rows = self.db.execute("SELECT kind, COUNT(*) AS n FROM packets GROUP BY kind").fetchall()
            counts = dict((r["kind"], r["n"]) for r in rows)
            total = self.db.execute("SELECT COUNT(*) AS n FROM packets").fetchone()["n"]
            last = self.db.execute("SELECT ts FROM packets ORDER BY id DESC LIMIT 1").fetchone()
        return {"packets": total, "kinds": counts,
                "last_packet": last["ts"] if last else ""}
