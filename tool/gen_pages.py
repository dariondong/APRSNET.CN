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
tocalls.js、hero-field.js）都不归它管，也不要去搬。
"""
import datetime
import json
import re
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
PARTS = HERE / "parts"

CSS_V = "25"        # style.css?v=
MAIN_V = "10"       # main.js?v=
SCRIPT_V = {"tocalls.js": "1", "hero-field.js": "10"}  # 页内脚本的缓存键

SITE = "https://aprsnet.cn/"
SITE_NAME = "APRSNET.CN"
AUTHOR = "BG7LZQ (Darion)"

# ── 页面表：file / 导航名 / 标题 / 描述 / 关键词 / 区块顺序 ──
# blocks 里的名字 = tool/parts/<名字>.html；hero 只在首页。
PAGES = [
    dict(
        file="index.html", nav="认识",
        title="APRSNET.CN — APRS 中文资料站",
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
        file="tocall.html", nav="查询", h1="官方 TOCALL 查询",
        lead="呼号后面那段 TOCALL 是设备或软件的记号。粘一整行报文进来也行，查表就在浏览器里做。",
        title="APRS TOCALL 查询 — 呼号后面那段是什么设备",
        desc="APRS 官方 TOCALL 查询：输入呼号后面那段标识，或直接粘整行报文，在浏览器里对照 APRS.org 官方设备名单，看对面用的是什么设备或软件。",
        og="APRS TOCALL 查询：呼号后面那段是什么设备，粘报文也能查。",
        keywords="TOCALL,TOCALL 查询,APRS 设备识别,APRS 报文头,APDR16,APRS 设备名单,"
                 "aprs-deviceid,APRSDroid,呼号",
        blocks=["tocall", "tocall-refs"],
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
        file="protocol.html", nav="协议规范", h1="APRSNET CN 通信协议规范",
        lead="面向国内 APRS 场景的接入与报文转发规范，v1.0 草案：客户端怎么连进来、谁被放行、报文怎么转发出去。",
        title="APRSNET CN 通信协议规范 v1.0（草案）",
        desc="APRSNET CN 通信协议规范 v1.0 草案：客户端接入与登录格式、Passcode 与白名单准入、报文处理与转发、APRS-IS 过滤器、安全运维、网络边界与测试要求。",
        og="APRSNET CN 通信协议规范 v1.0（草案）：接入、授权、转发与运维要求。",
        keywords="APRSNET CN,APRS 协议,APRS 服务器,呼号白名单,APRS-IS 接入,Passcode,"
                 "APRS 过滤器,APRS 转发,业余无线电通信协议,BG7LZQ",
        blocks=["proto-head", "proto-general", "proto-arch", "proto-access",
                "proto-whitelist", "proto-forward", "proto-filter", "proto-ops",
                "proto-boundary", "proto-test", "proto-misc"],
    ),
    dict(
        file="aprsis.html", nav="", nav_active="docs.html", h1="APRS-IS 文档（中文）",
        lead="APRS-IS 官方文档的中文整理：怎么连上网络、登录行怎么写、UDP 与只发端口怎么用；后面的过滤器、网关、q 构造接着补。",
        title="APRS-IS 中文文档 — 连接、登录行、端口与过滤器",
        desc="APRS-IS 官方文档的中文整理：TCP/UDP 端口怎么选（14501 / 10152 / 14580 / 23000）、登录行与 passcode、呼号与 SSID 规则、只发端口与 HTTP/WebSocket 返回码，附原文入口。",
        og="APRS-IS 中文文档：连上网络、登录行、UDP 与只发端口。",
        keywords="APRS-IS,APRS-IS 连接,APRS-IS 端口,14580,10152,登录行,passcode,"
                 "APRS-IS 文档,只发端口,呼号 SSID,WebSocket",
        blocks=["aprsis-connect", "aprsis-login", "aprsis-send"],
    ),
]

# 区块 → 所在页面（跨页链接、页脚链接都靠这张表算，不用手写）
BLOCK_PAGE = {}
for _p in PAGES:
    for _b in _p["blocks"]:
        if _b != "hero":
            BLOCK_PAGE[_b] = _p["file"]

EXTRA_TITLE = {
    "about": "认识 APRS", "start": "常用工具", "steps": "上手步骤",
    "freq": "频率与路径", "packets": "报文长什么样", "fields": "报文字段",
    "glossary": "名词小抄", "gear": "设备与链路", "faq": "常见问题",
    "tocall": "官方 TOCALL 查询", "tocall-refs": "名单与出处",
    "docs": "中文文档", "refs": "英文原文",
    "tut-listen": "先在手机上听", "tut-send": "接线发出第一个包", "tut-trouble": "没上去怎么查",
    "aprsis-connect": "连上 APRS-IS", "aprsis-login": "登录行怎么写",
    "aprsis-send": "UDP 与只发端口", "contribute": "参与贡献",
    "proto-head": "协议说明", "proto-general": "总则", "proto-arch": "系统组成",
    "proto-access": "接入规范", "proto-whitelist": "白名单", "proto-forward": "报文转发",
    "proto-filter": "过滤器", "proto-ops": "安全与运维", "proto-boundary": "网络边界",
    "proto-test": "测试与版本", "proto-misc": "附则",
}

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
<link rel="icon" type="image/png" href="assets/favicon.png">
<link rel="apple-touch-icon" href="assets/icon.png">
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
</head>

<body>

<a class="skip-link" href="#main">跳到主要内容</a>
<div class="read-bar" id="readBar" aria-hidden="true"></div>

<header class="nav" id="nav">
  <div class="nav-inner">
    <a class="brand" href="./">
      <span class="brand-mark" aria-hidden="true"></span>
      <span class="brand-name">APRSNET.CN</span>
    </a>
    <nav class="nav-links" id="navLinks" aria-label="页面导航">
{nav}
      <button class="theme-toggle" id="themeToggle" type="button" aria-pressed="false" aria-label="切换深色模式" title="切换深色模式">
        <svg class="ico ico-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/></svg>
        <svg class="ico ico-sun" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-13.5a1 1 0 0 1 1 1V6a1 1 0 0 1-2 0V4.5a1 1 0 0 1 1-1zm0 15a1 1 0 0 1 1 1V21a1 1 0 0 1-2 0v-1.5a1 1 0 0 1 1-1zM3.5 12a1 1 0 0 1 1-1H6a1 1 0 0 1 0 2H4.5a1 1 0 0 1-1-1zm15 0a1 1 0 0 1 1-1h1.5a1 1 0 0 1 0 2H19.5a1 1 0 0 1-1-1zM6.3 6.3a1 1 0 0 1 1.4 0l1 1a1 1 0 0 1-1.4 1.4l-1-1a1 1 0 0 1 0-1.4zm9 9a1 1 0 0 1 1.4 0l1 1a1 1 0 0 1-1.4 1.4l-1-1a1 1 0 0 1 0-1.4zm1.4-9a1 1 0 0 1 0 1.4l-1 1a1 1 0 0 1-1.4-1.4l1-1a1 1 0 0 1 1.4 0zm-9 9a1 1 0 0 1 0 1.4l-1 1a1 1 0 0 1-1.4-1.4l1-1a1 1 0 0 1 1.4 0z"/></svg>
      </button>
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
    <div class="footer-top">
      <div class="footer-brand">
        <b>APRSNET.CN</b>
        <span>业余无线电 · APRS 中文资料站</span>
      </div>
      <nav class="footer-links" aria-label="相关链接">
{footer_links}
        <a href="https://github.com/dariondong/APRSNET.CN" target="_blank" rel="noopener">本站源码</a>
      </nav>
    </div>
    <p class="footer-note">本站由业余无线电爱好者维护，与任何厂商、组织和项目都没有隶属关系。请遵守当地无线电管理法规，只在获得许可的频率上发射。</p>
    <p class="footer-edit">发现写错的、漏掉的？<a href="https://github.com/dariondong/APRSNET.CN/edit/main/{file}" target="_blank" rel="noopener">在 GitHub 上改这一页</a>，或者<a href="https://github.com/dariondong/APRSNET.CN/issues" target="_blank" rel="noopener">提个 issue</a>。</p>
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
        node["alternateName"] = "APRS 中文资料站"
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
    footer_links = "\n".join(
        '        <a href="%s#%s">%s</a>' % (BLOCK_PAGE[b], b, EXTRA_TITLE[b])
        if BLOCK_PAGE[b] != file else '        <a href="#%s">%s</a>' % (b, EXTRA_TITLE[b])
        for b in EXTRA_TITLE)

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
                        jsonld=build_jsonld(page), css_v=CSS_V, nav=nav)
            + "\n\n".join(out) + "\n"
            + FOOT.format(footer_links=footer_links, file=file, author=AUTHOR)
            + scripts
            + '\n<script src="main.js?v=%s"></script>\n</body>\n</html>\n' % MAIN_V)


def lastmod():
    """sitemap 的 lastmod：生成当天（= 内容最后一次变动的日子，跑生成器就是动过）。

    不用 git log 取日期：新提交的日期在生成时还不存在，取出来永远慢一拍。
    """
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def sitemap_xml():
    stamp = lastmod()
    rows = []
    for p in PAGES:
        rows.append("  <url>\n"
                    "    <loc>%s</loc>\n"
                    "    <lastmod>%s</lastmod>\n"
                    "    <changefreq>weekly</changefreq>\n"
                    "    <priority>%s</priority>\n"
                    "  </url>" % (page_url(p), stamp, "1.0" if p["file"] == "index.html" else "0.8"))
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
    if sorted(locs) != sorted(page_url(p) for p in PAGES):
        sys.exit("FAIL: sitemap 里的地址和页面表对不上：%s" % locs)


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
