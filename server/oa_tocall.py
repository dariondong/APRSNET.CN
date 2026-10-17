#!/usr/bin/env python3
"""TOCALL 认设备：读站点那份官方查表（仓库根上的 tocalls.js），服务端不另抄一份。

tocalls.js 由 `.scratch/cn_gen_tocalls.py` 从 aprs-deviceid 生成，一行一条
`"KEY": "名称 · 类别 · 平台"`。这里只取名称那一段：类别与平台是给人看的说明，
签到表上只要认出这一台用的什么设备。名称本身是个中文短语（官方表里只有 APRS 这一条
写成「未标明设备」）就不算认出了设备，回落到呼号本身。

查表先按原样找，找不到按官方表的写法找：`?` 表示该位任意字符，`*` 表示后面随便。
都不中就把目的呼号原样写出来，好过写一个猜出来的名字。
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}

# 官方表还没有的：本项目自己的客户端用的目的呼号
LOCAL = {"APALOC": "APRSLocus"}

_PAIR = re.compile(r'^"([^"]+)": "(.*)",$', re.M)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_KEEP = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ?*"


def _load(path=None):
    """把 tocalls.js 读成 {键: 名称} 与键的顺序（`?` 通配按文件里的先后取第一条）。"""
    path = path or os.path.join(_HERE, "..", "tocalls.js")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        return {}, []
    head = text.split("window.APRS_TOCALLS", 1)
    if len(head) < 2:
        return {}, []
    table = {}
    for key, value in _PAIR.findall(head[1]):
        name = value.split(" \u00b7 ")[0].replace('\\"', '"').strip()
        if name and not _CJK.search(name):
            table[key] = name
    return table, list(table)


def table():
    """查表本体与键的顺序。读不到就是空表，于是每条都回落到呼号本身。"""
    if "t" not in _CACHE:
        _CACHE["t"], _CACHE["k"] = _load()
    return _CACHE["t"], _CACHE["k"]


def _match(key, tocall):
    """官方表那种写法：`?` 该位任意，`*` 后面随便。"""
    star = key.find("*")
    if star >= 0:
        return tocall.startswith(key[:star])
    if len(key) != len(tocall):
        return False
    for i, ch in enumerate(key):
        if ch != "?" and ch != tocall[i]:
            return False
    return True


def device_of(tocall):
    """这条目的呼号对应的设备名；认不出就把呼号本身写上去，空着给短横。"""
    text = "".join(ch for ch in str(tocall or "").upper() if ch in _KEEP)
    if not text:
        return "\u2014"
    if text in LOCAL:
        return LOCAL[text]
    rows, keys = table()
    if text in rows:
        return rows[text]
    star = ""
    for key in keys:
        if "?" not in key and "*" not in key:
            continue
        if not _match(key, text):
            continue
        if "?" in key:
            return rows[key]
        if not star:
            star = rows[key]
    return star or text


if __name__ == "__main__":                      # 手边看一眼：python3 oa_tocall.py APDR16 APRS
    import sys
    for arg in sys.argv[1:] or ["APDR16", "APALOC", "APRS", "APZZZZ"]:
        sys.stdout.write("%-8s %s\n" % (arg, device_of(arg)))
