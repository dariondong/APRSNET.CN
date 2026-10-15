/* APRSNET.CN 首页脚本：导航滚动状态 / 主题切换 / 入场标记 / 页脚年份。
   初始主题由 <head> 内联脚本在首帧前设定，这里只负责按钮与后续切换。 */
(function () {
  "use strict";

  /* ── 导航：滚过一屏顶部就收窄 ── */
  var nav = document.getElementById("nav");
  if (nav) {
    var syncNav = function () {
      nav.classList.toggle("scrolled", window.scrollY > 24);
    };
    syncNav();
    window.addEventListener("scroll", syncNav, { passive: true });
  }

  /* ── 窄屏抽屉：汉堡开合（点条目 / 按 Esc 直接关） ── */
  var burger = document.getElementById("navBurger");
  var navLinks = document.getElementById("navLinks");
  if (burger && navLinks) {
    var setMenu = function (open) {
      burger.classList.toggle("open", open);
      navLinks.classList.toggle("open", open);
      burger.setAttribute("aria-expanded", String(open));
      document.body.style.overflow = open ? "hidden" : "";
    };
    burger.addEventListener("click", function () {
      setMenu(!navLinks.classList.contains("open"));
    });
    Array.prototype.forEach.call(navLinks.querySelectorAll("a"), function (a) {
      a.addEventListener("click", function () { setMenu(false); });
    });
    document.addEventListener("keydown", function (e) {
      if ((e.key === "Escape" || e.key === "Esc") && navLinks.classList.contains("open")) {
        setMenu(false);
        burger.focus();
      }
    });
  }

  /* ── 回到顶部 + 进度环 ── */
  var backTop = document.getElementById("backTop");
  var ringFg = document.getElementById("ringFg");
  if (backTop) {
    var CIRC = 106.8; // 半径 17 的周长
    var onScroll = function () {
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(window.scrollY / max, 1) : 0;
      backTop.classList.toggle("show", window.scrollY > 600);
      if (ringFg) ringFg.style.strokeDashoffset = (CIRC * (1 - p)).toFixed(1);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
  }

  /* ── 顶部阅读进度：一条橙色细线，随页面滚到底而填满 ── */
  var readBar = document.getElementById("readBar");
  if (readBar) {
    var syncBar = function () {
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(Math.max(window.scrollY / max, 0), 1) : 0;
      readBar.style.transform = "scaleX(" + p.toFixed(4) + ")";
    };
    syncBar();
    window.addEventListener("scroll", syncBar, { passive: true });
    window.addEventListener("resize", syncBar);
  }

  /* ── 主题切换（浅 / 深），偏好存 localStorage ── */
  var themeBtn = document.getElementById("themeToggle");
  if (themeBtn) {
    var syncTheme = function () {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      themeBtn.setAttribute("aria-pressed", String(dark));
      var label = dark ? "切换浅色模式" : "切换深色模式";
      themeBtn.setAttribute("aria-label", label);
      themeBtn.setAttribute("title", label);
    };
    syncTheme();
    themeBtn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", next === "dark" ? "#0b0e11" : "#f2f0ea");
      syncTheme();
    });
  }

  /* ── 导航高亮（Scrollspy）：当前区块对应的锚点加 .active ── */
  var sections = Array.prototype.slice.call(document.querySelectorAll("section[id]"));
  var navAnchors = Array.prototype.slice.call(document.querySelectorAll(".nav-links a[href^='#']"));
  if ("IntersectionObserver" in window && sections.length && navAnchors.length) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        navAnchors.forEach(function (a) {
          a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id);
        });
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    sections.forEach(function (s) { spy.observe(s); });
  }

  /* ── 动效开关：系统说要减少动效时，连那层淡入也不放 ── */
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var still = !!(mq && mq.matches);

  /* ── 滚动揭示：脚本自己给条目挂 .reveal，进视野时淡入
        （挂 class 的动作在脚本里，脚本不在时内容照常可见） ── */
  if (!still && "IntersectionObserver" in window) {
    var items = document.querySelectorAll(".section-head, .fact-list li, .link-list li, .pkt-list li, .steps li, .glossary dt, .glossary dd, .faq details");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("in");
        io.unobserve(e.target);
      });
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.06 });
    Array.prototype.forEach.call(items, function (el) {
      el.classList.add("reveal");
      io.observe(el);
    });
  }

  /* ── TOCALL 查询：只查随站点发布的官方名单，不发请求 ── */
  var tlqForm = document.getElementById("tlqForm");
  if (tlqForm) {
    var tlqInput = document.getElementById("tlqInput");
    var tlqOut = document.getElementById("tlqOut");
    var tlqCount = document.getElementById("tlqCount");
    var TLQ = window.APRS_TOCALLS || {};
    var TLQ_KEYS = Object.keys(TLQ);
    if (tlqCount) tlqCount.textContent = String(TLQ_KEYS.length);

    var tlqSay = function (msg, kind) {
      tlqOut.textContent = msg;
      tlqOut.className = kind ? "idq-out " + kind : "idq-out";
    };
    /* 整行报文也行：取「呼号>」之后的第一个字段 */
    var pickTocall = function (raw) {
      var s = String(raw || "").toUpperCase();
      var i = s.indexOf(">");
      if (i >= 0) s = s.slice(i + 1);
      s = s.split(",")[0].split(":")[0];
      return s.replace(/[^0-9A-Z?]/g, "");
    };
    /* 按位比：官方名单里的 ? 表示该位可以是任意字符 */
    var matchKey = function (key, tc) {
      if (key.length !== tc.length) return false;
      for (var i = 0; i < key.length; i++) {
        var c = key.charAt(i);
        if (c !== "?" && c !== tc.charAt(i)) return false;
      }
      return true;
    };
    var tlqLookup = function (tc) {
      if (TLQ[tc]) return { key: tc, val: TLQ[tc], exact: true };
      for (var i = 0; i < TLQ_KEYS.length; i++) {
        if (TLQ_KEYS[i].indexOf("?") >= 0 && matchKey(TLQ_KEYS[i], tc)) {
          return { key: TLQ_KEYS[i], val: TLQ[TLQ_KEYS[i]], exact: false };
        }
      }
      return null;
    };
    var tlqNear = function (tc) {
      var head = tc.replace(/\?/g, "").slice(0, 4), out = [];
      if (head.length < 3) return out;
      for (var i = 0; i < TLQ_KEYS.length && out.length < 3; i++) {
        if (TLQ_KEYS[i].replace(/\?/g, "").indexOf(head) === 0) out.push(TLQ_KEYS[i]);
      }
      return out;
    };
    tlqForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var tc = pickTocall(tlqInput.value);
      if (!tc) { tlqSay("先填一个 TOCALL，或者把整行报文粘进来。", "bad"); return; }
      var hit = tlqLookup(tc);
      if (hit) {
        tlqInput.value = tc;
        tlqSay(tc + " → " + hit.val + (hit.exact ? "" : "（按官方条目 " + hit.key + " 匹配）"), "good");
        return;
      }
      var near = tlqNear(tc);
      tlqSay("名单里没有 " + tc + "：可能是抄错了一两位，也可能是自制程序或还没登记的软件。"
        + (near.length ? "同开头的还有：" + near.join("、") + "。" : "")
        + "官方名单在下面的链接里。", "bad");
    });
    var tlqReset = tlqForm.querySelector("button[type=reset]");
    if (tlqReset) tlqReset.addEventListener("click", function () { tlqSay(""); });
  }

  /* ── 首屏入场：等字体与样式就位后再亮起来 ── */
  var markLoaded = function () { document.body.classList.add("loaded"); };
  if (document.readyState === "complete") markLoaded();
  else window.addEventListener("load", markLoaded);

  /* ── 页脚年份 ── */
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
