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

  /* ── 型号核准代码：本机校验 + 复制（不发请求、不存数据） ── */
  var idForm = document.getElementById("cmiitForm");
  if (idForm) {
    var idInput = document.getElementById("cmiitId");
    var idOut = document.getElementById("cmiitOut");
    var idSay = function (msg, kind) {
      idOut.textContent = msg;
      idOut.className = kind ? "idq-out " + kind : "idq-out";
    };
    idForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var raw = String(idInput.value || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
      if (raw.indexOf("CMIITID") === 0) raw = raw.slice(7);
      if (!raw) { idSay("先把手上的代码填进去。", "bad"); return; }
      var m = /^(\d{4})([A-Z]{2})(\d{2,6})$/.exec(raw);
      if (!m) {
        idSay("这不像型号核准代码：它是「4 位年份 + 两个字母 + 几位数字」，例如 2023DJ1234。", "bad");
        return;
      }
      idInput.value = raw;
      var parts = m[1] + " 年核准、" + m[2] + " 类别、编号 " + m[3];
      var good = function () {
        idSay("格式看着对：" + parts + "。已经复制，到下面的官方入口粘贴查询。", "good");
      };
      var manual = function () {
        idSay("格式看着对：" + parts + "。复制没成功，手动选中输入框里的代码，再去官方入口查询。", "good");
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(raw).then(good, manual);
      } else {
        manual();
      }
    });
    var idReset = idForm.querySelector("button[type=reset]");
    if (idReset) idReset.addEventListener("click", function () { idSay(""); });
  }

  /* ── 首屏入场：等字体与样式就位后再亮起来 ── */
  var markLoaded = function () { document.body.classList.add("loaded"); };
  if (document.readyState === "complete") markLoaded();
  else window.addEventListener("load", markLoaded);

  /* ── 页脚年份 ── */
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
