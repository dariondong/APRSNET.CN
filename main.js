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

  /* ── 首屏入场：等字体与样式就位后再亮起来 ── */
  var markLoaded = function () { document.body.classList.add("loaded"); };
  if (document.readyState === "complete") markLoaded();
  else window.addEventListener("load", markLoaded);

  /* ── 页脚年份 ── */
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
