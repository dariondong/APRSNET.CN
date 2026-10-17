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
   空数组就是没经过中继），at 是参与时间，msg 是这一台签到那几行后面报的话
   （`CQ CNNET` / `U CNNET` 去掉后的那一句）。status 是这一台的状态（called / acked / confirmed，
   三个词从页面的 data-status-* 上取），packets 是收到的几条报文，first 与 last 是第一次与
   最后一次的时间（都写成 10-17 15:04），页面上不写这两项，名单里还是带着，给别的用处。后四项
   没有就不写。名单里的时间是 UTC+8 的写法，atUtc 是同一个时间点的 UTC 写法，页面只在「时间」
   那一项写一遍：10-17 15:04 UTC+8 · 10-17 07:04 UTC。标 UTC+8 的那一串一直写，UTC 那一串
   名单里给了才接上（静态那份 JSON 手写时可以不写）。

   一台站画成一块：呼号一行，后面跟着由呼号前缀认出来的国家地区与国旗、状态与报文数，
   下面跟着带字段名的几项（网格 / 路径 / 时间 / 消息；网格没写着就写一个占位横线）。路径那一项把 via 与 digi
   合成一句话——走射频又过了中继是「射频 · A → B」，只走射频是「射频」，走网络是「网络」。
   字段名、路径的两种写法、空状态、「最后更新」的前缀都从页面的 data-* 上取，中英两页共用这份脚本。

   页面上的 [data-oa-stat] 是数据面板上那几个数字：rounds = 轮次，latest = 最新一轮台站，
   stations = 各轮台站合计，rf = 走射频的，updated = 名单最后更新的日期。
   活动页没有这些位子，什么也不做。

   取不到、不是合法 JSON、一轮都没有时页面一动不动，留着 HTML 里写好的那条空状态。
   一律用 textContent 写入，不拼 HTML 字符串。

   主控呼号那几个位子写的是 [data-oa-duty]，点一下就复制（按钮上的字先换成
   名单 ul 上 data-copy-done 的那句话，过一会儿换回来）。
   数据面板上还有两个下载按钮，位子是 [data-oa-image]，地址前半截取自名单 ul 的
   data-image-base，后半截是 board.png / points.png，后面接上选中那一轮与页面语言。
   数据面板上还有一份网格表，位子是 <ul id="oaGrid">：一个网格一行，后面跟着这一格里的呼号，
   跟着选中那一轮重画；活动页没有这个位子，什么也不做。
   每台那一行的右端还有一个奖状链接（data-oa-cert=呼号，文字取 ul 上的 data-cert-label），
   地址是同一目录下的 cert.png。活动页没写 data-image-base，那半截就从名单地址上去掉
   「roster」那一截自己推。 */
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
  var packetsLabel = list.getAttribute("data-label-packets") || "";
  var msgLabel = list.getAttribute("data-label-msg") || "";
  var statusWords = {
    called: list.getAttribute("data-status-called") || "",
    acked: list.getAttribute("data-status-acked") || "",
    confirmed: list.getAttribute("data-status-confirmed") || ""
  };
  var certLabel = list.getAttribute("data-cert-label") || "";
  var emptyTitle = list.getAttribute("data-empty-title") || "";
  var emptyNote = list.getAttribute("data-empty-note") || "";
  var box = document.getElementById("oaSwitch");
  var picker = document.getElementById("oaRound");

  /* 时间写成两个时区：先标 UTC+8，再跟上 UTC 那一串（名单里给了才写） */
  var zonesOf = function (local, utc) {
    var l = String(local == null ? "" : local).trim();
    if (!l) return "";
    var u = String(utc == null ? "" : utc).trim();
    return l + " UTC+8" + (u ? " \u00b7 " + u + " UTC" : "");
  };

  /* 一个字段：值空着就不写，字段名空着就只写值；给了占位就写占位 */
  var addField = function (dl, label, value, blank) {
    var v = String(value == null ? "" : value).trim();
    if (!v) v = String(blank == null ? "" : blank).trim();
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

  /* 呼号前缀认地区与国旗：长前缀先匹配，「B」宽在后面接着。
     "XX-YY" 是首两位落在区间里的（含两端），一对一的写法就是普通前缀。
     认不出来的不写那一行。中文页与英文页共一份，靠 <html lang> 选哪个名字。 */
  var REGIONS = [
    ["VR2", "中国香港", "Hong Kong", "\ud83c\udded\ud83c\uddf0"],
    ["XX9", "中国澳门", "Macao", "\ud83c\uddf2\ud83c\uddf4"],
    ["BM", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BN", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BO", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BP", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BQ", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BU", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BV", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BW", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["BX", "中国台湾", "Taiwan, China", "\ud83c\uddf9\ud83c\uddfc"],
    ["B", "中国", "China", "\ud83c\udde8\ud83c\uddf3"],
    ["JA-JS", "日本", "Japan", "\ud83c\uddef\ud83c\uddf5"],
    ["7J-7N", "日本", "Japan", "\ud83c\uddef\ud83c\uddf5"],
    ["8J-8N", "日本", "Japan", "\ud83c\uddef\ud83c\uddf5"],
    ["HL", "韩国", "South Korea", "\ud83c\uddf0\ud83c\uddf7"],
    ["6K", "韩国", "South Korea", "\ud83c\uddf0\ud83c\uddf7"],
    ["6L", "韩国", "South Korea", "\ud83c\uddf0\ud83c\uddf7"],
    ["DS-DT", "韩国", "South Korea", "\ud83c\uddf0\ud83c\uddf7"],
    ["D7-D9", "韩国", "South Korea", "\ud83c\uddf0\ud83c\uddf7"],
    ["P5", "朝鲜", "North Korea", "\ud83c\uddf0\ud83c\uddf5"],
    ["JT-JV", "蒙古", "Mongolia", "\ud83c\uddf2\ud83c\uddf3"],
    ["RA-RZ", "俄罗斯", "Russia", "\ud83c\uddf7\ud83c\uddfa"],
    ["UA-UI", "俄罗斯", "Russia", "\ud83c\uddf7\ud83c\uddfa"],
    ["UR-UZ", "乌克兰", "Ukraine", "\ud83c\uddfa\ud83c\udde6"],
    ["EU-EW", "白俄罗斯", "Belarus", "\ud83c\udde7\ud83c\uddfe"],
    ["ER", "摩尔多瓦", "Moldova", "\ud83c\uddf2\ud83c\udde9"],
    ["ES", "爱沙尼亚", "Estonia", "\ud83c\uddea\ud83c\uddea"],
    ["YL", "拉脱维亚", "Latvia", "\ud83c\uddf1\ud83c\uddfb"],
    ["LY", "立陶宛", "Lithuania", "\ud83c\uddf1\ud83c\uddf9"],
    ["UJ-UM", "乌兹别克斯坦", "Uzbekistan", "\ud83c\uddfa\ud83c\uddff"],
    ["UN-UQ", "哈萨克斯坦", "Kazakhstan", "\ud83c\uddf0\ud83c\uddff"],
    ["4L", "格鲁吉亚", "Georgia", "\ud83c\uddec\ud83c\uddea"],
    ["EK", "亚美尼亚", "Armenia", "\ud83c\udde6\ud83c\uddf2"],
    ["4J-4K", "阿塞拜疆", "Azerbaijan", "\ud83c\udde6\ud83c\uddff"],
    ["EX", "吉尔吉斯斯坦", "Kyrgyzstan", "\ud83c\uddf0\ud83c\uddec"],
    ["EY", "塔吉克斯坦", "Tajikistan", "\ud83c\uddf9\ud83c\uddeb"],
    ["VU", "印度", "India", "\ud83c\uddee\ud83c\uddf3"],
    ["AT", "印度", "India", "\ud83c\uddee\ud83c\uddf3"],
    ["8T-8Y", "印度", "India", "\ud83c\uddee\ud83c\uddf3"],
    ["AP-AS", "巴基斯坦", "Pakistan", "\ud83c\uddf5\ud83c\uddf0"],
    ["S2", "孟加拉国", "Bangladesh", "\ud83c\udde7\ud83c\udde9"],
    ["4S", "斯里兰卡", "Sri Lanka", "\ud83c\uddf1\ud83c\uddf0"],
    ["9N", "尼泊尔", "Nepal", "\ud83c\uddf3\ud83c\uddf5"],
    ["A5", "不丹", "Bhutan", "\ud83c\udde7\ud83c\uddf9"],
    ["HS", "泰国", "Thailand", "\ud83c\uddf9\ud83c\udded"],
    ["E2", "泰国", "Thailand", "\ud83c\uddf9\ud83c\udded"],
    ["XV", "越南", "Vietnam", "\ud83c\uddfb\ud83c\uddf3"],
    ["3W", "越南", "Vietnam", "\ud83c\uddfb\ud83c\uddf3"],
    ["XW", "老挝", "Laos", "\ud83c\uddf1\ud83c\udde6"],
    ["XU", "柬埔寨", "Cambodia", "\ud83c\uddf0\ud83c\udded"],
    ["XY-XZ", "缅甸", "Myanmar", "\ud83c\uddf2\ud83c\uddf2"],
    ["9M", "马来西亚", "Malaysia", "\ud83c\uddf2\ud83c\uddfe"],
    ["9V", "新加坡", "Singapore", "\ud83c\uddf8\ud83c\uddec"],
    ["V8", "文莱", "Brunei", "\ud83c\udde7\ud83c\uddf3"],
    ["YB-YH", "印度尼西亚", "Indonesia", "\ud83c\uddee\ud83c\udde9"],
    ["DU-DZ", "菲律宾", "Philippines", "\ud83c\uddf5\ud83c\udded"],
    ["4D-4I", "菲律宾", "Philippines", "\ud83c\uddf5\ud83c\udded"],
    ["VK", "澳大利亚", "Australia", "\ud83c\udde6\ud83c\uddfa"],
    ["ZL-ZM", "新西兰", "New Zealand", "\ud83c\uddf3\ud83c\uddff"],
    ["P2", "巴布亚新几内亚", "Papua New Guinea", "\ud83c\uddf5\ud83c\uddec"],
    ["A", "美国", "United States", "\ud83c\uddfa\ud83c\uddf8"],
    ["W", "美国", "United States", "\ud83c\uddfa\ud83c\uddf8"],
    ["K", "美国", "United States", "\ud83c\uddfa\ud83c\uddf8"],
    ["N", "美国", "United States", "\ud83c\uddfa\ud83c\uddf8"],
    ["VA-VE", "加拿大", "Canada", "\ud83c\udde8\ud83c\udde6"],
    ["VO", "加拿大", "Canada", "\ud83c\udde8\ud83c\udde6"],
    ["VY", "加拿大", "Canada", "\ud83c\udde8\ud83c\udde6"],
    ["CY-CZ", "加拿大", "Canada", "\ud83c\udde8\ud83c\udde6"],
    ["CF-CK", "加拿大", "Canada", "\ud83c\udde8\ud83c\udde6"],
    ["XL-XO", "加拿大", "Canada", "\ud83c\udde8\ud83c\udde6"],
    ["XA-XI", "墨西哥", "Mexico", "\ud83c\uddf2\ud83c\uddfd"],
    ["4A-4C", "墨西哥", "Mexico", "\ud83c\uddf2\ud83c\uddfd"],
    ["6D-6J", "墨西哥", "Mexico", "\ud83c\uddf2\ud83c\uddfd"],
    ["PP-PY", "巴西", "Brazil", "\ud83c\udde7\ud83c\uddf7"],
    ["ZV-ZZ", "巴西", "Brazil", "\ud83c\udde7\ud83c\uddf7"],
    ["LO-LW", "阿根廷", "Argentina", "\ud83c\udde6\ud83c\uddf7"],
    ["AY-AZ", "阿根廷", "Argentina", "\ud83c\udde6\ud83c\uddf7"],
    ["CA-CE", "智利", "Chile", "\ud83c\udde8\ud83c\uddf1"],
    ["XQ-XR", "智利", "Chile", "\ud83c\udde8\ud83c\uddf1"],
    ["OA-OC", "秘鲁", "Peru", "\ud83c\uddf5\ud83c\uddea"],
    ["CV-CX", "乌拉圭", "Uruguay", "\ud83c\uddfa\ud83c\uddfe"],
    ["HJ-HK", "哥伦比亚", "Colombia", "\ud83c\udde8\ud83c\uddf4"],
    ["YV-YY", "委内瑞拉", "Venezuela", "\ud83c\uddfb\ud83c\uddea"],
    ["HC-HD", "厄瓜多尔", "Ecuador", "\ud83c\uddea\ud83c\udde8"],
    ["G", "英国", "United Kingdom", "\ud83c\uddec\ud83c\udde7"],
    ["M", "英国", "United Kingdom", "\ud83c\uddec\ud83c\udde7"],
    ["2E", "英国", "United Kingdom", "\ud83c\uddec\ud83c\udde7"],
    ["2I", "英国", "United Kingdom", "\ud83c\uddec\ud83c\udde7"],
    ["2M", "英国", "United Kingdom", "\ud83c\uddec\ud83c\udde7"],
    ["2W", "英国", "United Kingdom", "\ud83c\uddec\ud83c\udde7"],
    ["EI", "爱尔兰", "Ireland", "\ud83c\uddee\ud83c\uddea"],
    ["EJ", "爱尔兰", "Ireland", "\ud83c\uddee\ud83c\uddea"],
    ["F", "法国", "France", "\ud83c\uddeb\ud83c\uddf7"],
    ["TK", "法国", "France", "\ud83c\uddeb\ud83c\uddf7"],
    ["DA-DR", "德国", "Germany", "\ud83c\udde9\ud83c\uddea"],
    ["PA-PI", "荷兰", "Netherlands", "\ud83c\uddf3\ud83c\uddf1"],
    ["ON-OT", "比利时", "Belgium", "\ud83c\udde7\ud83c\uddea"],
    ["LX", "卢森堡", "Luxembourg", "\ud83c\uddf1\ud83c\uddfa"],
    ["HB", "瑞士", "Switzerland", "\ud83c\udde8\ud83c\udded"],
    ["OE", "奥地利", "Austria", "\ud83c\udde6\ud83c\uddf9"],
    ["I", "意大利", "Italy", "\ud83c\uddee\ud83c\uddf9"],
    ["EA-EH", "西班牙", "Spain", "\ud83c\uddea\ud83c\uddf8"],
    ["CT-CU", "葡萄牙", "Portugal", "\ud83c\uddf5\ud83c\uddf9"],
    ["LA-LN", "挪威", "Norway", "\ud83c\uddf3\ud83c\uddf4"],
    ["JW", "挪威", "Norway", "\ud83c\uddf3\ud83c\uddf4"],
    ["JX", "挪威", "Norway", "\ud83c\uddf3\ud83c\uddf4"],
    ["SA-SM", "瑞典", "Sweden", "\ud83c\uddf8\ud83c\uddea"],
    ["7S", "瑞典", "Sweden", "\ud83c\uddf8\ud83c\uddea"],
    ["8S", "瑞典", "Sweden", "\ud83c\uddf8\ud83c\uddea"],
    ["OF-OJ", "芬兰", "Finland", "\ud83c\uddeb\ud83c\uddee"],
    ["OZ", "丹麦", "Denmark", "\ud83c\udde9\ud83c\uddf0"],
    ["OU-OY", "丹麦", "Denmark", "\ud83c\udde9\ud83c\uddf0"],
    ["5P-5Q", "丹麦", "Denmark", "\ud83c\udde9\ud83c\uddf0"],
    ["TF", "冰岛", "Iceland", "\ud83c\uddee\ud83c\uddf8"],
    ["SP-SQ", "波兰", "Poland", "\ud83c\uddf5\ud83c\uddf1"],
    ["SO", "波兰", "Poland", "\ud83c\uddf5\ud83c\uddf1"],
    ["SN", "波兰", "Poland", "\ud83c\uddf5\ud83c\uddf1"],
    ["3Z", "波兰", "Poland", "\ud83c\uddf5\ud83c\uddf1"],
    ["HF", "波兰", "Poland", "\ud83c\uddf5\ud83c\uddf1"],
    ["OK", "捷克", "Czechia", "\ud83c\udde8\ud83c\uddff"],
    ["OL", "捷克", "Czechia", "\ud83c\udde8\ud83c\uddff"],
    ["OM", "斯洛伐克", "Slovakia", "\ud83c\uddf8\ud83c\uddf0"],
    ["HA-HG", "匈牙利", "Hungary", "\ud83c\udded\ud83c\uddfa"],
    ["YO-YR", "罗马尼亚", "Romania", "\ud83c\uddf7\ud83c\uddf4"],
    ["LZ", "保加利亚", "Bulgaria", "\ud83c\udde7\ud83c\uddec"],
    ["SV-SZ", "希腊", "Greece", "\ud83c\uddec\ud83c\uddf7"],
    ["J4", "希腊", "Greece", "\ud83c\uddec\ud83c\uddf7"],
    ["9A", "克罗地亚", "Croatia", "\ud83c\udded\ud83c\uddf7"],
    ["S5", "斯洛文尼亚", "Slovenia", "\ud83c\uddf8\ud83c\uddee"],
    ["YT-YU", "塞尔维亚", "Serbia", "\ud83c\uddf7\ud83c\uddf8"],
    ["E7", "波黑", "Bosnia and Herzegovina", "\ud83c\udde7\ud83c\udde6"],
    ["Z3", "北马其顿", "North Macedonia", "\ud83c\uddf2\ud83c\uddf0"],
    ["4O", "黑山", "Montenegro", "\ud83c\uddf2\ud83c\uddea"],
    ["ZA", "阿尔巴尼亚", "Albania", "\ud83c\udde6\ud83c\uddf1"],
    ["TA-TC", "土耳其", "Turkey", "\ud83c\uddf9\ud83c\uddf7"],
    ["YM", "土耳其", "Turkey", "\ud83c\uddf9\ud83c\uddf7"],
    ["4X", "以色列", "Israel", "\ud83c\uddee\ud83c\uddf1"],
    ["4Z", "以色列", "Israel", "\ud83c\uddee\ud83c\uddf1"],
    ["HZ", "沙特阿拉伯", "Saudi Arabia", "\ud83c\uddf8\ud83c\udde6"],
    ["7Z", "沙特阿拉伯", "Saudi Arabia", "\ud83c\uddf8\ud83c\udde6"],
    ["A6", "阿联酋", "United Arab Emirates", "\ud83c\udde6\ud83c\uddea"],
    ["A7", "卡塔尔", "Qatar", "\ud83c\uddf6\ud83c\udde6"],
    ["A9", "巴林", "Bahrain", "\ud83c\udde7\ud83c\udded"],
    ["A4", "阿曼", "Oman", "\ud83c\uddf4\ud83c\uddf2"],
    ["9K", "科威特", "Kuwait", "\ud83c\uddf0\ud83c\uddfc"],
    ["JY", "约旦", "Jordan", "\ud83c\uddef\ud83c\uddf4"],
    ["OD", "黎巴嫩", "Lebanon", "\ud83c\uddf1\ud83c\udde7"],
    ["EP-EQ", "伊朗", "Iran", "\ud83c\uddee\ud83c\uddf7"],
    ["YI", "伊拉克", "Iraq", "\ud83c\uddee\ud83c\uddf6"],
    ["YK", "叙利亚", "Syria", "\ud83c\uddf8\ud83c\uddfe"],
    ["5B", "塞浦路斯", "Cyprus", "\ud83c\udde8\ud83c\uddfe"],
    ["C4", "塞浦路斯", "Cyprus", "\ud83c\udde8\ud83c\uddfe"],
    ["P3", "塞浦路斯", "Cyprus", "\ud83c\udde8\ud83c\uddfe"],
    ["AF0-AF9", "南极", "Antarctica", "\ud83c\udde6\ud83c\uddf6"],
    ["ZR-ZU", "南非", "South Africa", "\ud83c\uddff\ud83c\udde6"],
    ["SU", "埃及", "Egypt", "\ud83c\uddea\ud83c\uddec"],
    ["SS", "埃及", "Egypt", "\ud83c\uddea\ud83c\uddec"],
    ["6A-6B", "埃及", "Egypt", "\ud83c\uddea\ud83c\uddec"],
    ["CN", "摩洛哥", "Morocco", "\ud83c\uddf2\ud83c\udde6"],
    ["5C-5G", "摩洛哥", "Morocco", "\ud83c\uddf2\ud83c\udde6"],
    ["7X", "阿尔及利亚", "Algeria", "\ud83c\udde9\ud83c\uddff"],
    ["7R", "阿尔及利亚", "Algeria", "\ud83c\udde9\ud83c\uddff"],
    ["3V", "突尼斯", "Tunisia", "\ud83c\uddf9\ud83c\uddf3"],
    ["5A", "利比亚", "Libya", "\ud83c\uddf1\ud83c\uddfe"],
    ["5N", "尼日利亚", "Nigeria", "\ud83c\uddf3\ud83c\uddec"],
    ["5Z", "肯尼亚", "Kenya", "\ud83c\uddf0\ud83c\uddea"],
    ["ET", "埃塞俄比亚", "Ethiopia", "\ud83c\uddea\ud83c\uddf9"],
    ["9G", "加纳", "Ghana", "\ud83c\uddec\ud83c\udded"],
    ["Z2", "津巴布韦", "Zimbabwe", "\ud83c\uddff\ud83c\uddfc"]
  ];

  var chinese = /^zh/i.test(document.documentElement.lang || "");
  var ordered = false;

  /* "XX-YY" 是首两位落在区间里的（含两端），其余的就是普通前缀 */
  var hitPrefix = function (call, pat) {
    var dash = pat.indexOf("-");
    if (dash > 0) {
      var lo = pat.slice(0, dash);
      var hi = pat.slice(dash + 1);
      var head = call.slice(0, lo.length);
      return head >= lo && head <= hi;
    }
    return call.indexOf(pat) === 0;
  };

  var widthOf = function (pat) {
    var dash = pat.indexOf("-");
    return dash > 0 ? dash : pat.length;
  };

  /* 前缀长的先匹配：VR2 比 B 先，JA-JS 比 J 先；同长的按表里的顺序 */
  var regionOf = function (call) {
    var c = String(call == null ? "" : call).trim().toUpperCase().replace(/-\d+$/, "");
    if (!c) return null;
    if (!ordered) {
      REGIONS.sort(function (a, b) { return widthOf(b[0]) - widthOf(a[0]); });
      ordered = true;
    }
    for (var i = 0; i < REGIONS.length; i++) {
      if (hitPrefix(c, REGIONS[i][0])) return REGIONS[i];
    }
    return null;
  };

  /* 呼号那一行上的小字：地区、状态、报文数、首末时间。没值就什么都不写 */
  var addMeta = function (head, text) {
    var v = String(text == null ? "" : text).trim();
    if (!v) return;
    var span = document.createElement("span");
    span.className = "st-meta";
    span.textContent = v;
    head.appendChild(span);
  };

  /* 一台站：呼号一行，下面跟着网格 / 路径 / 时间（这一台签到的时间） */
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
    var head = document.createElement("div");
    head.className = "st-head";
    var b = document.createElement("b");
    b.className = "st-call";
    b.textContent = call;
    head.appendChild(b);
    var region = regionOf(call);
    if (region) {
      var where = document.createElement("span");
      where.className = "st-region";
      where.textContent = (chinese ? region[1] : region[2]) + " " + region[3];
      head.appendChild(where);
    }
    addMeta(head, statusWords[String(st.status == null ? "" : st.status)] || "");
    if (st.packets) addMeta(head, packetsLabel ? packetsLabel + " " + st.packets : "");
    if (certLabel && imgBase) {
      var cert = document.createElement("a");
      cert.className = "st-cert";
      cert.setAttribute("data-oa-cert", call);
      cert.setAttribute("download", "");
      cert.textContent = certLabel;
      head.appendChild(cert);
    }
    li.appendChild(head);

    var dl = document.createElement("dl");
    dl.className = "st-fields";
    /* 网格空着也留一行，写个横线，读的人知道这台的网格是空的 */
    addField(dl, gridLabel, st.grid, "\u2014");
    addField(dl, pathLabel, path);
    addField(dl, atLabel, zonesOf(st.at, st.atUtc));
    /* 这一台签到那行后面报的那几句话，名单里没有就不写 */
    addField(dl, msgLabel, st.msg);
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
    rounds.forEach(function (r) {
      var st = (r && Array.isArray(r.stations)) ? r.stations : [];
      total += st.length;
      st.forEach(function (s) {
        if (s && s.via === "rf") rf += 1;
      });
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

  /* 数据面板上的网格图：一张世界网格地图，180×180 个 4 位格子全在，有签到的格子点亮，
     下面按格子列出呼号。页面上没有这个位子就跳过。 */
  var gridBox = document.getElementById("oaGrid");
  var MAP_W = 1440;                     /* 画布内部像素，一格 8×4，世界是 180×180 格 */
  var MAP_H = 720;
  var MAP_CW = MAP_W / 180;
  var MAP_CH = MAP_H / 180;

  /* 网格按前 4 位算：OL62AA 与 OL62CQ 都是 OL62。认不出的原样留着。 */
  var gridCell = function (grid) {
    var text = String(grid == null ? "" : grid).trim().toUpperCase();
    if (!text) return "";
    var hit = /^([A-R]{2}[0-9]{2})/.exec(text);
    return hit ? hit[1] : text;
  };

  /* 4 位网格在地图上的位置：[经度序号 0..179, 纬度序号 0..179] */
  var gridSquare = function (code) {
    var text = gridCell(code);                  /* 6 位的也认：先收成 4 位那一格 */
    if (!/^[A-R]{2}[0-9]{2}$/.test(text)) return null;
    return [(text.charCodeAt(0) - 65) * 10 + Number(text.charAt(2)),
            (text.charCodeAt(1) - 65) * 10 + Number(text.charAt(3))];
  };

  /* 世界底图：数据面板那个格子上写着 data-world，缺了就只画网格 */
  var worldImage = null;
  var worldURL = gridBox ? String(gridBox.getAttribute("data-world") || "") : "";
  if (worldURL && typeof window.Image === "function") {
    worldImage = new window.Image();
    worldImage.onload = function () { paintMap(); };     /* 底图到了再重画一次 */
    worldImage.src = worldURL;
  }

  var drawMap = function (squares) {
    var canvas = document.createElement("canvas");
    canvas.className = "gc-canvas";
    canvas.width = MAP_W;
    canvas.height = MAP_H;
    var ctx = canvas.getContext ? canvas.getContext("2d") : null;
    if (!ctx) return canvas;
    var css = window.getComputedStyle ? window.getComputedStyle(document.documentElement) : null;
    var token = function (name, fallback) {
      var value = css ? String(css.getPropertyValue(name) || "").trim() : "";
      return value || fallback;
    };
    if (worldImage && worldImage.complete && worldImage.naturalWidth) {
      ctx.drawImage(worldImage, 0, 0, MAP_W, MAP_H);      /* 陆地压在网格线底下 */
    }
    ctx.lineWidth = 1;
    ctx.strokeStyle = token("--line", "rgba(21,24,28,.2)");
    var i, gx, gy;
    for (i = 0; i <= 18; i++) {                     /* 每 10 格一条 field 的界 */
      gx = Math.min(i * MAP_CW * 10, MAP_W - 1) + 0.5;
      gy = Math.min(i * MAP_CH * 10, MAP_H - 1) + 0.5;
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, MAP_H); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(MAP_W, gy); ctx.stroke();
    }
    ctx.fillStyle = token("--hot-bright", "#d61f26");
    squares.forEach(function (sq) {                 /* 点亮画大一点，缩到手机上也看得见 */
      var x = Math.max(0, (sq[0] - 1) * MAP_CW);
      var y = Math.max(0, (179 - sq[1] - 1) * MAP_CH);
      ctx.fillRect(x, y, Math.min(MAP_W, x + MAP_CW * 3) - x, Math.min(MAP_H, y + MAP_CH * 3) - y);
    });
    return canvas;
  };

  var mapCanvas = null;
  var mapSquares = [];

  var paintMap = function () {                      /* 换主题时按新的颜色重画一次 */
    if (!mapCanvas || !mapCanvas.parentNode) return;
    var canvas = drawMap(mapSquares);
    mapCanvas.parentNode.replaceChild(canvas, mapCanvas);
    mapCanvas = canvas;
  };

  if (typeof MutationObserver === "function") {
    new MutationObserver(paintMap).observe(document.documentElement,
      { attributes: true, attributeFilter: ["data-theme"] });
  }

  var fillGrid = function (round) {
    if (!gridBox) return;
    var stations = (round && Array.isArray(round.stations)) ? round.stations : [];
    var order = [];
    var groups = {};
    stations.forEach(function (st) {
      var call = String(st && st.call != null ? st.call : "").trim();
      if (!call) return;
      var grid = gridCell(st && st.grid) || "—";
      if (!groups[grid]) { groups[grid] = []; order.push(grid); }
      groups[grid].push(call);
    });
    order.sort();
    mapCanvas = null;
    var frag = document.createDocumentFragment();
    if (!order.length) {
      if (!emptyTitle && !emptyNote) return;      /* 没有可写的占位文案：宁可让页面保持原样 */
      var alone = document.createElement("p");
      alone.className = "prose";
      alone.textContent = emptyTitle || "—";
      frag.appendChild(alone);
      if (emptyNote) {
        var extra = document.createElement("p");
        extra.className = "prose";
        extra.textContent = emptyNote;
        frag.appendChild(extra);
      }
    } else {
      mapSquares = [];
      order.forEach(function (grid) {
        var square = gridSquare(grid);
        if (square) mapSquares.push(square);         /* 认不出的网格只进下面那份清单 */
      });
      if (mapSquares.length) {
        var box = document.createElement("div");
        box.className = "gc-map";
        mapCanvas = drawMap(mapSquares);
        box.appendChild(mapCanvas);
        frag.appendChild(box);
      }
      var legend = document.createElement("ul");
      legend.className = "fact-list";
      order.forEach(function (grid) {
        var li = document.createElement("li");
        var b = document.createElement("b");
        b.textContent = grid;
        var span = document.createElement("span");
        span.textContent = groups[grid].join(" · ");
        li.appendChild(b);
        li.appendChild(span);
        legend.appendChild(li);
      });
      frag.appendChild(legend);
    }
    gridBox.textContent = "";
    gridBox.appendChild(frag);
  };

  var paint = function (round) {
    exportRound = round || null;
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
    fillGrid(round);
    fillImages(round);
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

  /* 数据面板上的两个下载按钮：地址跟着选中的那一轮与页面语言走。
     地址的前半截（data-image-base）从名单 ul 上取，静态站与后端两种发法都能用。 */
  var imgBase = list.getAttribute("data-image-base") || "";
  if (!imgBase) {
    /* 活动页没写 data-image-base：名单地址（…/api/roster）去掉 roster 那一截就是接口目录 */
    imgBase = url.replace(/roster[^\/]*$/, "");
  }

  /* 导出用哪种语言：先跟着页面走，数据面板上那两个小按钮能改 */
  var exportLang = chinese ? "zh" : "en";
  var langBtns = document.querySelectorAll("[data-oa-lang]");
  var paintLang = function () {
    Array.prototype.forEach.call(langBtns, function (el) {
      var on = String(el.getAttribute("data-oa-lang")) === exportLang;
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
  };

  /* 按钮与奖状链接共用的那半截查询：选中那一轮 + 导出语言 */
  var imgQuery = function (round) {
    var query = [];
    var date = String((round && round.date) || "").trim();
    if (date) query.push("date=" + encodeURIComponent(date));
    if (exportLang === "en") query.push("lang=en");
    return query.join("&");
  };

  var fillImages = function (round) {
    if (!imgBase) return;
    var query = imgQuery(round);
    var tail = query ? "?" + query : "";
    Array.prototype.forEach.call(document.querySelectorAll("[data-oa-image]"), function (el) {
      var name = el.getAttribute("data-oa-image");
      if (name) el.setAttribute("href", imgBase + name + ".png" + tail);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-oa-cert]"), function (el) {
      var call = String(el.getAttribute("data-oa-cert") || "").trim();
      if (call) el.setAttribute("href", imgBase + "cert.png?call=" + encodeURIComponent(call) + (query ? "&" + query : ""));
    });
  };

  /* 换了导出语言就把按钮与奖状链接重排一遍；页面上没有这两个小按钮就跳过 */
  var exportRound = null;
  if (langBtns.length) {
    paintLang();
    Array.prototype.forEach.call(langBtns, function (el) {
      el.addEventListener("click", function () {
        var want = String(el.getAttribute("data-oa-lang") || "").trim();
        if (!want || want === exportLang) return;
        exportLang = want;
        paintLang();
        fillImages(exportRound);
      });
    });
  }

  /* 编号：奖状右上角那 20 位是算出来的，几段定长 32 进制拼在一起。
     位置固定，拆开就是呼号、场次、时间、网格，不靠服务器、不靠名单。 */
  var B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";   /* Crockford，去掉 I L O U */
  var B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  var SPAN = [7, 1, 3, 3, 3, 2, 1];                 /* 呼号 SSID 天数 分钟 四位网格 后两位 长度 */
  var verifyBox = document.querySelector(".oa-verify");
  var verifyIn = document.getElementById("oaVerifyIn");
  var verifyGo = document.getElementById("oaVerifyGo");
  var verifyOut = document.getElementById("oaVerifyOut");

  var readCode = function (raw) {
    var text = String(raw || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
    var total = 0;
    SPAN.forEach(function (w) { total += w; });
    if (text.length !== total) return null;
    var vals = [];
    var pos = 0;
    for (var k = 0; k < SPAN.length; k++) {
      var val = 0;
      for (var j = 0; j < SPAN[k]; j++) {
        var ch = text.charAt(pos + j);
        var v = B32.indexOf(ch);
        if (v < 0) v = ch === "O" ? 0 : (ch === "I" || ch === "L") ? 1 : -1;
        if (v < 0) return null;
        val = val * 32 + v;
      }
      vals.push(val);
      pos += SPAN[k];
    }
    var callVal = vals[0];
    var ssid = vals[1];
    var days = vals[2];
    var minutes = vals[3];
    var g4 = vals[4];
    var g6 = vals[5];
    var glen = vals[6];
    var letters = "";
    for (var n = 0; n < 6; n++) {
      letters = B36.charAt(callVal % 36) + letters;
      callVal = Math.floor(callVal / 36);
    }
    var call = letters.replace(/^0+/, "") || "0";
    if (ssid) call += "-" + ssid;
    var day = new Date(Date.UTC(2020, 0, 1) + days * 86400000).toISOString().slice(0, 10);
    var at = ("0" + Math.floor(minutes / 60)).slice(-2) + ":" + ("0" + (minutes % 60)).slice(-2);
    var grid = "";
    if (glen === 1 || glen === 2) {
      var d2 = g4 % 10;
      g4 = Math.floor(g4 / 10);
      var d1 = g4 % 10;
      g4 = Math.floor(g4 / 10);
      grid = String.fromCharCode(65 + Math.floor(g4 / 18)) + String.fromCharCode(65 + (g4 % 18)) +
        d1 + d2;
      if (glen === 2) {
        grid += String.fromCharCode(65 + Math.floor(g6 / 24)) + String.fromCharCode(65 + (g6 % 24));
      }
    }
    return { call: call, date: day, at: at, grid: grid };
  };

  /* 名单在手上就顺手把那张奖状也找出来；没拉到名单也不影响拆编号 */
  var findRecord = function (code) {
    for (var i = 0; i < last.length; i++) {
      var round = last[i] || {};
      var stations = round.stations || [];
      for (var j = 0; j < stations.length; j++) {
        var st = stations[j] || {};
        if (String(st.call || "").toUpperCase() !== code.call) continue;
        if (code.grid && String(st.grid || "").toUpperCase() !== code.grid) continue;
        return { round: round, station: st };
      }
    }
    return null;
  };

  var runVerify = function () {
    if (!verifyOut || !verifyIn) return;
    var typed = String(verifyIn.value || "").trim();
    var hint = verifyBox ? (verifyBox.getAttribute("data-verify-hint") || "") : "";
    var miss = verifyBox ? (verifyBox.getAttribute("data-verify-miss") || "") : "";
    verifyOut.textContent = "";
    if (!typed) {
      verifyOut.textContent = hint;
      return;
    }
    var code = readCode(typed);
    if (!code) {
      verifyOut.textContent = miss;
      return;
    }
    var call = document.createElement("b");
    call.textContent = code.call;
    verifyOut.appendChild(call);
    var label = function (key) { return list.getAttribute(key) || ""; };
    var bits = [];
    [["data-label-round", code.date], ["data-label-at", code.at],
     ["data-label-grid", code.grid]].forEach(function (pair) {
      if (!pair[1]) return;
      var name = label(pair[0]);
      bits.push(name ? name + " " + pair[1] : String(pair[1]));
    });
    if (bits.length) {
      var info = document.createElement("span");
      info.textContent = " " + bits.join(" · ");
      verifyOut.appendChild(info);
    }
    var hit = findRecord(code);
    var certLabel = list.getAttribute("data-cert-label") || "";
    if (hit && certLabel && imgBase) {
      var query = imgQuery(hit.round);
      var link = document.createElement("a");
      link.href = imgBase + "cert.png?call=" + encodeURIComponent(String(hit.station.call || "")) +
        (query ? "&" + query : "");
      link.textContent = certLabel;
      verifyOut.appendChild(document.createTextNode(" "));
      verifyOut.appendChild(link);
    }
  };

  if (verifyIn && verifyGo) {
    verifyGo.addEventListener("click", runVerify);
    verifyIn.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        runVerify();
      }
    });
  }

  /* 名单会自己变：服务端把 data-roster 指到 /api/roster（绝对路径），隔一会儿再读一次；
     静态站上它是相对路径，读一次就不动了。 */
  var live = url.charAt(0) === "/";
  var shown = 0;
  var last = [];
  var dates = "";

  var fill = function (rounds) {
    last = rounds;
    if (box && picker) {
      var key = rounds.map(function (r) { return String(r.date); }).join(",");
      if (key !== dates) {            /* 轮次没变就不动下拉框，别把展开的那一下收掉 */
        dates = key;
        picker.textContent = "";
        rounds.forEach(function (r, i) {
          var opt = document.createElement("option");
          opt.value = String(i);
          opt.textContent = String(r.date);
          picker.appendChild(opt);
        });
      }
      picker.value = String(shown);
      box.hidden = false;
    }
    fillStats(rounds);
    fillDuty(rounds[0]);
    paint(rounds[shown] || rounds[0]);
  };

  var load = function () {
    if (live && document.hidden) return;
    fetch(url, { credentials: "omit", cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        var rounds = ((data && data.rounds) || []).filter(function (r) { return r && r.date; });
        if (!rounds.length) return;
        rounds.sort(function (a, b) { return String(b.date) < String(a.date) ? -1 : 1; });
        if (shown >= rounds.length) shown = 0;
        fill(rounds);
      })
      .catch(function () {});
  };

  /* 换一轮就画那一轮，不重新拉 */
  if (box && picker) {
    picker.addEventListener("change", function () {
      var i = parseInt(picker.value, 10);
      shown = (i >= 0 && i < last.length) ? i : 0;
      paint(last[shown] || last[0]);
    });
  }

  if (live) {
    setInterval(load, 20000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) load();
    });
  }

  fillImages(null);   /* 名单还在路上的时候，按钮先只带上语言 */
  load();
})();
