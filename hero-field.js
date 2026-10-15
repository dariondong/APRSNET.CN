/* APRSNET.CN 首屏：一个点 → 发一次信标 → 一张网
   手写 WebGL1，零依赖、定种子（同一份网，每次打开都一样）。
   一开始 900 个点塌在一个点上，按下去发一次信标，点散开成球面，
   邻点之间自己拉出线来 —— 这才看出是「一张网」而不是一团雾；
   其中一粒是你的（橙的），一圈波纹从你这里漫过整张网。

   同一个 program 画两遍：先连线（uMode=1），再点（uMode=0），
   省掉第二套 uniform。点云里连的是最近邻，一眼能看出网格结构。

   没有 WebGL、关掉动效、或者上下文丢了，就什么都不做 ——
   留在 <figure class="hero-net"> 里的那张静态 SVG 顶上。 */
(function () {
  "use strict";

  var canvas = document.getElementById("heroField");
  if (!canvas || !canvas.getContext) return;
  var host = canvas.closest ? canvas.closest(".hero-net") : canvas.parentNode;
  if (!host) return;

  /* 系统说要减少动效时不开动画 —— 但仍然给一张静止的网（见文件末尾的 still 分支） */
  var still = false;
  try {
    var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    still = !!(mq && mq.matches);
  } catch (e) { still = false; }

  var opts = {
    alpha: true, premultipliedAlpha: false, antialias: false,
    depth: false, stencil: false, powerPreference: "low-power"
  };
  var gl = null;
  try {
    gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts);
  } catch (e) { gl = null; }
  if (!gl) return;

  /* ── 着色器 ──
     aPos  单位球面上的位置（物体空间）
     aMeta x = 尺寸系数, y = 错峰相位（同一次广播里各点先后不一样）
     aKind 1 = 你自己那一粒
     uMode 1 = 画线（不取样 gl_PointCoord），0 = 画点 */
  var VS = [
    "precision highp float;",
    "attribute vec3 aPos;",
    "attribute vec2 aMeta;",
    "attribute float aKind;",
    "uniform float uSpin;",
    "uniform float uGather;",
    "uniform float uTime;",
    "uniform float uDist;",
    "uniform float uProj;",
    "uniform float uAspect;",
    "uniform float uPxScale;",
    "uniform float uMinPx;",
    "uniform float uMaxSize;",
    "uniform float uRing;",
    "uniform float uMode;",
    "uniform vec3 uCloud;",
    "uniform vec3 uHot;",
    "uniform vec3 uYou;",
    "varying vec3 vCol;",
    "varying float vFade;",
    "void main() {",
    "  float ca = cos(uSpin);",
    "  float sa = sin(uSpin);",
    "  vec3 home = vec3(aPos.x * ca - aPos.z * sa, aPos.y, aPos.x * sa + aPos.z * ca);",
    "  float g = clamp((uGather - aMeta.y * 0.45) / 0.55, 0.0, 1.0);",
    "  g = g * g * (3.0 - 2.0 * g);",
    "  vec3 p = home * g;",
    "  float you = step(0.5, aKind);",
    "  float d = uDist - p.z;",
    "  float front = clamp(p.z * 0.9 + 0.55, 0.0, 1.0);",
    "  float tw = 0.5 + 0.5 * sin(uTime * (0.6 + 0.9 * aMeta.y) + aMeta.y * 24.0);",
    "  float bright = 0.42 + 0.58 * tw;",
    "  float pulse = 0.5 + 0.5 * sin(uTime * 1.5);",
    "  bright *= mix(1.0, 0.72 + 0.78 * pulse, you);",
    "  float q = (distance(normalize(home), normalize(uYou)) - uRing) * 3.4;",
    "  bright *= 1.0 + exp(-q * q) * 2.2 * g;",
    "  float vis = mix(1.0, 0.10 + 0.90 * front, g);",
    "  vis = max(vis, you * 0.5);",
    "  vFade = clamp(vis * bright, 0.0, 1.0);",
    "  vec3 base = mix(uCloud, uHot, you);",
    "  vCol = mix(uHot, base, g) * (0.78 + 0.42 * bright);",
    "  gl_PointSize = clamp(aMeta.x * uPxScale / max(d, 0.45) * mix(1.8, 1.0, g) * (0.88 + 0.28 * tw), uMinPx, uMaxSize);",
    "  vec2 sc = vec2(p.x * uProj / uAspect, p.y * uProj);",
    "  gl_Position = vec4(sc, d - uDist, d);",
    "}"
  ].join("\n");

  var FS = [
    "precision mediump float;",
    "varying vec3 vCol;",
    "varying float vFade;",
    "uniform float uMode;",
    "void main() {",
    "  float a = vFade;",
    "  if (uMode < 0.5) {",
    "    vec2 c = gl_PointCoord - vec2(0.5);",
    "    float r2 = dot(c, c);",
    "    if (r2 > 0.25) discard;",
    "    a *= 1.0 - smoothstep(0.18, 0.25, r2);",
    "  } else {",
    "    a *= 0.22;",
    "  }",
    "  gl_FragColor = vec4(vCol, a);",
    "}"
  ].join("\n");

  function compile(type, src) {
    var sh = gl.createShader(type);
    if (!sh) return null;
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) { gl.deleteShader(sh); return null; }
    return sh;
  }

  var vs = compile(gl.VERTEX_SHADER, VS);
  var fs = compile(gl.FRAGMENT_SHADER, FS);
  if (!vs || !fs) return;
  var prog = gl.createProgram();
  if (!prog) return;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;

  /* 每个 uniform 都在这里点一次名 —— .scratch/cn_field_check.py 会拿它和着色器里的声明对表 */
  var UNIFORMS = [
    "uSpin", "uGather", "uTime", "uDist", "uProj", "uAspect",
    "uPxScale", "uMinPx", "uMaxSize", "uRing", "uMode", "uCloud", "uHot", "uYou"
  ];
  var u = {};
  var i;
  for (i = 0; i < UNIFORMS.length; i++) u[UNIFORMS[i]] = gl.getUniformLocation(prog, UNIFORMS[i]);

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

  var STRIDE = 24;
  var data = new Float32Array(N * 6);
  var youIndex = 0, youBest = -2;
  for (i = 0; i < N; i++) {
    var y = 1 - (i + 0.5) * 2 / N;
    var r = Math.sqrt(Math.max(0, 1 - y * y));
    var a = i * GA;
    var dx = Math.cos(a) * r, dz = Math.sin(a) * r;
    var rad = 0.96 + 0.07 * rnd();
    var ph = rnd();
    /* 每隔十来个点给一粒稍大的，看上去像地图上的台站，不是均匀的一团 */
    var size = (0.019 + 0.007 * rnd()) * (i % 11 === 0 ? 1.5 : 1);
    var dot = dx * YOU[0] + y * YOU[1] + dz * YOU[2];
    if (dot > youBest) { youBest = dot; youIndex = i; }
    var o = i * 6;
    data[o] = dx * rad;
    data[o + 1] = y * rad;
    data[o + 2] = dz * rad;
    data[o + 3] = size;
    data[o + 4] = ph;
    data[o + 5] = 0;
  }
  data[youIndex * 6 + 3] = 0.052;
  data[youIndex * 6 + 5] = 1;

  var pointVbo = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, pointVbo);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);

  /* ── 邻点之间拉线：先量出近邻尺度，再按 1.7 倍连边（只看 i<j，天然不重复） ── */
  var near2 = new Float32Array(N);
  var a2, b2, d2, j, k, dx2, dy2, dz2;
  for (i = 0; i < N; i++) near2[i] = 8;
  for (i = 0; i < N; i++) {
    var oi = i * 6;
    for (j = i + 1; j < N; j++) {
      var oj = j * 6;
      dx2 = data[oi] - data[oj];
      dy2 = data[oi + 1] - data[oj + 1];
      dz2 = data[oi + 2] - data[oj + 2];
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
    var oa = i * 6;
    a2 = data[oa + 0]; b2 = data[oa + 1];
    var c2 = data[oa + 2];
    /* 先比单个轴，超出半径就直接跳过，不必每次都算完整平方和 */
    for (j = i + 1; j < N; j++) {
      var ob = j * 6;
      dx2 = a2 - data[ob];
      if (dx2 * dx2 > cut2) continue;
      dy2 = b2 - data[ob + 1];
      if (dy2 * dy2 > cut2) continue;
      dz2 = c2 - data[ob + 2];
      if (dz2 * dz2 > cut2) continue;
      d2 = dx2 * dx2 + dy2 * dy2 + dz2 * dz2;
      if (d2 <= cut2 && d2 > 0.000001) segs.push(i, j);
    }
  }
  var lineData = new Float32Array(segs.length * 6);
  for (k = 0; k < segs.length; k++) {
    var so = segs[k] * 6, to = k * 6;
    lineData[to] = data[so];
    lineData[to + 1] = data[so + 1];
    lineData[to + 2] = data[so + 2];
    lineData[to + 3] = data[so + 3];
    lineData[to + 4] = data[so + 4];
    lineData[to + 5] = data[so + 5];
  }
  var lineVbo = null;
  if (segs.length) {
    lineVbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, lineVbo);
    gl.bufferData(gl.ARRAY_BUFFER, lineData, gl.STATIC_DRAW);
  }

  gl.useProgram(prog);
  var aPos = gl.getAttribLocation(prog, "aPos");
  var aMeta = gl.getAttribLocation(prog, "aMeta");
  var aKind = gl.getAttribLocation(prog, "aKind");
  function bindAttribs(buf) {
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, STRIDE, 0);
    gl.enableVertexAttribArray(aMeta);
    gl.vertexAttribPointer(aMeta, 2, gl.FLOAT, false, STRIDE, 12);
    gl.enableVertexAttribArray(aKind);
    gl.vertexAttribPointer(aKind, 1, gl.FLOAT, false, STRIDE, 20);
  }

  /* ── 颜色：只从站上现成的两支令牌里取，不写死色号 ── */
  function hexToRgb(s) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(s);
    if (!m) return null;
    var h = m[1];
    if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
    var n = parseInt(h, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  function token(name, fallback) {
    var raw = "";
    try { raw = getComputedStyle(document.documentElement).getPropertyValue(name) || ""; } catch (e) { raw = ""; }
    return hexToRgb(raw.replace(/\s+/g, "")) || fallback;
  }
  /* 网用墨色（不是淡墨）—— 白底上一粒深点才干净，深浅交给前后层次去拉 */
  var col = [0.08, 0.09, 0.11], hot = [1, 0.42, 0];
  function readTokens() {
    col = token("--text", [0.08, 0.09, 0.11]);
    hot = token("--hot-bright", [1, 0.42, 0]);
  }
  readTokens();

  /* ── 画布尺寸：量 clientWidth/Height（CSS 变换不触发 resize，用缩放后的矩形会糊） ── */
  var FOV = 33 * Math.PI / 180;
  var PROJ = 1 / Math.tan(FOV / 2);
  var DIST = 3.8;
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var aspect = 4 / 3, pxScale = 400;

  function resize() {
    var cw = canvas.clientWidth || 0;
    var ch = canvas.clientHeight || 0;
    if (cw < 8 || ch < 8) return false;
    var w = Math.round(cw * dpr), h = Math.round(ch * dpr);
    if (w !== canvas.width || h !== canvas.height) {
      canvas.width = w;
      canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    aspect = cw / ch;
    pxScale = (h / 2) * PROJ;
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

  function draw() {
    if (!canvas.width || !canvas.height) return;
    gl.disable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(prog);
    gl.uniform1f(u.uSpin, spin);
    gl.uniform1f(u.uGather, gather);
    gl.uniform1f(u.uTime, tAnim);
    gl.uniform1f(u.uDist, DIST);
    gl.uniform1f(u.uProj, PROJ);
    gl.uniform1f(u.uAspect, aspect);
    gl.uniform1f(u.uPxScale, pxScale);
    gl.uniform1f(u.uMinPx, 1.0);
    gl.uniform1f(u.uMaxSize, 16.0);
    gl.uniform1f(u.uRing, ring);
    gl.uniform3f(u.uCloud, col[0], col[1], col[2]);
    gl.uniform3f(u.uHot, hot[0], hot[1], hot[2]);
    gl.uniform3f(u.uYou, YOU[0], YOU[1], YOU[2]);
    if (lineVbo) {
      gl.uniform1f(u.uMode, 1.0);
      bindAttribs(lineVbo);
      gl.drawArrays(gl.LINES, 0, segs.length);
    }
    gl.uniform1f(u.uMode, 0.0);
    bindAttribs(pointVbo);
    gl.drawArrays(gl.POINTS, 0, N);
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

  host.classList.add("is-3d");
  if (!resize()) {
    host.classList.remove("is-3d");
    return;
  }

  if (still) {
    /* 关了动效：不呼吸、不转、不放电波，就给一张静止的网
       —— 以前这里直接退回静态 SVG，看上去就像「GL 没生效」 */
    gather = 1;
    ring = 9;
    spin = 0;
    tAnim = 0;
    draw();
  } else {
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

  if ("ResizeObserver" in window) {
    new ResizeObserver(function () {
      if (resize() && !raf) draw();
    }).observe(canvas);
  } else {
    window.addEventListener("resize", function () {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (resize() && !raf) draw();
    });
  }

  /* 换肤只换颜色：重读令牌，停着的那一帧也补画一次（静止模式下也生效） */
  try {
    new MutationObserver(function () {
      readTokens();
      if (!raf) draw();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  } catch (e) { /* 老浏览器没有 MutationObserver 也没关系 */ }

  /* 上下文丢了就退回那张静态 SVG，别留一块空画布 */
  canvas.addEventListener("webglcontextlost", function (ev) {
    ev.preventDefault();
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    host.classList.remove("is-3d");
  }, false);

  /* 给以后的自检留个口子（同页面脚本可读） */
  window.__heroField = {
    state: function () {
      return {
        gather: gather, ring: ring, spin: spin, points: N, you: youIndex,
        segs: segs.length, spacing: spacing,
        w: canvas.width, h: canvas.height, dpr: dpr
      };
    },
    /* 没开 preserveDrawingBuffer，只有紧跟在一次绘制之后读才有数 */
    pixelStats: function () {
      var w = canvas.width, h = canvas.height, px, lit = 0, orange = 0;
      if (!w || !h) return null;
      px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      for (var m = 0; m < px.length; m += 4) {
        if (px[m + 3] > 8) lit++;
        if (px[m + 3] > 8 && px[m] > px[m + 2] + 40) orange++;
      }
      return { w: w, h: h, lit: lit, orange: orange, ratio: lit / (w * h) };
    }
  };
})();
