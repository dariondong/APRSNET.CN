/* APRSNETCN OA 的参与台名单：读 <ul id="oaRoster" data-roster="…"> 指到的那份 JSON，
   把选中那一轮的台站铺成行，旁边的 <select id="oaRound"> 用来切轮次。名单每周换一次，
   数据由发布流程写入，活动开始之前它是空的。

   文件格式（rounds 里新的在前，页面也会自己按 date 倒序排一遍）：
     {"updated": "2026-10-18",
      "rounds": [
        {"date": "2026-10-17", "updated": "2026-10-18", "call": "BG7LZQ",
         "stations": [{"call": "BG7XXX", "grid": "OL62", "via": "rf", "at": "2026-10-17"}]}
      ]}

   date 是这一轮的日期，也是切换格里的那一行文字。updated 是这一轮名单最后写入的日期，
   有才显示。call 是这一轮的轮值呼号，写进页面上 [data-oa-duty] 的位子，取最新的一轮；
   没写 call 就留着 HTML 里那句占位文案。via 只有两种：rf = 射频送进来的，行末加一个标签；is = 网络送进来的，不加。
   标签文字、空状态与「最后更新」的前缀都从页面的 data-* 上取，中英两页共用这份脚本。

   页面上的 [data-oa-stat] 是数据面板上那几个数字：rounds = 轮次，latest = 最新一轮台站，
   stations = 各轮台站合计，rf = 走射频的，updated = 名单最后更新的日期。
   活动页没有这些位子，什么也不做。

   取不到、不是合法 JSON、一轮都没有时页面一动不动，留着 HTML 里写好的那条空状态。
   一律用 textContent 写入，不拼 HTML 字符串。 */
(function () {
  "use strict";

  var list = document.getElementById("oaRoster");
  if (!list || !window.fetch) return;
  var url = list.getAttribute("data-roster");
  if (!url) return;

  var tagText = list.getAttribute("data-tag-rf") || "RF";
  var updatedLabel = list.getAttribute("data-updated-label") || "";
  var emptyTitle = list.getAttribute("data-empty-title") || "";
  var emptyNote = list.getAttribute("data-empty-note") || "";
  var box = document.getElementById("oaSwitch");
  var picker = document.getElementById("oaRound");

  /* 一行：呼号（粗体）+ 网格与日子 + 可选的路径标签 */
  var makeRow = function (st) {
    var call = String(st && st.call != null ? st.call : "").trim();
    if (!call) return null;

    var bits = [];
    if (st.grid) bits.push(String(st.grid).trim());
    if (st.at) bits.push(String(st.at).trim());

    var li = document.createElement("li");
    var row = document.createElement("div");
    row.className = "link-row is-static";

    var main = document.createElement("span");
    main.className = "lr-main";
    var b = document.createElement("b");
    b.textContent = call;
    var meta = document.createElement("span");
    meta.textContent = bits.join(" \u00b7 ");
    main.appendChild(b);
    main.appendChild(meta);
    row.appendChild(main);

    if (st.via === "rf") {
      var tag = document.createElement("span");
      tag.className = "lr-tag";
      tag.textContent = tagText;
      row.appendChild(tag);
    }

    li.appendChild(row);
    return li;
  };

  /* 这一轮没有台站时的占位行，文字从页面的 data-empty-* 来 */
  var makeEmpty = function () {
    var li = document.createElement("li");
    var row = document.createElement("div");
    row.className = "link-row is-static";
    var main = document.createElement("span");
    main.className = "lr-main";
    var b = document.createElement("b");
    b.textContent = emptyTitle;
    var meta = document.createElement("span");
    meta.textContent = emptyNote;
    main.appendChild(b);
    main.appendChild(meta);
    row.appendChild(main);
    li.appendChild(row);
    return li;
  };

  /* 「名单最后更新」那一行：每画一轮先撤掉上一次的 */
  var stamp = function (round) {
    var old = document.getElementById("oaStamp");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (!updatedLabel || !round || !round.updated || !list.parentNode) return;
    var p = document.createElement("p");
    p.className = "oa-note";
    p.id = "oaStamp";
    p.textContent = updatedLabel + String(round.updated);
    list.parentNode.insertBefore(p, list.nextSibling);
  };

  /* 当周的轮值呼号：写进页面上所有 [data-oa-duty] 的位子 */
  var fillDuty = function (round) {
    var call = String(round && round.call != null ? round.call : "").trim();
    if (!call) return;
    var slots = document.querySelectorAll("[data-oa-duty]");
    Array.prototype.forEach.call(slots, function (el) { el.textContent = call; });
  };

  /* 数据面板上那几个数字：一份 JSON 汇总出来，页面没有这些位子就跳过 */
  var fillStats = function (rounds) {
    var slots = document.querySelectorAll("[data-oa-stat]");
    if (!slots.length) return;
    var total = 0, rf = 0, updated = "";
    rounds.forEach(function (r) {
      var st = (r && Array.isArray(r.stations)) ? r.stations : [];
      total += st.length;
      st.forEach(function (s) { if (s && s.via === "rf") rf += 1; });
      if (!updated && r && r.updated) updated = String(r.updated);
      if (!updated && r && r.date) updated = String(r.date);
    });
    var latest = (rounds[0] && Array.isArray(rounds[0].stations)) ? rounds[0].stations.length : 0;
    var keys = ["rounds", "latest", "stations", "rf", "updated"];
    var vals = [rounds.length, latest, total, rf, updated];
    Array.prototype.forEach.call(slots, function (el) {
      var i = keys.indexOf(el.getAttribute("data-oa-stat"));
      if (i < 0) return;
      var v = vals[i];
      el.textContent = (v === "" || v === null) ? "\u2014" : String(v);
    });
  };

  var paint = function (round) {
    var stations = (round && Array.isArray(round.stations)) ? round.stations : [];
    var frag = document.createDocumentFragment();
    var n = 0;
    stations.forEach(function (st) {
      var li = makeRow(st);
      if (li) { frag.appendChild(li); n += 1; }
    });
    if (!n) {
      if (!emptyTitle) return;      /* 没有可写的占位文案：宁可让页面保持原样 */
      frag.appendChild(makeEmpty());
    }
    list.textContent = "";
    list.appendChild(frag);
    stamp(round);
  };

  fetch(url, { credentials: "omit", cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      var rounds = ((data && data.rounds) || []).filter(function (r) { return r && r.date; });
      if (!rounds.length) return;
      rounds.sort(function (a, b) { return String(b.date) < String(a.date) ? -1 : 1; });

      if (box && picker) {
        picker.textContent = "";
        rounds.forEach(function (r, i) {
          var opt = document.createElement("option");
          opt.value = String(i);
          opt.textContent = String(r.date);
          picker.appendChild(opt);
        });
        picker.value = "0";
        picker.addEventListener("change", function () {
          var i = parseInt(picker.value, 10);
          paint(rounds[i >= 0 && i < rounds.length ? i : 0]);
        });
        box.hidden = false;
      }

      fillStats(rounds);
      fillDuty(rounds[0]);
      paint(rounds[0]);
    })
    .catch(function () {});
})();
