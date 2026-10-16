/* APRSNETCN OA 的参与台名单：读 <ul id="oaRoster" data-roster="…"> 指到的那份 JSON，
   把选中那一轮的台站铺成行，旁边的 <select id="oaRound"> 用来切轮次。名单每周换一次，
   数据由发布流程写入，机器人上线之前它是空的。

   文件格式（rounds 里新的在前，页面也会自己按 date 倒序排一遍）：
     {"updated": "2026-10-18",
      "rounds": [
        {"date": "2026-10-17", "updated": "2026-10-18",
         "stations": [{"call": "BG7XXX", "grid": "OL62", "via": "rf", "at": "2026-10-17"}]}
      ]}

   date 是这一轮的日期，也是切换格里的那一行文字。updated 是这一轮名单最后写入的日期，
   有才显示。via 只有两种：rf = 射频送进来的，行末加一个标签；is = 网络送进来的，不加。
   标签文字、空状态与「最后更新」的前缀都从页面的 data-* 上取，中英两页共用这份脚本。

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

  fetch(url, { credentials: "omit" })
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

      paint(rounds[0]);
    })
    .catch(function () {});
})();
