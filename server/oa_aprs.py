#!/usr/bin/env python3
"""APRSNETCN OA 后端：APRS-IS 报文的解析与接收。

APRS-IS 推来的一行是 SRC>DEST,PATH:info。这里把它拆成呼号、路径、位置、网格和文字，
再从文字里认出 CQ CNNET 与 U CNNET。只用标准库，不装任何东西。

接收用 pass -1，只收不发。要发 ACK 得另给 OA_TX_PASSCODE，默认不发。
"""
import re
import socket
import threading
import time
from datetime import datetime, timezone

UTC = timezone.utc

_LINE = re.compile(r"^([^>]{1,32})>([^,:]{1,32})(?:,([^:]*))?:(.*)$", re.S)
_CNNET = re.compile(r"^\s*(CQ|U)\s+CNNET\b", re.I)
_ACK = re.compile(r"^ACK", re.I)
_LAT = re.compile(r"^[0-9]{4}\.[0-9]{2}[NS]$")
_LON = re.compile(r"^[0-9]{5}\.[0-9]{2}[EW]$")
_ALIAS = re.compile(r"^(WIDE|RELAY|TRACE|GATE|SGATE|RFONLY|NOGATE)\d*", re.I)
_FIELD = "ABCDEFGHIJKLMNOPQR"


def now_iso():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def grid_of(lat, lon):
    """经纬度换 4 位梅登黑德网格，越界给空串。"""
    if lat is None or lon is None:
        return ""
    lon = lon + 180.0
    lat = lat + 90.0
    if not (0.0 <= lon < 360.0 and 0.0 <= lat < 180.0):
        return ""
    return "%s%s%d%d" % (_FIELD[int(lon // 20)], _FIELD[int(lat // 10)],
                         int((lon % 20) // 2), int(lat % 10))


def _b91(text):
    value = 0
    for ch in text:
        value = value * 91 + (ord(ch) - 33)
    return value


def _uncompressed(a, b):
    """4903.50N / 07201.75W 这两段。"""
    if not (_LAT.match(a) and _LON.match(b)):
        return None
    try:
        lat = int(a[0:2]) + float(a[2:7]) / 60.0
        lon = int(b[0:3]) + float(b[3:8]) / 60.0
    except ValueError:
        return None
    if a[7] == "S":
        lat = -lat
    if b[8] == "W":
        lon = -lon
    return (round(lat, 5), round(lon, 5))


def _compressed(a, b):
    """压缩位置的两段 4 位 base-91。"""
    if len(a) != 4 or len(b) != 4:
        return None
    for ch in a + b:
        if not 33 <= ord(ch) <= 123:
            return None
    return (round(90.0 - _b91(a) / 380926.0, 5),
            round(-180.0 + _b91(b) / 190463.0, 5))


def _position(rest):
    """类型符后面的那串 → (经纬度, 剩下的文字)。认不出位置就把整串当文字。

    不带压缩时：纬度 8 位、符号表 1 位、经度 9 位、符号码 1 位，后面才是文字。
    压缩时：符号表 1 位、纬度 4 位、经度 4 位、符号码 1 位、航向速度 2 位，后面才是文字。
    """
    if len(rest) >= 19 and rest[8] in "/\\":
        pos = _uncompressed(rest[0:8], rest[9:18])
        if pos:
            return pos, rest[19:]
    if len(rest) >= 12 and rest[0] in "/\\":
        pos = _compressed(rest[1:5], rest[5:9])
        if pos:
            return pos, rest[12:]
    return None, rest


def path_parts(path):
    """路径拆成 (走射频还是走网络, 经过的中继呼号)。

    qAR / 带 * 的中继 / WIDE 这类别名都算射频；TCPIP* 与 qAC 这类 q 构造算网络。
    """
    on_air = False
    digi = []
    seen_q = False
    for raw in (path or "").split(","):
        item = raw.strip()
        if not item:
            continue
        core = item.rstrip("*").strip()
        upper = core.upper()
        if upper.startswith("TCPIP") or upper.startswith("TCPXX"):
            continue
        if len(core) >= 2 and core[0] in "qQ" and core[1] in "aAiIxXzZ":
            if upper == "QAR":
                on_air = True
            seen_q = True
            continue
        if seen_q:
            continue                     # q 构造后面是网关与服务器，不算中继
        if _ALIAS.match(core):
            on_air = True
            continue
        on_air = True
        digi.append(core)
    return ("rf" if on_air else "is"), digi


def parse_info(src, info):
    """报文体 → (kind, 文字, 收件人, 经纬度)。"""
    kind = "other"
    text = info
    msgto = ""
    lat = lon = None
    if info[:1] in ("!", "=", "/", "@"):
        cut = 8 if info[0] in ("/", "@") else 1
        pos, tail = _position(info[cut:])
        if pos:
            lat, lon = pos
        text = tail
    elif info[:1] == ":" and len(info) >= 11:
        msgto = info[1:10].strip()
        text = info[11:].split("{", 1)[0]
    text = text.strip()
    hit = _CNNET.search(text)
    if hit:
        kind = "cq" if hit.group(1).upper() == "CQ" else "u"
    elif _ACK.match(text):
        kind = "ack"
    return kind, text, msgto, lat, lon


def parse_line(line, ts=None):
    """一行 APRS-IS 报文 → 记录；不是报文（服务器横幅、空行）给 None。"""
    raw = (line or "").rstrip("\r\n")
    if not raw or raw.startswith("#"):
        return None
    hit = _LINE.match(raw)
    if not hit:
        return None
    src, dst, path, info = hit.group(1).strip(), hit.group(2).strip(), (hit.group(3) or ""), hit.group(4)
    kind, text, msgto, lat, lon = parse_info(src, info)
    via, digi = path_parts(path)
    return {
        "ts": ts or now_iso(),
        "src": src.upper(),
        "dst": dst.upper(),
        "msgto": msgto.upper(),
        "path": path.strip(),
        "kind": kind,
        "text": text,
        "lat": lat,
        "lon": lon,
        "grid": grid_of(lat, lon),
        "via": via,
        "digi": digi,
        "raw": raw,
    }


class AprsIsLink(threading.Thread):
    """一条只收的 APRS-IS 连接，断了按 5 秒起的退避重连。

    状态挂在 self.status：state（online/waiting/off）、since、lines、last、error。
    on_line(记录) 在接收线程里跑，里面别做慢活。
    """

    def __init__(self, host, port=14580, login="", passcode="-1", filter_text="",
                 on_line=None, retry_min=5, retry_max=60):
        threading.Thread.__init__(self, name="oa-aprs")
        self.daemon = True
        self.host = host
        self.port = int(port)
        self.login = login
        self.passcode = passcode or "-1"
        self.filter_text = (filter_text or "").strip()
        self.on_line = on_line
        self.retry_min = retry_min
        self.retry_max = retry_max
        self.status = {"state": "off", "since": "", "lines": 0, "last": "", "error": ""}
        self._stop = threading.Event()
        self._sock = None

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.status["state"] = "off"

    def run(self):
        backoff = self.retry_min
        while not self._stop.is_set():
            self.status["state"] = "connecting"
            self.status["error"] = ""
            try:
                self._session()
                backoff = self.retry_min
            except Exception as exc:                      # 网络、DNS、服务器踢人
                self.status["state"] = "waiting"
                self.status["error"] = str(exc)[:200]
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(self.retry_max, backoff * 2)
        self.status["state"] = "off"

    def _session(self):
        sock = socket.create_connection((self.host, self.port), timeout=30)
        self._sock = sock
        sock.settimeout(180)
        login = "user %s pass %s vers aprsnetcn-oa 0.1" % (self.login, self.passcode)
        if self.filter_text:
            login = login + " filter " + self.filter_text
        sock.sendall((login + "\r\n").encode("utf-8"))
        self.status["state"] = "online"
        self.status["since"] = now_iso()
        buf = b""
        while not self._stop.is_set():
            chunk = sock.recv(8192)
            if not chunk:
                raise IOError("服务器断开")
            buf = buf + chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").rstrip("\r")
                if not text:
                    continue
                if text.startswith("#"):
                    continue
                self.status["lines"] = self.status["lines"] + 1
                self.status["last"] = now_iso()
                if self.on_line is None:
                    continue
                try:
                    record = parse_line(text)
                except Exception:
                    record = None
                if record:
                    self.on_line(record)


def send_once(host, port, login, passcode, line, timeout=12):
    """发一条报文：连上去、登录、等 logresp、发、走。返回服务器那句 logresp。"""
    if not passcode:
        raise RuntimeError("没有报文口令（OA_TX_PASSCODE 空着），不发")
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(("user %s pass %s vers aprsnetcn-oa 0.1\r\n" % (login, passcode)).encode("utf-8"))
        banner = ""
        buf = b""
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf = buf + chunk
            while b"\n" in buf:
                head, buf = buf.split(b"\n", 1)
                head = head.decode("utf-8", "replace").strip()
                if head.startswith("# logresp"):
                    banner = head
            if banner:
                break
        if "verified" not in banner and "unverified" not in banner:
            raise RuntimeError("登录没有回话：%s" % (banner or "（空）"))
        sock.sendall((line.rstrip("\r\n") + "\r\n").encode("utf-8"))
        time.sleep(0.6)
        return banner
    finally:
        try:
            sock.close()
        except OSError:
            pass
