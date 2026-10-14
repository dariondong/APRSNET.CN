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
      if (meta) meta.setAttribute("content", next === "dark" ? "#0a0e12" : "#f4f7fc");
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

  /* ── 首屏入场：等字体与样式就位后再亮起来 ── */
  var markLoaded = function () { document.body.classList.add("loaded"); };
  if (document.readyState === "complete") markLoaded();
  else window.addEventListener("load", markLoaded);

  /* ── 页脚年份 ── */
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
