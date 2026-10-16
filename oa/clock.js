/* APRSNETCN OA 首屏的两个倒计时：到活动窗口开始、到窗口结束。

   窗口固定是北京时间的周六 00:00 到 23:59，也就是两个绝对时刻，谁来看都差得一样多。
   剩下的时间按访问者设备上的钟算，页面那行「你那边」的星期几与钟点也按设备时区写，
   所以 UTC-5 的人看到的是周五 11:00 开始，UTC+5:30 的人看到的是周五 21:30 开始。

   文案全部从页面的 data-* 上取，中英两页共用这一份脚本：
     ul[data-oa-clock]        data-clock-live 窗口里那句词 / data-clock-day 天的单位
                              data-clock-days 七个星期几，逗号隔开，周日在前
     span[data-clock=start]   到窗口开始的倒计时（窗口里就写那句词）
     span[data-clock=end]     到窗口结束的倒计时
     span[data-clock=from]    窗口开始在你那边的钟点
     span[data-clock=to]      窗口结束在你那边的钟点

   取不到位子就什么也不做，HTML 里写好的占位文案留着。 */
(function () {
  "use strict";

  var box = document.querySelector("[data-oa-clock]");
  if (!box) return;

  var at = function (key) { return document.querySelector('[data-clock="' + key + '"]'); };
  var startSlot = at("start");
  var endSlot = at("end");
  var fromSlot = at("from");
  var toSlot = at("to");
  if (!startSlot && !endSlot && !fromSlot && !toSlot) return;

  var liveText = box.getAttribute("data-clock-live") || "Live";
  var dayUnit = box.getAttribute("data-clock-day") || "d";
  var weekNames = (box.getAttribute("data-clock-days") || "").split(",");

  var DAY = 86400000;
  var pad = function (n) { return (n < 10 ? "0" : "") + n; };

  /* 北京时间 = UTC+8：把绝对时刻挪 8 小时，再用 UTC 字段读到的就是北京时间的墙上钟。 */
  var windowAt = function (now) {
    var bj = new Date(now + 8 * 3600000);
    var midnight = bj.getTime()
      - bj.getUTCHours() * 3600000 - bj.getUTCMinutes() * 60000
      - bj.getUTCSeconds() * 1000 - bj.getUTCMilliseconds();
    var back = (bj.getUTCDay() + 1) % 7;            /* 回到本来的那个周六 00:00 */
    var start = midnight - back * DAY - 8 * 3600000; /* 再换回绝对时刻 */
    return { start: start, end: start + DAY - 60000 };
  };

  /* 余下的时间：有天数就带上天数，没有就只剩钟点 */
  var countdown = function (ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var d = Math.floor(s / 86400);
    s -= d * 86400;
    var h = Math.floor(s / 3600);
    s -= h * 3600;
    var m = Math.floor(s / 60);
    return (d ? d + dayUnit + " " : "") + pad(h) + ":" + pad(m) + ":" + pad(s - m * 60);
  };

  /* 同一个绝对时刻，按访问者设备上的时区写成「周五 11:00」 */
  var localClock = function (ms) {
    var t = new Date(ms);
    return (weekNames[t.getDay()] || "") + " " + pad(t.getHours()) + ":" + pad(t.getMinutes());
  };

  var tick = function () {
    var now = Date.now();
    var w = windowAt(now);
    if (now >= w.end) w = { start: w.start + 7 * DAY, end: w.end + 7 * DAY };
    var live = now >= w.start;
    if (startSlot) {
      startSlot.textContent = live ? liveText : countdown(w.start - now);
      startSlot.className = live ? "is-live" : "";
    }
    if (endSlot) endSlot.textContent = countdown(w.end - now);
    if (fromSlot) fromSlot.textContent = localClock(w.start);
    if (toSlot) toSlot.textContent = localClock(w.end);
  };

  tick();
  setInterval(tick, 1000);
})();
