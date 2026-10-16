/* APRSNETCN OA 的参与台名单：读 <ul id="oaRoster" data-roster="…"> 指到的那份 JSON，
   把选中那一轮的台站铺成行，旁边的 <select id="oaRound"> 用来切轮次。名单每周换一次，
   数据由发布流程写入，活动开始之前它是空的。

   文件格式（rounds 里新的在前，页面也会自己按 date 倒序排一遍）：
     {"updated": "2026-10-18",
      "rounds": [
        {"date": "2026-10-17", "updated": "2026-10-18", "call": "BG7LZQ",
         "stations": [{"call": "BG7XXX", "grid": "OL62", "via": "rf",
                       "digi": ["BG7LZQ-1", "BG7LZQ-2"], "at": "10-17 15:04"}]}
      ]}

   date 是这一轮的日期，也是切换格里的那一行文字。updated 是这一轮名单最后写入的日期，
   有才显示。call 是这一轮的轮值呼号，写进页面上 [data-oa-duty] 的位子，取最新的一轮；
   没写 call 就留着 HTML 里那句占位文案。grid 是网格，digi 是报文经过的中继（按经过顺序写，
   空数组就是没经过中继），at 是参与时间。

   一台站画成一块：呼号一行，下面跟着带字段名的几项（网格 / 路径 / 时间）。路径那一项把 via 与 digi
   合成一句话——走射频又过了中继是「射频 · A → B」，只走射频是「射频」，走网络是「网络」。
   字段名、路径的两种写法、空状态、「最后更新」的前缀都从页面的 data-* 上取，中英两页共用这份脚本。

   页面上的 [data-oa-stat] 是数据面板上那几个数字：rounds = 轮次，latest = 最新一轮台站，
   stations = 各轮台站合计，rf = 走射频的，digis = 用过的中继（去重），
   updated = 名单最后更新的日期。
   活动页没有这些位子，什么也不做。

   取不到、不是合法 JSON、一轮都没有时页面一动不动，留着 HTML 里写好的那条空状态。
   一律用 textContent 写入，不拼 HTML 字符串。

   主控呼号那几个位子写的是 [data-oa-duty]，点一下就复制（按钮上的字先换成
   名单 ul 上 data-copy-done 的那句话，过一会儿换回来）。 */
(function () {
  "use strict";

  var list = document.getElementById("oaRoster");
  if (!list || !window.fetch) return;
  var url = list.getAttribute("data-roster");
  if (!url) return;

  var gridLabel = list.getAttribute("data-label-grid") || "";
  var pathLabel = list.getAttribute("data-label-path") || "";
  var atLabel = list.getAttribute("data-label-at") || "";
  var pathRf = list.getAttribute("data-path-rf") || "RF";
  var pathIs = list.getAttribute("data-path-is") || "Network";
  var updatedLabel = list.getAttribute("data-updated-label") || "";
  var emptyTitle = list.getAttribute("data-empty-title") || "";
  var emptyNote = list.getAttribute("data-empty-note") || "";
  var box = document.getElementById("oaSwitch");
  var picker = document.getElementById("oaRound");

  /* 一个字段：值空着就不写，字段名空着就只写值 */
  var addField = function (dl, label, value) {
    var v = String(value == null ? "" : value).trim();
    if (!v) return;
    var wrap = document.createElement("div");
    if (label) {
      var dt = document.createElement("dt");
      dt.textContent = label;
      wrap.appendChild(dt);
    }
    var dd = document.createElement("dd");
    dd.textContent = v;
    wrap.appendChild(dd);
    dl.appendChild(wrap);
  };

  /* 一台站：呼号一行，下面跟着网格 / 路径 / 参与时间 */
  var makeRow = function (st) {
    var call = String(st && st.call != null ? st.call : "").trim();
    if (!call) return null;

    var chain = [];
    var digis = Array.isArray(st.digi) ? st.digi : [];
    digis.forEach(function (d) {
      var name = String(d == null ? "" : d).trim();
      if (name) chain.push(name);
    });
    /* 走射频就是路上过了中继：两半并成一项写 */
    var via = st.via === "rf" ? pathRf : (st.via === "is" ? pathIs : "");
    var path = [via, chain.join(" \u2192 ")].filter(function (s) {
      return s;
    }).join(" \u00b7 ");

    var li = document.createElement("li");
    li.className = "st";
    var b = document.createElement("b");
    b.className = "st-call";
    b.textContent = call;
    li.appendChild(b);

    var dl = document.createElement("dl");
    dl.className = "st-fields";
    addField(dl, gridLabel, st.grid);
    addField(dl, pathLabel, path);
    addField(dl, atLabel, st.at);
    li.appendChild(dl);
    return li;
  };

  /* 这一轮没有台站时的占位块，文字从页面的 data-empty-* 来 */
  var makeEmpty = function () {
    var li = document.createElement("li");
    li.className = "st";
    var b = document.createElement("b");
    b.className = "st-call";
    b.textContent = emptyTitle;
    li.appendChild(b);
    if (emptyNote) {
      var note = document.createElement("span");
      note.className = "st-note";
      note.textContent = emptyNote;
      li.appendChild(note);
    }
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
    var digiSet = [];
    var countDigis = function (s) {
      var chain = (s && Array.isArray(s.digi)) ? s.digi : [];
      chain.forEach(function (d) {
        var name = String(d == null ? "" : d).trim();
        if (name && digiSet.indexOf(name) < 0) digiSet.push(name);
      });
    };
    rounds.forEach(function (r) {
      var st = (r && Array.isArray(r.stations)) ? r.stations : [];
      total += st.length;
      st.forEach(function (s) {
        if (s && s.via === "rf") rf += 1;
        countDigis(s);
      });
      if (!updated && r && r.updated) updated = String(r.updated);
      if (!updated && r && r.date) updated = String(r.date);
    });
    var latest = (rounds[0] && Array.isArray(rounds[0].stations)) ? rounds[0].stations.length : 0;
    var keys = ["rounds", "latest", "stations", "rf", "digis", "updated"];
    var vals = [rounds.length, latest, total, rf, digiSet.length, updated];
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

  /* 主控呼号：点一下复制。老浏览器或非安全来源没有 navigator.clipboard，
     退回选中再复制的老办法。 */
  var copyDone = list.getAttribute("data-copy-done") || "";

  var copied = function (el) {
    if (!copyDone) return;
    if (!el.getAttribute("data-label")) el.setAttribute("data-label", el.textContent);
    el.textContent = copyDone;
    setTimeout(function () {
      var label = el.getAttribute("data-label");
      if (label) el.textContent = label;
    }, 1600);
  };

  var fallbackCopy = function (text, el) {
    var box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "");
    box.style.position = "fixed";
    box.style.top = "-1000px";
    document.body.appendChild(box);
    box.select();
    try {
      document.execCommand("copy");
      copied(el);
    } catch (e) {
      /* 复制不成也看得见呼号，不折腾 */
    }
    document.body.removeChild(box);
  };

  var copyText = function (text, el) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { copied(el); },
        function () { fallbackCopy(text, el); });
      return;
    }
    fallbackCopy(text, el);
  };

  /* 呼号那个位子可能是按钮，也可能还是一段文字，点它就复制 */
  document.addEventListener("click", function (e) {
    var el = e.target;
    while (el && el.nodeType === 1 && el !== document) {
      if (el.hasAttribute("data-oa-duty")) {
        copyText(String(el.getAttribute("data-label") || el.textContent || "").trim(), el);
        return;
      }
      el = el.parentNode;
    }
  });

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
