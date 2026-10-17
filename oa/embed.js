/* APRSNETCN OA 小件：嵌在公告里的那块读数。

   一只钟交给 clock.js（北京时间的周六 00:00 到 23:59），这里补上名单上算得出来的几样：
   有几台、几个网格、几台走射频、轮值呼号、最近一台是谁几点签的、这一轮的日期，
   再把「未开始 / 进行中 / 结束了」写在右上角。

   数字来自同一份名单 JSON（[data-em-roster]），文案写在页面里，脚本只填数：
     [data-em=stations] 台站  [data-em=grids] 网格（前四位去重）  [data-em=rf] 走射频
     [data-em=duty] 轮值呼号  [data-em=latest] 最近签到的那一台与时间  [data-em=round] 场次日期
     [data-em=state] 状态，三个词在 data-em-before / -live / -over 上

   取不到名单就留着页面上的占位符，不把「—」改成 0 —— 0 是「这一轮还没有台站」。
   状态与名单无关，照样每秒自己算。 */
(function () {
  "use strict";

  var box = document.querySelector("[data-em-roster]");
  var url = box ? box.getAttribute("data-em-roster") : "";
  var slots = {};
  ["stations", "grids", "rf", "duty", "latest", "round", "state"].forEach(function (key) {
    slots[key] = document.querySelector('[data-em="' + key + '"]');
  });

  var put = function (key, text) {
    var el = slots[key];
    if (el && text) el.textContent = text;
  };

  var DAY = 86400000;
  var HOUR = 3600000;

  /* 北京时间 = UTC+8，把绝对时刻挪八小时再用 UTC 字段读，就是北京时间的墙上钟。
     窗口是本周六 00:00 到 23:59，两个绝对时刻。 */
  var windowAt = function (now) {
    var bj = new Date(now + 8 * HOUR);
    var midnight = bj.getTime()
      - bj.getUTCHours() * HOUR - bj.getUTCMinutes() * 60000
      - bj.getUTCSeconds() * 1000 - bj.getUTCMilliseconds();
    var back = (bj.getUTCDay() + 1) % 7;
    var start = midnight - back * DAY - 8 * HOUR;
    return { start: start, end: start + DAY - 60000 };
  };

  var stateEl = slots.state;
  var words = stateEl ? {
    before: stateEl.getAttribute("data-em-before") || "",
    live: stateEl.getAttribute("data-em-live") || "",
    over: stateEl.getAttribute("data-em-over") || ""
  } : null;
  var lastState = "";

  var paintState = function () {
    if (!stateEl || !words) return;
    var now = Date.now();
    var w = windowAt(now);
    var key = now < w.start ? "before" : (now <= w.end ? "live" : "over");
    if (key === lastState) return;
    lastState = key;
    stateEl.textContent = words[key];
    stateEl.className = "em-state" + (key === "live" ? " is-live" : "");
  };

  /* 网格只认前四位：前四位就是网格方格本身，后两位是格内再分的一小块。
     没写网格的不算，重复的只算一次。 */
  var counts = function (round) {
    var list = (round && round.stations) || [];
    var seen = {};
    var grids = 0;
    var rf = 0;
    var latest = null;
    for (var i = 0; i < list.length; i++) {
      var st = list[i] || {};
      var g = String(st.grid || "").trim().toUpperCase().slice(0, 4);
      if (g.length === 4 && !seen[g]) {
        seen[g] = 1;
        grids += 1;
      }
      if (st.via === "rf") rf += 1;
      /* 时间写成「10-17 15:04」，字面比大小就是比先后 */
      if (st.call && (!latest || String(st.at || "") > String(latest.at || ""))) latest = st;
    }
    return { stations: list.length, grids: grids, rf: rf, latest: latest };
  };

  var fill = function (data) {
    var rounds = (data && data.rounds) || [];
    var round = rounds[0] || null;
    if (!round) return;
    var n = counts(round);
    put("stations", String(n.stations));
    put("grids", String(n.grids));
    put("rf", String(n.rf));
    put("duty", String(round.call || ""));
    put("round", String(round.date || ""));
    if (n.latest) {
      put("latest", String(n.latest.call) + (n.latest.at ? " " + String(n.latest.at) : ""));
    }
  };

  var POLL_MS = 30000;
  var busy = false;

  var load = function () {
    if (!url || busy) return;
    busy = true;
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(fill)
      .catch(function () { /* 后端不在，占位符与页面写好的呼号留着 */ })
      .then(function () { busy = false; });
  };

  paintState();
  setInterval(paintState, 1000);
  load();
  setInterval(function () { if (!document.hidden) load(); }, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
