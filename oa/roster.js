/* APRSNETCN OA 的参与台名单：读 <ul id="oaRoster" data-roster="…"> 指到的那份 JSON，
   把台站铺成行。数据由发布流程写入，机器人上线之前它是空的。

   文件格式：
     {"updated": "2026-10-17",
      "stations": [{"call": "BG7XXX", "grid": "OL62", "via": "rf", "at": "2026-10-17"}]}

   via 只有两种：rf = 射频送进来的，行末加一个标签；is = 网络送进来的，不加。
   标签文字与「最后更新」的前缀都从页面的 data-* 上取，所以中英两页共用这一份脚本。

   取不到、不是合法 JSON、名单为空时页面一动不动，留着 HTML 里写好的那条空状态。
   一律用 textContent 写入，不拼 HTML 字符串。 */
(function () {
  "use strict";

  var list = document.getElementById("oaRoster");
  if (!list || !window.fetch) return;
  var url = list.getAttribute("data-roster");
  if (!url) return;

  var tagText = list.getAttribute("data-tag-rf") || "RF";
  var updatedLabel = list.getAttribute("data-updated-label") || "";

  /* 一行：呼号（粗体）+ 网格与日子 + 可选的路径标签 */
  var makeRow = function (st) {
    var call = String(st && st.call != null ? st.call : "").trim();
    if (!call) return null;

    var bits = [];
    if (st.grid) bits.push(String(st.grid).trim());
    if (st.at) bits.push(String(st.at).trim());

    var li = document.createElement("li");
    var box = document.createElement("div");
    box.className = "link-row is-static";

    var main = document.createElement("span");
    main.className = "lr-main";
    var b = document.createElement("b");
    b.textContent = call;
    var meta = document.createElement("span");
    meta.textContent = bits.join(" \u00b7 ");
    main.appendChild(b);
    main.appendChild(meta);
    box.appendChild(main);

    if (st.via === "rf") {
      var tag = document.createElement("span");
      tag.className = "lr-tag";
      tag.textContent = tagText;
      box.appendChild(tag);
    }

    li.appendChild(box);
    return li;
  };

  fetch(url, { credentials: "omit" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (!data || !data.stations || !data.stations.length) return;
      var frag = document.createDocumentFragment();
      var n = 0;
      data.stations.forEach(function (st) {
        var li = makeRow(st);
        if (li) { frag.appendChild(li); n += 1; }
      });
      if (!n) return;
      list.textContent = "";
      list.appendChild(frag);
      if (updatedLabel && data.updated && list.parentNode) {
        var stamp = document.createElement("p");
        stamp.className = "oa-note";
        stamp.textContent = updatedLabel + String(data.updated);
        list.parentNode.insertBefore(stamp, list.nextSibling);
      }
    })
    .catch(function () {});
})();
