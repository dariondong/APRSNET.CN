#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APRSNET.CN 页面生成器：把 tool/parts/*.html 里切好的区块组装成整页。

为什么要生成而不是手写每个页面：导航、页脚、head、缓存键、SEO 标签这些东西在
每一页都一模一样，手抄五份迟早对不上。区块正文留在 tool/parts/*.html 里（一个字
都不改），这个脚本只负责外壳、分页、编号、跨页链接与 SEO（title / description /
keywords / canonical / JSON-LD / sitemap）。

用法：
    python3 tool/gen_pages.py          # 写文件
    python3 tool/gen_pages.py --check  # 只比对，不写（自检用）

改内容 → 改 tool/parts/<区块>.html；改分页 → 改下面的 PAGES；改外壳 → 改这里的模板。

产出：5 个 HTML + sitemap.xml + robots.txt。
根目录其他文件（CNAME、BingSiteAuth.xml、assets/、style.css、main.js、
tocalls.js、hero-field.js、passcode.js）都不归它管，也不要去搬。
"""
import datetime
import json
import re
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
PARTS = HERE / "parts"

CSS_V = "34"        # style.css?v=
MAIN_V = "13"       # main.js?v=
SCRIPT_V = {"tocalls.js": "1", "hero-field.js": "12", "passcode.js": "2"}  # 页内脚本的缓存键
ICON_V = "5"        # 图标的缓存键：favicon.png / icon.png 换色后递增（浏览器对 favicon 缓存很钝）

SITE = "https://aprsnet.cn/"
SITE_NAME = "APRSNET.CN"
AUTHOR = "BG7LZQ (Darion)"

# ── 页面表：file / 导航名 / 标题 / 描述 / 关键词 / 区块顺序 ──
# blocks 里的名字 = tool/parts/<名字>.html；hero 只在首页。
PAGES = [
    dict(
        file="index.html", nav="认识",
        title="APRSNET.CN — 信标不止，回响不息。",
        desc="APRSNET.CN 是中文业余无线电 APRS（自动分组报告系统）资料站：APRS 是什么、常用工具、官方 TOCALL 查询，一处查完。",
        og="面向中文业余无线电爱好者的 APRS 资料站：从认识 APRS 开始，附常用工具与官方 TOCALL 查询。",
        keywords="APRS,APRS 中文,APRS 教程,业余无线电,自动分组报告系统,APRSNET,"
                 "APRS 是什么,信标,中继,网关,iGate,TOCALL 查询",
        scripts=("tocalls.js", "hero-field.js"),
        blocks=["hero", "about", "start", "contribute"],
    ),
    dict(
        file="guide.html", nav="上手", h1="上手与设备",
        lead="从零发出第一个包：参数怎么填、频率与路径怎么选，以及电台、TNC、网关这些设备怎么串起来。",
        title="APRS 上手与设备 — 参数、频率与路径、TNC 与网关",
        desc="APRS 上手：信标参数怎么填、频率与路径（含 WIDE1-1 之类怎么写）怎么选，以及电台、TNC、iGate 这些设备怎么串起来。",
        og="APRS 上手与设备：从零发出第一个包，参数、频率、路径与链路一次讲清。",
        keywords="APRS 上手,APRS 参数,APRS 频率,APRS 路径,WIDE1-1,APRS 网关,iGate,"
                 "APRS 电台,TNC,APRS 手台,信标参数",
        blocks=["steps", "freq", "gear"],
    ),
    dict(
        file="tutorial.html", nav="", nav_active="guide.html", h1="从零开始，一步一步",
        lead="前面几节讲的是怎么回事，这里全是动作：先在手机上听，再接电台发射，最后是没上去时怎么查。",
        title="APRS 从零上手教程 — 手机只收、接线发射、故障排查",
        desc="一步一步的 APRS 入门教程：先用手机连 APRS-IS 只收（呼号、服务器、passcode、过滤器怎么填），再接电台设频率与路径发出第一个包，附一份按顺序的故障排查清单。",
        og="APRS 从零上手：手机只收、接线发射、没上去怎么查，一步一确认。",
        keywords="APRS 教程,APRS 入门,APRS 怎么用,APRSdroid 设置,声卡线,蓝牙 TNC,"
                 "APRS 故障排查,APRS 发不出去,APRS-IS 只收,呼号,过滤器",
        blocks=["tut-listen", "tut-send", "tut-trouble"],
    ),
    dict(
        file="packet.html", nav="报文", h1="报文与字段",
        lead="APRS 报文到底长什么样：一条位置包里每一段是什么、字段怎么读，附一份名词小抄。",
        title="APRS 报文与字段 — 位置包逐段拆开看",
        desc="APRS 报文格式：位置包怎么拆开看、每个字段什么意思、呼号与符号怎么读，常用名词解释，照着 APRS101 整理。",
        og="APRS 报文与字段：位置包逐段拆开看，字段与名词一次查清。",
        keywords="APRS 报文,APRS 数据包,APRS 字段,APRS 位置包,APRS 呼号,APRS 符号,"
                 "APRS 报文解码,APRS101,APRS 名词",
        blocks=["packets", "fields", "glossary"],
    ),
    dict(
        file="symbols.html", nav="", nav_active="packet.html", h1="APRS 符号表",
        lead="地图上那个图标是位置包里两个字符定出来的。这一页把主表和替补表的每一格摊开：字符、图标、名字都在格子里。",
        title="APRS 符号表 — 主表与替补表逐格对照",
        desc="APRS 符号表中文版：主表 / 与替补表 \\ 各 94 格逐格列出，一格一张官方图标加中文名，附位置包里哪两个字符定图标、叠加字符怎么用、没定义的格子画成什么样。",
        og="APRS 符号表：主表与替补表各 94 格逐格对照，字符、图标、名字都在格子里。",
        keywords="APRS 符号表,APRS 符号,APRS 图标,主表,替补表,叠加字符,APRS 台站符号,"
                 "APRS 报文,APRS 图标对照,APRS 教程",
        blocks=["sym-read", "sym-primary", "sym-alt"],
        extra_head=('<style>\n'
                    ".sym-grid {\n"
                    "  list-style: none;\n"
                    "  display: grid;\n"
                    "  grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));\n"
                    "  gap: 14px 18px;\n"
                    "}\n"
                    ".sym-cell { display: grid; grid-template-columns: 34px minmax(0, 1fr); "
                    "column-gap: 10px; align-items: center; }\n"
                    "/* 官方图标是透明底的彩色笔画，深色皮肤下会糊成一片，所以固定垫在浅色底上 */\n"
                    ".sym-ico { width: 34px; height: 34px; display: grid; place-items: center; "
                    "background: #ffffff; border: 1px solid var(--line); }\n"
                    ".sym-ico img { width: 24px; height: 24px; }\n"
                    ".sym-txt { display: flex; align-items: baseline; column-gap: 6px; min-width: 0; }\n"
                    ".sym-cell code { background: none; padding: 0; color: var(--text); font-size: 14px; }\n"
                    ".sym-name { font-size: 13px; color: var(--text-dim); }\n"
                    ".sym-cell.is-blank code, .sym-cell.is-blank .sym-name { color: var(--text-faint); }\n"
                    "@media (max-width: 560px) {\n"
                    "  .sym-grid { grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); "
                    "gap: 12px 14px; }\n"
                    "  .sym-txt { flex-direction: column; align-items: flex-start; row-gap: 1px; }\n"
                    "}\n"
                    "</style>\n"),
    ),
    dict(
        file="tocall.html", nav="查询", h1="官方 TOCALL 查询",
        lead="呼号后面那段 TOCALL 是设备或软件的记号。粘一整行报文进来也行，查表就在浏览器里做。",
        title="APRS TOCALL 查询 — 呼号后面那段是什么设备",
        desc="APRS 官方 TOCALL 查询：输入呼号后面那段标识，或直接粘整行报文，在浏览器里对照 APRS.org 官方设备名单，看对面用的是什么设备或软件。",
        og="APRS TOCALL 查询：呼号后面那段是什么设备，粘报文也能查。",
        keywords="TOCALL,TOCALL 查询,APRS 设备识别,APRS 报文头,APDR16,APRS 设备名单,"
                 "aprs-deviceid,APRSDroid,呼号",
        blocks=["tocall", "tocall-refs"],
        scripts=("tocalls.js",),
    ),
    dict(
        file="faq.html", nav="问答", h1="常见问题",
        lead="最常被问到的几件事：为什么别人看不到我、包为什么会重复、网关要不要自己架。",
        title="APRS 常见问题 — 看不到、包重复、网关要不要架",
        desc="APRS 常见问题：没有呼号能不能玩、需要什么设备、别人看不到我、包重复、网关怎么架、能不能只收不发，逐条说清楚。",
        og="APRS 常见问题：看不到、包重复、网关与只收不发，逐条说清楚。",
        keywords="APRS 常见问题,APRS 看不到,APRS 重复包,APRS 只收不发,iGate 搭建,"
                 "APRS 没有呼号,APRS 设备",
        blocks=["faq"],
    ),
    dict(
        file="docs.html", nav="文档", h1="规范与文档",
        lead="中文整理和英文原文分开列：自己重写的几篇在上面，官方 PDF 与规范原文在下面。",
        title="APRS 规范与文档 — APRS101、APRS-IS 实施细则",
        desc="APRS 规范与官方文档入口：APRS101、APRS-IS 规范与实施细则、服务器列表与过滤器语法、TAPR 历年资料（英文原文）。",
        og="APRS 规范与文档：APRS101、APRS-IS 实施细则与官方入口。",
        keywords="APRS101,APRS-IS,APRS 规范,APRS 实施细则,APRS 官方文档,TAPR,"
                 "APRS 服务器列表,过滤器语法",
        blocks=["docs", "refs"],
    ),
    dict(
        file="protocol.html", nav="", nav_active="docs.html",
        h1="APRSNET CN CONNECT v0.1.0（草案）",
        lead="APRSNET CN CONNECT 是一份 APRS 服务器的接入与转发规范：客户端怎么连进来、谁被放行、报文怎么转发出去。这一版是 v0.1.0，还是草案。",
        title="APRSNET CN CONNECT v0.1.0（草案）— 接入与报文转发规范",
        desc="APRSNET CN CONNECT v0.1.0 草案：客户端接入与登录格式、Passcode 与白名单准入、报文处理与转发、APRS-IS 过滤器、安全运维、网络边界与测试要求。",
        og="APRSNET CN CONNECT v0.1.0（草案）：接入、授权、转发与运维要求。",
        keywords="APRSNET CN,APRSNET CN CONNECT,APRS 协议,APRS 服务器,呼号白名单,APRS-IS 接入,Passcode,"
                 "APRS 过滤器,APRS 转发,业余无线电通信协议,BG7LZQ",
        blocks=["proto-head", "proto-general", "proto-arch", "proto-access",
                "proto-whitelist", "proto-forward", "proto-filter", "proto-ops",
                "proto-boundary", "proto-test", "proto-misc"],
        # 这一页正文自己分十章：序号栏就当章号用（总则 = 01 … 附则 = 10），
        # 标题块和「草案说明」不占号，否则 03 和第 N 章会撞车
        extra_head=('<style>\n'
                    "main > section.section:first-child .section-head::before,\n"
                    "main > section#proto-head .section-head::before { content: none; }\n"
                    "main > section#proto-head { counter-reset: chap; }\n"
                    "main > section.section:not(:first-child):not(#proto-head) { counter-increment: chap; }\n"
                    "main > section.section:not(:first-child):not(#proto-head) "
                    ".section-head::before { content: counter(chap, decimal-leading-zero); }\n"
                    "</style>\n"),
    ),
    dict(
        file="aprsis.html", nav="", nav_active="docs.html", h1="APRS-IS 文档（中文）",
        lead="APRS-IS 官方文档的中文整理：怎么连上网络、登录行怎么写、UDP 与只发端口怎么用。过滤器、网关细则和 q 构造各有专篇。",
        title="APRS-IS 中文文档 — 连接、登录行、端口与过滤器",
        desc="APRS-IS 官方文档的中文整理：TCP/UDP 端口怎么选（14501 / 10152 / 14580 / 23000）、登录行与 passcode、呼号与 SSID 规则、只发端口与 HTTP/WebSocket 返回码，附原文入口。",
        og="APRS-IS 中文文档：连上网络、登录行、UDP 与只发端口。",
        keywords="APRS-IS,APRS-IS 连接,APRS-IS 端口,14580,10152,登录行,passcode,"
                 "APRS-IS 文档,只发端口,呼号 SSID,WebSocket",
        blocks=["aprsis-connect", "aprsis-login", "aprsis-send"],
    ),
    dict(
        file="filter.html", nav="", nav_active="docs.html", h1="APRS-IS 过滤器",
        lead="服务器会把哪些包推给你，全看这一行 filter。官方文档里每个条件都在这一页摊开写。",
        title="APRS-IS 过滤器 — filter 命令与条件清单（中文）",
        desc="APRS-IS 服务器端过滤器中文整理：filter 命令怎么写、包含与排除、按位置 / 台站 / 类型 / 符号 / q 构造的每个条件怎么填，附官方原文入口。",
        og="APRS-IS 过滤器中文整理：filter 怎么写，每个条件分别管什么。",
        keywords="APRS-IS 过滤器,APRS filter,14580 过滤器,r/ 范围过滤,b/ 呼号过滤,"
                 "t/ 类型过滤,s/ 符号过滤,q 构造,APRS-IS 中文文档",
        blocks=["filter-basics", "filter-place", "filter-station",
                "filter-type", "filter-symbol", "filter-refs"],
    ),
    dict(
        file="igate.html", nav="", nav_active="docs.html", h1="iGate 细则",
        lead="网关把空中和互联网两头接起来。转什么、不转什么、路径怎么改，官方细则都在这一页。",
        title="iGate 细则 — 网关转什么、不转什么、路径怎么写（中文）",
        desc="APRS-IS 官方的 iGate 细则中文整理：哪些包该送上互联网、哪几类不转，消息什么条件下发回空中，qAR 与 qAO 怎么加，第三方格式怎么拼，附原文入口。",
        og="iGate 细则中文整理：转什么、不转什么、路径和第三方格式怎么写。",
        keywords="iGate,APRS 网关,iGate 细则,qAR,qAO,第三方格式,APRS-IS 网关,"
                 "网关搭建,APRS 中文文档",
        blocks=["igate-duty", "igate-up", "igate-down", "igate-path",
                "igate-query", "igate-refs"],
    ),
    dict(
        file="qconstruct.html", nav="", nav_active="docs.html", h1="q 构造",
        lead="包进了 APRS-IS 之后，末尾会多出一小段 q 开头的标记。这一页把每个标记是谁写的、什么意思讲清。",
        title="q 构造 — APRS-IS 包里那段 q 标记是什么（中文）",
        desc="APRS-IS 的 q 构造中文整理：qAC、qAX、qAU、qAo、qAO、qAS、qAr、qAR、qAZ、qAI 各自是谁写上去的、什么意思，登录名与跟踪的几条规矩，附两个例子与官方原文。",
        og="q 构造中文整理：从 qAC 到 qAI，每个标记是谁写的、什么意思。",
        keywords="q 构造,qAC,qAR,qAO,qAr,APRS-IS q construct,qAS,qAI,"
                 "APRS-IS 中文文档,iGate 转发",
        blocks=["q-what", "q-server", "q-client", "q-rules", "q-examples", "q-refs"],
    ),
    dict(
        file="tools.html", nav="工具", h1="工具与链接",
        lead="APRS 的软件、网站和自制的家伙都收在这一页：手机电脑上的客户端、浏览器里的地图，想自己搭一套时用得上的东西也在。",
        title="APRS 工具与链接 — 客户端、在线地图与自建工具",
        desc="中文 APRS 工具与链接集合：本站的 passcode 计算器与官方 TOCALL 查询、客户端（APRSdroid、APRSLocus、YAAC、APRSIS32、PinPoint APRS）、在线地图与查询（APRS.TV、aprs.fi、findu、CWOP 气象台站、服务器列表）、自己动手（Direwolf、MicroModem、官方设备名单、TAPR），条目还在慢慢收，欢迎补一条。",
        og="APRS 的工具和网站收在一页：passcode 计算器、客户端、在线地图，和自己动手用的东西。",
        keywords="APRS 工具,passcode,APRS-IS passcode,passcode 计算器,APRS 客户端,"
                 "APRS 软件,APRS 地图,APRS 网站,APRSdroid,APRSLocus,YAAC,APRSIS32,"
                 "PinPoint APRS,Direwolf,aprs.fi,APRS.TV,APRS 设备名单",
        blocks=["tools-own", "tools-client", "tools-web", "tools-make",
                "tools-submit"],
    ),
    dict(
        file="passcode.html", nav="", nav_active="tools.html", h1="passcode 计算器",
        lead="连 APRS-IS 的登录行里要填一个 passcode，它只跟呼号有关。填上自己的呼号，当场算出来，复制了就能用。",
        title="APRS-IS passcode 计算器 — 按呼号算登录用的 passcode",
        desc="在浏览器里按呼号算 APRS-IS 的 passcode：填呼号（带不带 SSID 都行）当场得数，一键复制；同时说清 pass -1 只收不发、只算自己的呼号、算法出处，以及登录行怎么写、连哪个端口。",
        og="填上呼号，当场算出 APRS-IS 登录用的 passcode，一键复制。",
        keywords="passcode,APRS passcode,APRS-IS passcode,passcode 计算器,passcode 怎么算,"
                 "登录行,APRS-IS 登录,pass -1,呼号,APRS 工具",
        blocks=["passcode-calc", "passcode-note"],
        scripts=("passcode.js",),
    ),
    dict(
        file="links.html", nav="友链", h1="友链",
        lead="同一条电波上的站，互相留个门牌。",
        title="友链 — APRS 相关的项目与站点",
        desc="APRSNET.CN 的友链：APRSLocus 客户端与它的源码仓库、APRS.TV 实时地图、同行协会的站点，以及换链接的做法。",
        og="APRS 相关的项目与站点，都列在这一页。",
        keywords="友链,APRSNET.CN,APRSLocus,APRS.TV,APRS 客户端,业余无线电协会,APRS 相关站点,链接交换",
        blocks=["links", "links-friends", "links-swap"],
    ),
    dict(
        file="about.html", nav="关于", h1="关于 APRSNET.CN",
        lead="信标不止，回响不息。",
        title="关于 APRSNET.CN",
        desc="APRSNET.CN 是一份中文 APRS 资料站：APRS 是什么、参数与频率怎么设、报文怎么读、官方 TOCALL 查询、APRS-IS 文档与协议规范，由 BG7LZQ 维护，源码与投稿入口都在 GitHub。",
        og="一份中文 APRS 资料站：资料、TOCALL 查询与协议规范都在这里。",
        keywords="APRSNET.CN,关于本站,APRS 中文资料,APRS 资料站,业余无线电,APRS 查询,开源,BG7LZQ",
        blocks=["about-origin", "about-site", "about-upkeep"],
    ),
]

# 手写的独立页：不归生成器管（不在 PAGES 里，也就不会进导航和页脚目录），
# 但地址要出现在 sitemap.xml 里，所以在这儿登记一条 (路径, priority)。
EXTRA_URLS = (
    ("oa", "0.9"),  # APRSNETCN-OA 活动占位页：aprsnet.cn/oa（目录形式，文件是 oa/index.html）
)

# 区块 → 所在页面（跨页链接、页脚链接都靠这张表算，不用手写）
BLOCK_PAGE = {}
for _p in PAGES:
    for _b in _p["blocks"]:
        if _b != "hero":
            BLOCK_PAGE[_b] = _p["file"]

# 页脚目录：三栏，一页一条；每页自己的分节，从那一页里进
FOOT_MAP = (
    ("认识与上手", ("index.html", "guide.html", "tutorial.html",
                    "packet.html", "symbols.html", "tocall.html", "faq.html")),
    ("文档", ("docs.html", "aprsis.html", "filter.html", "igate.html",
              "qconstruct.html", "protocol.html")),
    ("本站", ("tools.html", "passcode.html", "links.html", "about.html")),
)
# 页脚里每页那一行的短名
PAGE_SHORT = {
    "index.html": "认识 APRS", "guide.html": "上手与设备",
    "tutorial.html": "从零上手教程", "packet.html": "报文与字段",
    "symbols.html": "APRS 符号表",
    "tocall.html": "官方 TOCALL 查询", "faq.html": "常见问题",
    "docs.html": "规范与文档", "aprsis.html": "APRS-IS 文档（中文）",
    "filter.html": "APRS-IS 过滤器", "igate.html": "iGate 细则",
    "qconstruct.html": "q 构造", "protocol.html": "APRSNET CN CONNECT（草案）",
    "tools.html": "工具与链接", "passcode.html": "passcode 计算器",
    "links.html": "友链", "about.html": "关于本站",
}

# 页脚最后那栏里的两个外部入口
FOOT_SOURCE = (
    ("https://github.com/dariondong/APRSNET.CN", "源码仓库"),
    ("https://github.com/dariondong/APRSNET.CN/issues", "提个 issue"),
)


def footer_groups():
    """页脚目录：[(栏名, [(href, 文字, 是不是外部链接), ...]), ...]，一页一条。"""
    out = []
    for label, files in FOOT_MAP:
        rows = [(f, PAGE_SHORT[f], False) for f in files]
        if label == "本站":
            rows += [(href, text, True) for href, text in FOOT_SOURCE]
        out.append((label, rows))
    return out

HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="{keywords}">
<meta name="author" content="{author}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta name="msvalidate.01" content="A10C9AF6F0DAC9E3BF451E4DC575DC4E" />
<meta name="theme-color" content="#f2f0ea">
<meta property="og:type" content="website">
<meta property="og:site_name" content="APRSNET.CN">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{og}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="https://aprsnet.cn/assets/icon.png">
<meta property="og:image:width" content="512">
<meta property="og:image:height" content="512">
<meta property="og:image:alt" content="APRSNET.CN">
<meta property="og:locale" content="zh_CN">
<link rel="canonical" href="{url}">
<link rel="icon" type="image/png" href="assets/favicon.png?v={icon_v}">
<link rel="apple-touch-icon" href="assets/icon.png?v={icon_v}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{og}">
<meta name="twitter:image" content="https://aprsnet.cn/assets/icon.png">
<link rel="stylesheet" href="style.css?v={css_v}">
<script type="application/ld+json">{jsonld}</script>
<script>
/* 主题：localStorage 优先，没存过就用浅色（默认皮肤 = 混凝土白）。
   必须在首帧前执行，否则会闪一下深底。 */
(function () {{
  try {{
    var t = localStorage.getItem('theme');
    if (t !== 'dark' && t !== 'light') t = 'light';
    document.documentElement.setAttribute('data-theme', t);
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute('content', t === 'dark' ? '#0b0e11' : '#f2f0ea');
  }} catch (e) {{}}
}})();
</script>
{extra_head}</head>

<body>

<a class="skip-link" href="#main">跳到主要内容</a>
<div class="read-bar" id="readBar" aria-hidden="true"></div>

<header class="nav" id="nav">
  <div class="nav-inner">
    <a class="brand" href="./"><img class="brand-mark" src="assets/icon.png?v={icon_v}" alt="APRSNET.CN" width="64" height="64"></a>
    <nav class="nav-links" id="navLinks" aria-label="页面导航">
{nav}
      <button class="theme-toggle" id="themeToggle" type="button" aria-pressed="false" aria-label="切换深色模式" title="切换深色模式">
        <svg class="ico ico-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/></svg>
        <svg class="ico ico-sun" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-13.5a1 1 0 0 1 1 1V6a1 1 0 0 1-2 0V4.5a1 1 0 0 1 1-1zm0 15a1 1 0 0 1 1 1V21a1 1 0 0 1-2 0v-1.5a1 1 0 0 1 1-1zM3.5 12a1 1 0 0 1 1-1H6a1 1 0 0 1 0 2H4.5a1 1 0 0 1-1-1zm15 0a1 1 0 0 1 1-1h1.5a1 1 0 0 1 0 2H19.5a1 1 0 0 1-1-1zM6.3 6.3a1 1 0 0 1 1.4 0l1 1a1 1 0 0 1-1.4 1.4l-1-1a1 1 0 0 1 0-1.4zm9 9a1 1 0 0 1 1.4 0l1 1a1 1 0 0 1-1.4 1.4l-1-1a1 1 0 0 1 0-1.4zm1.4-9a1 1 0 0 1 0 1.4l-1 1a1 1 0 0 1-1.4-1.4l1-1a1 1 0 0 1 1.4 0zm-9 9a1 1 0 0 1 0 1.4l-1 1a1 1 0 0 1-1.4-1.4l1-1a1 1 0 0 1 1.4 0z"/></svg>
      </button>
      <a class="nav-brand-aside" href="about.html" aria-label="关于本站">
        <img src="assets/icon.png?v={icon_v}" alt="" width="64" height="64">
        <span>信标不止，回响不息。</span>
      </a>
    </nav>
    <button class="nav-burger" id="navBurger" type="button" aria-label="菜单" aria-expanded="false" aria-controls="navLinks"><span></span><span></span><span></span></button>
  </div>
</header>

<main id="main">
"""

INTRO = """  <section class="section">
    <div class="section-head">
      <h1>{h1}</h1>
      <p>{lead}</p>
    </div>
  </section>
"""

FOOT = """
</main>

<footer class="footer">
  <div class="footer-inner">
    <div class="footer-brand">
      <b><span class="wm-aprs">APRS</span><span class="wm-net">NET</span><span class="wm-cn">.CN</span></b>
      <span>信标不止，回响不息。</span>
    </div>
    <nav class="footer-index" aria-label="站内目录">
{footer_links}
    </nav>
    <p class="footer-note">本站由业余无线电爱好者维护，与任何厂商、组织和项目都没有隶属关系。请遵守当地无线电管理法规，只在获得许可的频率上发射。</p>
    <p class="footer-edit">发现写错的、漏掉的？<a href="https://github.com/dariondong/APRSNET.CN/edit/main/{file}" target="_blank" rel="noopener">在 GitHub 上改这一页</a>。</p>
    <p class="footer-copy">© <span id="year">2026</span> {author}</p>
  </div>
</footer>

<a class="backtop" id="backTop" href="#main" aria-label="回到顶部">
  <svg class="backtop-ring" viewBox="0 0 40 40" aria-hidden="true"><circle class="ring-bg" cx="20" cy="20" r="17"/><circle class="ring-fg" id="ringFg" cx="20" cy="20" r="17"/></svg>
  <svg class="backtop-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5l7 7-1.4 1.4L13 8.8V20h-2V8.8l-4.6 4.6L5 12l7-7z"/></svg>
</a>
"""

TAGS = re.compile(r"<[^>]+>")
ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}
DETAILS = re.compile(r"<details[^>]*>\s*<summary[^>]*>(.*?)</summary>(.*?)</details>", re.S)


def page_url(page):
    return SITE if page["file"] == "index.html" else SITE + page["file"]


def read_part(name):
    path = PARTS / (name + ".html")
    if not path.exists():
        sys.exit("FAIL: 缺区块文件 %s" % path)
    return path.read_text(encoding="utf-8").rstrip("\n")


def plain(html):
    text = TAGS.sub(" ", html)
    for k, v in ENT.items():
        text = text.replace(k, v)
    return re.sub(r"\s+", " ", text).strip()


def faq_items():
    """把 faq 区块里的 <details> 抽成 schema.org 的问答（内容照抄，不另编）。"""
    items = []
    for q, a in DETAILS.findall(read_part("faq")):
        items.append({
            "@type": "Question",
            "name": plain(q),
            "acceptedAnswer": {"@type": "Answer", "text": plain(a)},
        })
    return items


def build_jsonld(page):
    node = {
        "@context": "https://schema.org",
        "@type": ["WebPage", "FAQPage"] if page["file"] == "faq.html" else "WebPage",
        "name": page["title"],
        "url": page_url(page),
        "inLanguage": "zh-CN",
        "description": page["desc"],
        "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": SITE},
        "publisher": {"@type": "Organization", "name": SITE_NAME, "url": SITE},
        "license": "https://creativecommons.org/licenses/by-sa/4.0/",
    }
    if page["file"] == "index.html":
        node["@type"] = "WebSite"
        node["name"] = SITE_NAME
        node.pop("isPartOf", None)
        node["hasPart"] = [
            {"@type": "WebPage", "name": p["title"].split(" — ")[0], "url": page_url(p)}
            for p in PAGES if p["file"] != "index.html"
        ]
    if page["file"] == "faq.html":
        node["mainEntity"] = faq_items()
    return json.dumps(node, ensure_ascii=False, separators=(",", ":"))


def set_band(block, alt):
    """区块底色按它在本页的位置交替（alt/plain 严格交替是这套版式的规矩）。"""
    want = "section alt" if alt else "section"
    out, n = re.subn(r'(<section\b[^>]*\bclass=")([^"]*)(")',
                     lambda m: m.group(1) + want + m.group(3), block, count=1)
    if n != 1:
        sys.exit("FAIL: 区块没有 class 可以替换：%s" % block[:60])
    return out


def relink(block, page_file):
    """区块里的 #锚点：搬了页的改成 <页面>#锚点，还在本页的留原样。"""
    def sub(m):
        anchor = m.group(1)
        owner = BLOCK_PAGE.get(anchor)
        if not owner or owner == page_file or anchor == "main":
            return m.group(0)
        return 'href="%s#%s"' % (owner, anchor)
    return re.sub(r'href="#([\w-]+)"', sub, block)


def render(page):
    file = page["file"]
    # nav_active：不在导航里的子页（如 aprsis.html）也能把归属的那一项点亮
    def nav_state(p):
        if p["file"] == file:
            return ' class="active" aria-current="page"'
        if p["file"] == page.get("nav_active"):
            return ' class="active"'
        return ""

    nav = "\n".join(
        '      <a href="%s"%s>%s</a>' % (
            "./" if p["file"] == "index.html" else p["file"], nav_state(p), p["nav"])
        for p in PAGES if p.get("nav"))
    # 页脚目录：三栏，一页一条；每页自己的分节从那一页里进
    groups = []
    for label, rows in footer_groups():
        items = "\n".join(
            '          <li><a href="%s"%s>%s</a></li>' % (
                href,
                ' target="_blank" rel="noopener"' if ext else
                (' aria-current="page"' if href == file else ""),
                text)
            for href, text, ext in rows)
        groups.append('      <div class="footer-group">\n        <b>%s</b>\n        <ul>\n%s\n        </ul>\n      </div>'
                      % (label, items))
    footer_links = "\n".join(groups)

    body = [read_part(b) for b in page["blocks"]]
    if "hero" not in page["blocks"]:
        body.insert(0, INTRO.format(h1=page["h1"], lead=page["lead"]).rstrip("\n"))

    # 编号和底色都按“本页第几个 section”算，hero 不占位
    out, sec_no = [], 0
    for chunk in body:
        if re.match(r'^  <section\b[^>]*\bclass="section\b', chunk):
            chunk = set_band(chunk, sec_no % 2 == 1)
            sec_no += 1
        out.append(relink(chunk, file))

    scripts = "".join('\n<script src="%s?v=%s"></script>' % (s, SCRIPT_V[s])
                      for s in page.get("scripts", ()))
    return (HEAD.format(title=page["title"], desc=page["desc"], og=page["og"],
                        keywords=page["keywords"], author=AUTHOR, url=page_url(page),
                        jsonld=build_jsonld(page), css_v=CSS_V, icon_v=ICON_V, nav=nav,
                        extra_head=page.get("extra_head", ""))
            + "\n\n".join(out) + "\n"
            + FOOT.format(footer_links=footer_links, file=file, author=AUTHOR)
            + scripts
            + '\n<script src="main.js?v=%s"></script>\n</body>\n</html>\n' % MAIN_V)


def lastmod():
    """sitemap 的 lastmod：生成当天（= 内容最后一次变动的日子，跑生成器就是动过）。

    不用 git log 取日期：新提交的日期在生成时还不存在，取出来永远慢一拍。
    """
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def sitemap_row(loc, stamp, priority):
    return ("  <url>\n"
            "    <loc>%s</loc>\n"
            "    <lastmod>%s</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n"
            "    <priority>%s</priority>\n"
            "  </url>" % (loc, stamp, priority))


def sitemap_xml():
    stamp = lastmod()
    rows = [sitemap_row(page_url(p), stamp,
                        "1.0" if p["file"] == "index.html" else "0.8")
            for p in PAGES]
    rows += [sitemap_row(SITE + name, stamp, priority) for name, priority in EXTRA_URLS]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


def robots_txt():
    return ("User-agent: *\n"
            "Allow: /\n"
            "\n"
            "Sitemap: %ssitemap.xml\n" % SITE)


def build():
    out = {}
    for page in PAGES:
        out[page["file"]] = render(page)
    out["sitemap.xml"] = sitemap_xml()
    out["robots.txt"] = robots_txt()
    return out


def norm(t):
    """把区块底色类和跨页链接还原，用于「区块有没有原样进页」的比对。"""
    t = re.sub(r'(<section\b[^>]*\bclass=")[^"]*(")', r"\1X\2", t)
    return re.sub(r'href="[^"#]*#([\w-]+)"', r'href="#\1"', t)


def selfcheck(files):
    # 每页里的区块必须和 tool/parts 里那片一字不差（除了底色类和跨页链接）
    lost = []
    for page in PAGES:
        text = norm(files[page["file"]])
        for b in page["blocks"]:
            if norm(read_part(b)) not in text:
                lost.append("%s ← %s" % (page["file"], b))
    if lost:
        sys.exit("FAIL: 有区块没能原样进页：%s" % ", ".join(lost))

    # 同一页里不能有两个相同 id；跨页链接指向的页面与锚点必须真的存在
    by_file = dict((p["file"], p) for p in PAGES)
    for page in PAGES:
        text = files[page["file"]]
        ids = re.findall(r'\bid="([\w-]+)"', text)
        dup = sorted(set(i for i in ids if ids.count(i) > 1))
        if dup:
            sys.exit("FAIL: %s 里有重复 id：%s" % (page["file"], dup))
        if not re.search(r"<h1[ >]", text):
            sys.exit("FAIL: %s 没有 h1" % page["file"])
        if text.count("<h1") != 1:
            sys.exit("FAIL: %s 的 h1 不止一个" % page["file"])
        # 标题不能重样：子页的 h2 跟自己的 h1 撞名过一次（常见问题 / 官方 TOCALL 查询）
        heads = [re.sub(r"<[^>]+>", "", s).strip()
                 for s in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", text, re.S)]
        repeat = sorted(set(h for h in heads if heads.count(h) > 1))
        if repeat:
            sys.exit("FAIL: %s 里有重名的标题：%s" % (page["file"], repeat))
        for target, anchor in re.findall(r'href="([\w-]+\.html)#([\w-]+)"', text):
            if target not in by_file:
                sys.exit("FAIL: %s 链到了不存在的页面 %s" % (page["file"], target))
            if anchor not in re.findall(r'\bid="([\w-]+)"', files[target]):
                sys.exit("FAIL: %s 链到了 %s 里不存在的错点 #%s" %
                         (page["file"], target, anchor))

    # 结构化数据得是能解析的 JSON，sitemap 得是能解析的 XML
    for page in PAGES:
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                      files[page["file"]], re.S)
        if not m:
            sys.exit("FAIL: %s 缺 JSON-LD" % page["file"])
        try:
            data = json.loads(m.group(1))
        except ValueError as e:
            sys.exit("FAIL: %s 的 JSON-LD 不是合法 JSON：%s" % (page["file"], e))
        if data.get("url") != page_url(page) or not data.get("description"):
            sys.exit("FAIL: %s 的 JSON-LD 缺 url/description" % page["file"])
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(files["sitemap.xml"])
    except ET.ParseError as e:
        sys.exit("FAIL: sitemap.xml 不是合法 XML：%s" % e)
    locs = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    want = sorted([page_url(p) for p in PAGES] + [SITE + n for n, _p in EXTRA_URLS])
    if sorted(locs) != want:
        sys.exit("FAIL: sitemap 里的地址和页面表对不上：%s" % locs)

    # 页脚目录：页面表里的每一页都得在里面出现一次
    listed = [f for _label, files in FOOT_MAP for f in files]
    if sorted(listed) != sorted(p["file"] for p in PAGES):
        sys.exit("FAIL: 页脚目录和页面表对不上：%s" % listed)


def no_stamp(t):
    """比对 sitemap 时把 lastmod 抹掉：跨天跑 --check 不该报错。"""
    return re.sub(r"<lastmod>[^<]*</lastmod>", "<lastmod>X</lastmod>", t)


def main():
    check = "--check" in sys.argv
    files = build()
    selfcheck(files)

    bad = 0
    for name in sorted(files):
        path = REPO / name
        old = path.read_text(encoding="utf-8") if path.exists() else None
        text = files[name]
        if name == "sitemap.xml" and old is not None and no_stamp(old) == no_stamp(text):
            print("same:  %-12s （只有 lastmod 日期不同）" % name)
            continue
        if old == text:
            print("same:  %-12s %6d 字节" % (name, len(text.encode("utf-8"))))
        elif check:
            bad += 1
            print("DIFF:  %-12s 磁盘 %s / 生成 %d 字节" % (
                name, len(old.encode("utf-8")) if old else "-", len(text.encode("utf-8"))))
        else:
            path.write_text(text, encoding="utf-8")
            print("write: %-12s %6d 字节 %s" % (
                name, len(text.encode("utf-8")), "(新建)" if old is None else ""))

    if check and bad:
        sys.exit("%d 个文件与生成结果不一致，跑一遍 python3 tool/gen_pages.py" % bad)
    print("OK: %d 个文件，区块原样进页、h1 唯一、跨页链接能落地、JSON-LD 与 sitemap 合法"
          % len(files))


if __name__ == "__main__":
    main()
