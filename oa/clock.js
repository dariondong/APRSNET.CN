/* APRSNETCN OA 首屏：两个大钟倒计时，下面一行小字是当下的北京时间与 UTC。

   窗口固定是北京时间的周六 00:00 到 23:59，也就是两个绝对时刻，谁来看都差得一样多。
   倒计时只有一种形态：进了窗口也走数字，距开始读到 00:00:00，另一个数接着往下走。
   小字那两只钟各按自己的时区算，跟访问者的设备无关。

   文案从页面的 data-* 上取，中英两页共用这一份脚本：
     ul[data-oa-clock]       data-clock-day 天的单位 / data-clock-days 七个星期几，周日在前
     span[data-clock=start]  距窗口开始
     span[data-clock=end]    距窗口结束
     span[data-clock=bj]     当下的北京时间（UTC+8）
     span[data-clock=utc]    当下的 UTC

   取不到位子就什么也不做，HTML 里写好的占位文案留着。 */
(function () {
  "use strict";

  var box = document.querySelector("[data-oa-clock]");
  if (!box) return;

  var at = function (key) { return document.querySelector('[data-clock="' + key + '"]'); };
  var startSlot = at("start");
  var endSlot = at("end");
  var bjSlot = at("bj");
  var utcSlot = at("utc");
  if (!startSlot && !endSlot && !bjSlot && !utcSlot) return;

  var dayUnit = box.getAttribute("data-clock-day") || "d";
  var weekNames = (box.getAttribute("data-clock-days") || "").split(",");

  var DAY = 86400000;
  var HOUR = 3600000;
  var pad = function (n) { return (n < 10 ? "0" : "") + n; };

  /* 北京时间 = UTC+8：把绝对时刻挪 8 小时，再用 UTC 字段读到的就是北京时间的墙上钟。 */
  var windowAt = function (now) {
    var bj = new Date(now + 8 * HOUR);
    var midnight = bj.getTime()
      - bj.getUTCHours() * HOUR - bj.getUTCMinutes() * 60000
      - bj.getUTCSeconds() * 1000 - bj.getUTCMilliseconds();
    var back = (bj.getUTCDay() + 1) % 7;             /* 回到本来的那个周六 00:00 */
    var start = midnight - back * DAY - 8 * HOUR;    /* 再换回绝对时刻 */
    return { start: start, end: start + DAY - 60000 };
  };

  /* 余下的时间：有整天就带上整天，没有就只剩钟点 */
  var countdown = function (ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var d = Math.floor(s / 86400);
    s -= d * 86400;
    var h = Math.floor(s / 3600);
    s -= h * 3600;
    var m = Math.floor(s / 60);
    return (d ? d + dayUnit + " " : "") + pad(h) + ":" + pad(m) + ":" + pad(s - m * 60);
  };

  /* 某一刻在给定时区里的钟点，shift 是那个时区相对 UTC 的毫秒数 */
  var clockAt = function (now, shift) {
    var t = new Date(now + shift);
    return (weekNames[t.getUTCDay()] || "") + " " + pad(t.getUTCHours()) + ":"
      + pad(t.getUTCMinutes()) + ":" + pad(t.getUTCSeconds());
  };

  var tick = function () {
    var now = Date.now();
    var w = windowAt(now);
    if (now >= w.end) w = { start: w.start + 7 * DAY, end: w.end + 7 * DAY };
    if (startSlot) startSlot.textContent = countdown(w.start - now);
    if (endSlot) endSlot.textContent = countdown(w.end - now);
    if (bjSlot) bjSlot.textContent = clockAt(now, 8 * HOUR);
    if (utcSlot) utcSlot.textContent = clockAt(now, 0);
  };

  tick();
  setInterval(tick, 1000);
})();
