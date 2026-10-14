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

  /* ── 动效开关：系统说要减少动效时，把 SVG 里的 SMIL 动画停住 ── */
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var still = !!(mq && mq.matches);
  if (still) {
    Array.prototype.forEach.call(document.querySelectorAll("svg"), function (s) {
      if (s.pauseAnimations) s.pauseAnimations();
    });
  }

  /* ── 滚动揭示：脚本自己给条目挂 .reveal，进视野时再入场
        （挂 class 的动作在脚本里，脚本不在时内容照常可见） ── */
  if (!still && "IntersectionObserver" in window) {
    var items = document.querySelectorAll(".section-head, .fact-list li, .link-list li, .steps li, .glossary dt, .glossary dd, .faq details");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("in");
        io.unobserve(e.target);
      });
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.06 });
    Array.prototype.forEach.call(items, function (el) {
      var sibs = el.parentNode ? el.parentNode.children : null;
      var i = sibs ? Array.prototype.indexOf.call(sibs, el) : 0;
      el.style.setProperty("--d", Math.min(i, 6) * 55 + "ms");
      el.classList.add("reveal");
      io.observe(el);
    });
  }

  /* ── 首屏动效：蓝图网格与面板图随滚动错开，面板图再随鼠标轻微倾斜
        （transform 只在这一处合成，免得多处互相覆盖） ── */
  var heroGrid = document.querySelector(".hero-grid");
  var heroFig = document.querySelector(".hero-panel-svg");
  if (!still && (heroGrid || heroFig)) {
    var wide = window.matchMedia ? window.matchMedia("(min-width: 1000px)") : null;
    var canTilt = !!(wide && wide.matches && !("ontouchstart" in window));
    var parY = 0, tiltX = 0, tiltY = 0, waiting = false;
    var paint = function () {
      waiting = false;
      if (heroGrid) heroGrid.style.transform = "translate3d(0," + (parY * 0.10).toFixed(1) + "px,0)";
      if (!heroFig) return;
      var t = "translate3d(0," + (parY * -0.075).toFixed(1) + "px,0)";
      if (canTilt) t = "perspective(1000px) " + t + " rotateY(" + tiltX.toFixed(2) + "deg) rotateX(" + tiltY.toFixed(2) + "deg)";
      heroFig.style.transform = t;
    };
    var schedule = function () {
      if (waiting) return;
      waiting = true;
      if (window.requestAnimationFrame) window.requestAnimationFrame(paint); else paint();
    };
    window.addEventListener("scroll", function () {
      parY = Math.min(window.scrollY || 0, (window.innerHeight || 1) * 1.2);
      schedule();
    }, { passive: true });
    if (canTilt) {
      document.addEventListener("pointermove", function (e) {
        var r = heroFig.getBoundingClientRect();
        if (!r.width) return;
        var nx = (e.clientX - (r.left + r.width / 2)) / ((window.innerWidth || 1) / 2);
        var ny = (e.clientY - (r.top + r.height / 2)) / ((window.innerHeight || 1) / 2);
        tiltX = Math.max(-1, Math.min(1, nx)) * 4.5;
        tiltY = Math.max(-1, Math.min(1, ny)) * -3.5;
        schedule();
      }, { passive: true });
    }
    paint();
  }

  /* ── 首屏入场：等字体与样式就位后再亮起来 ── */
  var markLoaded = function () { document.body.classList.add("loaded"); };
  if (document.readyState === "complete") markLoaded();
  else window.addEventListener("load", markLoaded);

  /* ── 页脚年份 ── */
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
