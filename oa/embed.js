/* APRSNETCN OA 小件：名单上有几台、几个网格。
   数字来自同一份名单 JSON（[data-em-roster]），文案写在页面里，脚本只填两个数。
   取不到就留着页面上的占位符，不把「—」改成 0 —— 0 是「这一轮还没有台站」。 */
(function () {
  "use strict";

  var box = document.querySelector("[data-em-roster]");
  if (!box) return;
  var url = box.getAttribute("data-em-roster");
  if (!url) return;

  var stationSlot = document.querySelector('[data-em="stations"]');
  var gridSlot = document.querySelector('[data-em="grids"]');
  if (!stationSlot && !gridSlot) return;

  var POLL_MS = 30000;
  var busy = false;

  /* 网格只认前四位：前四位就是网格方格本身，后两位是格内再分的一小块。
     没写网格的不算，重复的只算一次。 */
  var counts = function (data) {
    var rounds = (data && data.rounds) || [];
    var list = (rounds[0] && rounds[0].stations) || [];
    var seen = {};
    var grids = 0;
    for (var i = 0; i < list.length; i++) {
      var g = String((list[i] && list[i].grid) || "").trim().toUpperCase().slice(0, 4);
      if (g.length === 4 && !seen[g]) {
        seen[g] = 1;
        grids += 1;
      }
    }
    return { stations: list.length, grids: grids };
  };

  var fill = function (n) {
    if (stationSlot) stationSlot.textContent = String(n.stations);
    if (gridSlot) gridSlot.textContent = String(n.grids);
  };

  var load = function () {
    if (busy) return;
    busy = true;
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { fill(counts(d)); })
      .catch(function () { /* 后端不在，占位符留着 */ })
      .then(function () { busy = false; });
  };

  load();
  setInterval(function () { if (!document.hidden) load(); }, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
