/* 呼号 → APRS-IS passcode，全在浏览器里算：不发请求、不存东西。
   算法是各家开源客户端通用的那一套（0x73e2 起头，逐字符异或，取低 15 位）。
   官方文档没有公开它；它只用来登录自己的呼号。 */
(function () {
  'use strict';

  var form = document.getElementById('pcForm');
  if (!form) return;
  var field = document.getElementById('pcInput');
  var out = document.getElementById('pcOut');

  function passcode(call) {
    var hash = 0x73e2;
    for (var i = 0; i < call.length; i++) {
      hash ^= (i % 2 === 0) ? (call.charCodeAt(i) << 8) : call.charCodeAt(i);
    }
    return hash & 0x7fff;
  }

  // 三种写法都认：BG7LZQ / BG7LZQ-9 / 一整行 user BG7LZQ pass … vers …
  function callOf(raw) {
    var s = String(raw || '').toUpperCase().replace(/[^A-Z0-9 -]+/g, ' ').trim();
    if (!s) return '';
    var parts = s.split(/\s+/);
    if (parts[0] === 'USER' && parts.length > 1) parts = parts.slice(1);
    return parts[0].split('-')[0];
  }

  function fail(msg) {
    out.className = 'idq-out bad';
    out.textContent = msg;
  }

  function hit(base, code, hadSsid) {
    out.className = 'idq-out good';
    out.innerHTML =
      '<div class="tlq-hit">' +
        '<div class="tlq-tag">PASSCODE</div>' +
        '<div class="tlq-code">' + base + '</div>' +
        '<div class="tlq-name">' + code + '</div>' +
        '<div class="tlq-meta">登录行：user ' + base + ' pass ' + code +
          ' vers 软件名 版本</div>' +
        (hadSsid ? '<div class="tlq-note">带 SSID 也算得出同一个数：' +
          '登录行里写 ' + base + ' 就行。</div>' : '') +
      '</div>';
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var raw = String(field.value || '');
    var base = callOf(raw);
    if (!base) {
      fail('先填上呼号。');
      return;
    }
    if (!/^[A-Z0-9]{3,9}$/.test(base)) {
      fail('这不是呼号：呼号本体是 3 到 9 位字母数字，SSID 用 - 隔开。');
      return;
    }
    hit(base, passcode(base), /-/.test(raw));
  });

  form.addEventListener('reset', function () {
    setTimeout(function () {
      out.className = 'idq-out';
      out.textContent = '';
    }, 0);
  });
})();
