/* APRSNET.CN 首屏：一个点 → 发一次信标 → 一张网
   手写 Canvas 2D，零依赖、定种子（同一份网，每次打开都一样）。
   900 个点按黄金角铺在球面上，一开始挤成一粒；按下去等于发一次信标，
   点散开成球面，邻点之间自己拉出线来 —— 这才看出是「一张网」；
   其中一粒是你的（橙的），一圈波纹从你这里漫过整张网。

   为什么换成 2D 画布：原来那版是 WebGL1 点云，在有的浏览器/设备上首屏
   一直是空的（拿不到上下文，或者带 alpha 的画布合成不出来），只剩一张
   静态图顶着。2D 画布哪里都有，几何和时间轴一个字没改，换的只是画法：
   邻点连线一次 stroke 出去，点按明暗分桶，一桶一次 fill。

   画布就是首屏那张网，没有备胎（静态 SVG 已删）。
   关了动效就给一张静止的网：不呼吸、不转、不放电波，但照样画出来。 */
(function () {
  "use strict";

  var canvas = document.getElementById("heroField");
  if (!canvas || !canvas.getContext) return;
  var ctx = null;
  try { ctx = canvas.getContext("2d"); } catch (e) { ctx = null; }
  if (!ctx) return;

  /* 系统说要减少动效时不开动画 —— 但仍然给一张静止的网（见下面的 still 分支） */
  var still = false;
  try {
    var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    still = !!(mq && mq.matches);
  } catch (e) { still = false; }

  /* ── 几何：球面上的等距点（黄金角螺旋），壳薄一点球面才清楚 ── */
  var N = 900;
  var GA = 2.399963229728653;
  var seed = 0x41504e53; /* "APNS" */
  function rnd() {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  }

  var YOU = [0.44, 0.28, 0.85]; /* 「你」那一粒的朝向（靠前偏右） */
  var youLen = Math.sqrt(YOU[0] * YOU[0] + YOU[1] * YOU[1] + YOU[2] * YOU[2]);
  YOU[0] /= youLen; YOU[1] /= youLen; YOU[2] /= youLen;

  var gx = new Float32Array(N), gy = new Float32Array(N), gz = new Float32Array(N);
  var psz = new Float32Array(N);   /* 点的粗细系数 */
  var ph = new Float32Array(N);    /* 错峰相位（同一次广播里各点先后不一样） */
  var kind = new Uint8Array(N);    /* 1 = 你自己那一粒 */
  var youIndex = 0, youBest = -2;
  var i, j;
  for (i = 0; i < N; i++) {
    var y = 1 - (i + 0.5) * 2 / N;
    var r = Math.sqrt(Math.max(0, 1 - y * y));
    var a = i * GA;
    var dx = Math.cos(a) * r, dz = Math.sin(a) * r;
    var rad = 0.96 + 0.07 * rnd();
    ph[i] = rnd();
    /* 每隔十来个点给一粒稍大的，看上去像地图上的台站，不是均匀的一团 */
    psz[i] = (0.019 + 0.007 * rnd()) * (i % 11 === 0 ? 1.5 : 1);
    gx[i] = dx * rad;
    gy[i] = y * rad;
    gz[i] = dz * rad;
    var dot = dx * YOU[0] + y * YOU[1] + dz * YOU[2];
    if (dot > youBest) { youBest = dot; youIndex = i; }
  }
  psz[youIndex] = 0.052;
  kind[youIndex] = 1;

  /* ── 邻点之间拉线：先量出近邻尺度，再按 1.7 倍连边（只看 i<j，天然不重复） ── */
  var near2 = new Float32Array(N);
  var d2, dx2, dy2, dz2;
  for (i = 0; i < N; i++) near2[i] = 8;
  for (i = 0; i < N; i++) {
    for (j = i + 1; j < N; j++) {
      dx2 = gx[i] - gx[j];
      dy2 = gy[i] - gy[j];
      dz2 = gz[i] - gz[j];
      d2 = dx2 * dx2 + dy2 * dy2 + dz2 * dz2;
      if (d2 < near2[i]) near2[i] = d2;
      if (d2 < near2[j]) near2[j] = d2;
    }
  }
  var sortedNear = Array.prototype.slice.call(near2).sort(function (p, q) { return p - q; });
  var spacing = Math.sqrt(sortedNear[Math.floor(N / 2)]); /* 中位近邻距离 */
  var cut2 = spacing * spacing * 1.7 * 1.7;

  var segs = [];
  for (i = 0; i < N; i++) {
    for (j = i + 1; j < N; j++) {
      dx2 = gx[i] - gx[j];
      if (dx2 * dx2 > cut2) continue;
      dy2 = gy[i] - gy[j];
      if (dy2 * dy2 > cut2) continue;
      dz2 = gz[i] - gz[j];
      if (dz2 * dz2 > cut2) continue;
      d2 = dx2 * dx2 + dy2 * dy2 + dz2 * dz2;
      if (d2 <= cut2 && d2 > 0.000001) segs.push(i, j);
    }
  }

  /* ── 颜色：只从站上现成的两支令牌里取，不写死色号 ── */
  function token(name, fallback) {
    var raw = "";
    try { raw = getComputedStyle(document.documentElement).getPropertyValue(name) || ""; } catch (e) { raw = ""; }
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(raw.replace(/\s+/g, ""));
    if (!m) return fallback;
    var h = m[1];
    if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
    var n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  /* 网用墨色（不是淡墨）—— 白底上一粒深点才干净，深浅交给前后层次去拉 */
  var col = [21, 24, 28], hot = [255, 106, 0];
  function readTokens() {
    col = token("--text", [21, 24, 28]);
    hot = token("--hot-bright", [255, 106, 0]);
  }
  readTokens();

  function rgba(c, a) {
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }

  /* ── 画布尺寸：量 clientWidth/Height（CSS 变换不触发 resize，用缩放后的矩形会糊） ── */
  var FOV = 33 * Math.PI / 180;
  var PROJ = 1 / Math.tan(FOV / 2);
  var DIST = 3.8;
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var cw = 0, chh = 0, pxScale = 400;

  function resize() {
    cw = canvas.clientWidth || 0;
    chh = canvas.clientHeight || 0;
    if (cw < 8 || chh < 8) return false;
    var w = Math.round(cw * dpr), h = Math.round(chh * dpr);
    if (w !== canvas.width || h !== canvas.height) {
      canvas.width = w;
      canvas.height = h;
    }
    pxScale = (chh / 2) * PROJ;
    return true;
  }

  /* ── 入场时间轴 ── */
  var GATHER_AT = 0.9;    /* 先安静地坐成一粒点 */
  var GATHER_MS = 1.7;    /* 发一次信标，点散开成网 */
  var RING_AT = 1.15;     /* 波纹从你这里漫出去 */
  var RING_PERIOD = 4.8;
  var RING_MS = 1.7;
  var RING_MAX = 2.35;    /* 单位球面上两点最远距离 2 */
  var SPIN = 0.13;        /* 弧度/秒，一圈约 48 秒 */

  var t0 = 0, started = false, raf = 0, inView = true, tAnim = 0, gather = 0, spin = 0, ring = 9;

  /* 每帧用的几个暂存：投影后的屏幕坐标、明暗、粗细 */
  var sxa = new Float32Array(N), sya = new Float32Array(N);
  var fad = new Float32Array(N), sza = new Float32Array(N);

  /* 明暗分档：桶里取的值略低于该档上限，免得把背面的点/线也提亮
     （原来那版是 vFade 逐像素插值的，分桶要是向上取整，整张网会糊成一片）
     另外背面（f 很小）索性不画 —— 一眼看得出球面朝向，也不白花力气。 */
  var CULL = 0.32;
  var LINE_A = [0.04, 0.08, 0.14];
  var DOT_A = [0.3, 0.5, 0.75, 0.95];
  var lineB = new Uint8Array(segs.length); /* 复用的分桶表，省得每帧新建 */

  function draw() {
    if (!cw || !chh) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, chh);

    var ca = Math.cos(spin), sa = Math.sin(spin);
    var hx = cw / 2, hy = chh / 2;
    var k, b, x, y, z, g, d, tw, bright, pulse, front, vis, f, ln, q, t;
    /* 散开的过程中点先是橙的（刚发出去的那一粒），落定成墨色 */
    var cloud = [
      Math.round(hot[0] + (col[0] - hot[0]) * gather),
      Math.round(hot[1] + (col[1] - hot[1]) * gather),
      Math.round(hot[2] + (col[2] - hot[2]) * gather)
    ];

    for (i = 0; i < N; i++) {
      x = gx[i] * ca - gz[i] * sa;
      z = gx[i] * sa + gz[i] * ca;
      y = gy[i];
      /* 每粒自己的散开进度，错峰 */
      g = (gather - ph[i] * 0.45) / 0.55;
      g = g <= 0 ? 0 : (g >= 1 ? 1 : g);
      g = g * g * (3 - 2 * g);
      /* 波纹：按它与「你」的球面距离取一圈亮环（home 半径≈1，先归一化） */
      ln = Math.sqrt(x * x + y * y + z * z) || 1;
      q = (x / ln) * YOU[0] + (y / ln) * YOU[1] + (z / ln) * YOU[2];
      q = q > 1 ? 1 : (q < -1 ? -1 : q);
      q = (Math.sqrt(2 - 2 * q) - ring) * 3.4;

      x *= g; y *= g; z *= g;
      d = DIST - z;
      if (d < 0.45) d = 0.45;
      sxa[i] = hx + (x / d) * pxScale;
      sya[i] = hy - (y / d) * pxScale;

      tw = 0.5 + 0.5 * Math.sin(tAnim * (0.6 + 0.9 * ph[i]) + ph[i] * 24);
      bright = 0.42 + 0.58 * tw;
      if (kind[i]) {
        pulse = 0.5 + 0.5 * Math.sin(tAnim * 1.5);
        bright *= 0.72 + 0.78 * pulse;
      }
      bright *= 1 + Math.exp(-q * q) * 2.2 * g;
      front = z * 0.9 + 0.55;
      front = front < 0 ? 0 : (front > 1 ? 1 : front);
      vis = 1 + (0.10 + 0.90 * front - 1) * g;
      if (kind[i] && vis < 0.5) vis = 0.5;
      f = vis * bright;
      fad[i] = f > 1 ? 1 : f;
      sza[i] = psz[i] * pxScale / d * (1.8 - 0.8 * g) * (0.88 + 0.28 * tw);
    }

    /* 先连线：按两端明暗分成三档，一档一条路径一次 stroke */
    t = segs.length * 0.5;
    for (k = 0; k < t; k++) {
      f = (fad[segs[k * 2]] + fad[segs[k * 2 + 1]]) * 0.5;
      lineB[k] = f < CULL ? 255 : (f < 0.42 ? 0 : (f < 0.7 ? 1 : 2));
    }
    for (b = 0; b < 3; b++) {
      ctx.beginPath();
      for (k = 0; k < t; k++) {
        if (lineB[k] !== b) continue;
        i = segs[k * 2];
        j = segs[k * 2 + 1];
        ctx.moveTo(sxa[i], sya[i]);
        ctx.lineTo(sxa[j], sya[j]);
      }
      ctx.strokeStyle = rgba(col, LINE_A[b]);
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    /* 再画点：颜色两种（墨 / 橙）× 明暗四档，一桶一次 fill */
    for (b = 0; b < 4; b++) {
      ctx.fillStyle = rgba(cloud, DOT_A[b]);
      ctx.beginPath();
      for (i = 0; i < N; i++) {
        if (kind[i]) continue;
        f = fad[i];
        if (f < CULL) continue;
        if ((f >= 1 ? 3 : (f * 4) | 0) !== b) continue;
        k = sza[i];
        if (k < 0.8) k = 0.8;
        if (k > 8) k = 8;
        if (k < 2) ctx.rect(sxa[i] - k * 0.5, sya[i] - k * 0.5, k, k);
        else {
          /* 圆点前先 moveTo：arc 会从当前点拉一条线过来，否则整片路径连成一张实心网 */
          ctx.moveTo(sxa[i] + k * 0.5, sya[i]);
          ctx.arc(sxa[i], sya[i], k * 0.5, 0, 6.2832);
        }
      }
      ctx.fill();
    }
    /* 「你」那一粒单独画：最亮，橙的 */
    ctx.fillStyle = rgba(hot, 1);
    ctx.beginPath();
    k = sza[youIndex];
    if (k < 1.2) k = 1.2;
    if (k > 12) k = 12;
    ctx.moveTo(sxa[youIndex] + k * 0.5, sya[youIndex]);
    ctx.arc(sxa[youIndex], sya[youIndex], k * 0.5, 0, 6.2832);
    ctx.fill();
  }

  function schedule() {
    if (raf || !inView || document.hidden) return;
    raf = requestAnimationFrame(frame);
  }

  function frame(ts) {
    raf = 0;
    if (!started) { t0 = ts; started = true; }
    var e = Math.max(0, (ts - t0) / 1000);
    tAnim = e;
    spin = (e * SPIN) % (Math.PI * 2);
    var g = (e - GATHER_AT) / GATHER_MS;
    gather = g <= 0 ? 0 : (g >= 1 ? 1 : g);
    var age = e - RING_AT;
    ring = age < 0 ? 9 : ((age % RING_PERIOD) / RING_MS) * RING_MAX;
    if (ring > RING_MAX) ring = 9;
    draw();
    schedule();
  }

  /* 画布就是首屏那张网，没有备胎了：量到真尺寸才开机，量不到（祖先 display:none、
     布局还没稳）就等下面的观察者再量。 */
  var booted = false;

  function boot() {
    booted = true;
    if (still) {
      /* 关了动效：不呼吸、不转、不放电波，就给一张静止的网
         —— 以前这里直接退回静态 SVG，看上去就像「GL 没生效」 */
      gather = 1;
      ring = 9;
      spin = 0;
      tAnim = 0;
      draw();
      return;
    }

    /* 亮出来之前先摆成一粒点 */
    draw();

    if (document.readyState === "complete") schedule();
    else window.addEventListener("load", schedule);

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (list) {
        for (var m = 0; m < list.length; m++) inView = list[m].isIntersecting;
        if (inView) schedule();
      }, { threshold: 0.01 }).observe(canvas);
    }

    document.addEventListener("visibilitychange", function () { if (!document.hidden) schedule(); });
  }

  /* 量到尺寸就开机；已经开过就补画一帧（缩放、换肤都走这里） */
  function ensure() {
    if (!resize()) return;
    if (!booted) boot();
    else if (!raf) draw();
  }

  ensure();

  if ("ResizeObserver" in window) {
    new ResizeObserver(function () { ensure(); }).observe(canvas);
  } else {
    window.addEventListener("resize", function () {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      ensure();
    });
  }

  /* 换肤只换颜色：重读令牌，停着的那一帧也补画一次（静止模式下也生效） */
  try {
    new MutationObserver(function () {
      readTokens();
      if (!raf) draw();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  } catch (e) { /* 老浏览器没有 MutationObserver 也没关系 */ }

  /* 给以后的自检留个口子（同页面脚本可读） */
  window.__heroField = {
    draw: draw,
    state: function () {
      return {
        engine: "2d",
        gather: gather, ring: ring, spin: spin, points: N, you: youIndex,
        segs: segs.length * 0.5, spacing: spacing,
        w: canvas.width, h: canvas.height, dpr: dpr
      };
    },
    /* 2D 画布随时可读（不像 WebGL 那样只有紧跟一次绘制之后才有数） */
    pixelStats: function () {
      var w = canvas.width, h = canvas.height, pix, lit = 0, orange = 0, m;
      if (!w || !h) return null;
      try { pix = ctx.getImageData(0, 0, w, h).data; } catch (e) { return null; }
      for (m = 0; m < pix.length; m += 4) {
        if (pix[m + 3] > 8) {
          lit++;
          if (pix[m] > pix[m + 2] + 40) orange++;
        }
      }
      return { w: w, h: h, lit: lit, orange: orange, ratio: lit / (w * h) };
    }
  };
})();
