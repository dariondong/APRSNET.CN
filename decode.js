/* 一行 APRS 报文 → 拆开的字段，全在浏览器里做：不发请求，也不存。
   位置的三种编码（非压缩 / 压缩 / Mic-E）照 APRS101 与 aprslib 的写法，
   跟 APRSLocus 客户端里那份解析器同一套判断，模糊位置与 Mic-E 的位运算都在。 */
(function () {
  'use strict';

  var form = document.getElementById('dcForm');
  if (!form) return;
  var field = document.getElementById('dcInput');
  var out = document.getElementById('dcOut');

  // ── 基础 ──
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function splitHeader(line) {
    var cut = line.indexOf(':');
    if (cut < 0) return null;
    var head = line.slice(0, cut);
    var info = line.slice(cut + 1);
    var arrow = head.indexOf('>');
    if (arrow < 1) return null;
    var src = head.slice(0, arrow).trim().toUpperCase();
    var rest = head.slice(arrow + 1);
    var parts = rest.split(',');
    return {
      src: src,
      dst: (parts.shift() || '').trim().toUpperCase(),
      path: parts.map(function (p) { return p.trim(); }).filter(Boolean),
      info: info
    };
  }

  // ── 位置：非压缩 ──
  var NORMAL = /^(\d{2})([0-9 ]{2}\.[0-9 ]{2})([NnSs])([\/\\0-9A-Z])(\d{3})([0-9 ]{2}\.[0-9 ]{2})([EeWw])([\x21-\x7e])([\s\S]*)$/;

  function parseNormal(s) {
    var m = NORMAL.exec(s);
    if (!m) return null;
    var latDeg = parseInt(m[1], 10);
    var latMin = m[2];
    var lngDeg = parseInt(m[5], 10);
    var lngMin = m[6];
    var amb = latMin.split('').filter(function (c) { return c === ' '; }).length;
    if (amb !== lngMin.split('').filter(function (c) { return c === ' '; }).length) return null;
    if (amb >= 4) { latMin = '30'; lngMin = '30'; }
    else if (amb > 0) { latMin = latMin.replace(' ', '5'); lngMin = lngMin.replace(' ', '5'); }
    if (latDeg > 89 || lngDeg > 179) return null;
    var lat = latDeg + parseFloat(latMin) / 60;
    var lng = lngDeg + parseFloat(lngMin) / 60;
    if (m[3] === 'S' || m[3] === 's') lat = -lat;
    if (m[7] === 'W' || m[7] === 'w') lng = -lng;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
    return {
      lat: lat, lon: lng, table: m[4], symbol: m[8], rest: m[9],
      format: '非压缩', ambiguity: amb
    };
  }

  // ── 位置：压缩（13 字符固定段）──
  var COMPRESSED = /^[\/\\A-Za-j][!-|]{8}[!-{}][ -|]{3}/;

  function base91(text) {
    var n = 0;
    for (var i = 0; i < text.length; i++) {
      var c = text.charCodeAt(i);
      if (c < 0x21 || c > 0x7b) return 0;
      n = n * 91 + (c - 33);
    }
    return n;
  }

  function parseCompressed(s) {
    if (!COMPRESSED.test(s) || s.length < 13) return null;
    var c = s.slice(0, 13);
    var lat = 90 - (base91(c.slice(1, 5)) / 380926);
    var lng = -180 + (base91(c.slice(5, 9)) / 190463);
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
    var c1 = c.charCodeAt(10) - 33;
    var s1 = c.charCodeAt(11) - 33;
    var type = c.charCodeAt(12) - 33;
    var course = null, speed = null, alt = null;
    if ((type & 0x18) === 0x10) {
      alt = Math.pow(1.002, c1 * 91 + s1) * 0.3048;
    } else if (c1 >= 0 && c1 <= 89) {
      course = c1 === 0 ? 360 : c1 * 4;
      speed = (Math.pow(1.08, s1) - 1) * 1.852;
    }
    return {
      lat: lat, lon: lng, table: c[0], symbol: c[9], rest: s.slice(13),
      format: '压缩', course: course, speed: speed, alt: alt, ambiguity: 0
    };
  }

  // ── 位置：Mic-E ──
  function parseMice(destRaw, body) {
    var dest = String(destRaw || '').split('-')[0];
    if (dest.length !== 6 || !/^[0-9A-Z]{3}[0-9L-Z]{3}$/.test(dest)) return null;
    var info = body.slice(1);
    if (info.length < 8) return null;

    var digits = '';
    for (var i = 0; i < 6; i++) {
      var ch = dest[i];
      var code = ch.charCodeAt(0);
      if (ch === 'K' || ch === 'L' || ch === 'Z') digits += ' ';
      else if (code > 76) digits += String.fromCharCode(code - 32);
      else if (code > 57) digits += String.fromCharCode(code - 17);
      else digits += ch;
    }
    var ambMatch = /^(\d+)( *)$/.exec(digits);
    if (!ambMatch) return null;
    var amb = ambMatch[2].length;
    var d = digits.split('');
    if (amb > 0) {
      if (amb >= 4) d[2] = '3';
      else d[6 - amb] = '5';
    }
    d = d.join('');
    var latMin = parseFloat((d.slice(2, 4) + '.' + d.slice(4, 6)).replace(/ /g, '0'));
    var lat = parseInt(d.slice(0, 2), 10) + latMin / 60;
    if (dest.charCodeAt(3) <= 0x4c) lat = -lat;

    var lng = info.charCodeAt(0) - 28;
    if (dest.charCodeAt(4) >= 0x50) lng += 100;
    if (lng >= 180 && lng <= 189) lng -= 80;
    if (lng >= 190 && lng <= 199) lng -= 190;
    var lngMin = info.charCodeAt(1) - 28;
    if (lngMin >= 60) lngMin -= 60;
    lngMin += (info.charCodeAt(2) - 28) / 100;
    if (amb === 4) lngMin = 30;
    else if (amb === 3) lngMin = (Math.floor(lngMin / 10) + 0.5) * 10;
    else if (amb === 2) lngMin = Math.floor(lngMin) + 0.5;
    else if (amb === 1) lngMin = (Math.floor(lngMin * 10) + 0.5) / 10;
    lng = lng + lngMin / 60;
    if (dest.charCodeAt(5) >= 0x50) lng = -lng;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;

    var speed = (info.charCodeAt(3) - 28) * 10;
    var course = info.charCodeAt(4) - 28;
    var q = Math.floor(course / 10);
    course = course - q * 10;
    course = course * 100 + (info.charCodeAt(5) - 28);
    speed += q;
    if (speed >= 800) speed -= 800;
    if (course >= 400) course -= 400;
    speed = speed * 1.852;

    var rest = info.slice(8);
    var tel = /^('[0-9a-fA-F]{10}|`[0-9a-fA-F]{4})/.exec(rest);
    if (tel) rest = rest.slice(tel[0].length);
    if (rest && (rest[0] === ']' || rest[0] === '}')) rest = rest.slice(1);

    return {
      lat: lat, lon: lng, table: info[7], symbol: info[6], rest: rest,
      format: 'Mic-E', course: course, speed: speed, ambiguity: amb
    };
  }

  // ── 位置总入口 ──
  function positionOf(dti, body, dest) {
    if (dti === "'" || dti === '`') return parseMice(dest, body);
    if (dti !== '!' && dti !== '=' && dti !== '/' && dti !== '@') return null;
    var s = body.slice(1);
    var stamp = '';
    if (dti === '/' || dti === '@') {
      if (s.length < 8) return null;
      stamp = s.slice(0, 7);
      s = s.slice(7);
    }
    var pos = parseCompressed(s) || parseNormal(s);
    if (pos) pos.stamp = stamp;
    return pos;
  }

  // ── 时间戳（DDHHMMz / HHMMSSh / DDHHMM/）──
  function stampText(t) {
    if (!t || t.length !== 7) return '';
    var a = t.slice(0, 2), b = t.slice(2, 4), c = t.slice(4, 6), kind = t[6];
    if (kind === 'z' || kind === 'Z') return '第 ' + (+a) + ' 天 ' + b + ':' + c + ' UTC';
    if (kind === 'h' || kind === 'H') return a + ':' + b + ':' + c + ' UTC';
    if (kind === '/') return '第 ' + (+a) + ' 天 ' + b + ':' + c + ' 本地时间';
    return t;
  }

  // ── 备注里的附加段 ──
  function extrasOf(rest) {
    var found = {};
    var body = rest || '';
    var alt = /^(.*?)\/A=(-?\d{5,6})(.*)$/.exec(body);
    if (alt) {
      var ft = parseInt(alt[2], 10);
      if (!isNaN(ft)) found.alt = ft * 0.3048;
      body = alt[1] + alt[3];
    }
    // CsT：紧接符号的 ddd/sss，只认备注开头
    var cst = /^\s*(\d{3})\/(\d{2,3})(?![0-9])/.exec(body);
    if (cst) {
      var cv = parseInt(cst[1], 10), sv = parseInt(cst[2], 10);
      if (cv >= 1 && cv <= 360) found.course = cv;
      if (sv !== 0) found.speed = sv * 1.852;
      body = body.replace(cst[0], '');
    }
    found.rest = body.trim();
    var phg = /\bPHG(\d)(\d)(\d)(\d)/.exec(body);
    if (phg) {
      found.phg = '天线增益 ' + (phg[1] * 1.5 - 1.5) + ' dB，' + (20 * Math.log(phg[2] * 10) / Math.LN10 / 2).toFixed(0) +
        ' 米高，方向 ' + (phg[3] * 45) + ' 度，发射功率 ' + Math.pow(2, +phg[4]) + ' 瓦';
    }
    return found;
  }

  // ── 网格 ──
  function gridOf(lat, lon) {
    var A = 'ABCDEFGHIJKLMNOPQRSTUVWX';
    var x = lon + 180, y = lat + 90;
    if (x < 0 || x >= 360 || y < 0 || y > 180) return '';
    var fx = Math.floor(x / 20), fy = Math.floor(y / 10);
    var restX = x - fx * 20, restY = y - fy * 10;
    var sx = Math.floor(restX / 2), sy = Math.floor(restY);
    var subX = Math.floor((restX - sx * 2) * 12), subY = Math.floor((restY - sy) * 24);
    return A[fx] + A[fy] + sx + sy + A[subX] + A[subY];
  }

  // ── 气象 ──
  // 气象包与符号 _ 的位置包，备注是一串「字母 + 数字」字段，顺次扫一遍就够。
  // 只在确定是气象的报文上调用，免得把普通备注里的字母数字误读成读数。
  function weatherOf(text) {
    var fields = {};
    var re = /c(\d{3})|s(\d{3})|g(\d{3})|t(-?\d{3})|r(\d{3})|p(\d{3})|P(\d{3})|h(\d{2})|b(\d{5})/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m[1]) fields['风向'] = parseInt(m[1], 10) + ' 度';
      else if (m[2]) fields['风速'] = parseInt(m[2], 10) + ' mph';
      else if (m[3]) fields['阵风'] = parseInt(m[3], 10) + ' mph';
      else if (m[4]) {
        var f = parseInt(m[4], 10);
        fields['气温'] = f + ' °F（' + ((f - 32) / 1.8).toFixed(1) + ' °C）';
      } else if (m[5]) fields['一小时雨量'] = (parseInt(m[5], 10) / 100).toFixed(2) + ' 英寸';
      else if (m[6]) fields['24 小时雨量'] = (parseInt(m[6], 10) / 100).toFixed(2) + ' 英寸';
      else if (m[7]) fields['累计雨量'] = (parseInt(m[7], 10) / 100).toFixed(2) + ' 英寸';
      else if (m[8]) fields['湿度'] = parseInt(m[8], 10) + ' %';
      else if (m[9]) fields['气压'] = (parseInt(m[9], 10) / 10).toFixed(1) + ' hPa';
    }
    return fields;
  }

  // ── 类型 ──
  var TYPES = {
    '!': '位置，不带时间戳', '=': '位置，不带时间戳，可收发消息',
    '/': '位置，带时间戳', '@': '位置，带时间戳，可收发消息',
    "'": 'Mic-E 位置', '`': 'Mic-E 位置（带状态位）',
    ':': '消息', ';': '对象', ')': '条目', '>': '状态',
    'T': '遥测', '_': '气象，不带位置', '#': 'Peet Bros 气象',
    '*': 'Peet Bros 气象，不带时间', '$': '原始 GPS（NMEA）',
    '?': '查询', '{': '用户自定义', '}': '第三条协议', '[': 'Maidenhead 定位信标'
  };

  // ── 主流程 ──
  function decode(line) {
    var text = String(line || '').replace(/\r?\n/g, '').trim();
    if (!text) return { error: '先贴一行报文。' };
    var head = splitHeader(text);
    if (!head) return { error: '这不像一行 TNC2 报文：开头要是「呼号>目标:内容」。' };

    var rows = [];
    function put(k, v) { if (v !== '' && v != null) rows.push([k, v]); }

    put('源呼号', head.src);
    put('目标', head.dst);
    put('TOCALL', head.dst.split('-')[0]);
    put('路径', head.path.length ? head.path.join(' · ') : '源站直接发出，没有中继');

    var path = head.path.join(',').toUpperCase();
    var onIs = /TCPIP|TCPXX/.test(path) || /qA/i.test(path);
    put('经过 APRS-IS', onIs ? '是' : '否');
    var q = /,q([A-Za-z]{2}),/.exec(',' + head.path.join(',') + ',');
    if (q) put('q 构造', 'q' + q[1].toUpperCase() + '，由 ' + (head.path[head.path.length - 1] || '') + ' 写上去');

    var info = head.info;
    var third = info[0] === '}' ? splitHeader(info.slice(1)) : null;
    if (third) {
      put('第三方转发', third.src + ' > ' + third.dst);
      info = third.info;
      head.src = third.src;
      head.dst = third.dst;
    }

    if (!info) return { error: '冒号后面是空的，没有内容可解。' };
    var dti = info[0];
    put('报文类型', (TYPES[dti] || '认不出的类型') + '（' + dti + '）');

    var pos = positionOf(dti, info, head.dst);

    // 消息
    if (dti === ':') {
      var to = info.slice(1, 10).replace(/\s+$/, '');
      var body2 = info.slice(11);
      var mid = /\{([0-9A-Za-z]{1,5})$/.exec(body2);
      put('收件人', to);
      put('正文', mid ? body2.slice(0, mid.index) : body2);
      put('消息编号', mid ? mid[1] : '没带');
      if (/^(ack|rej)/i.test(body2)) put('这是一条', '对方回的确认' + (/^ack/i.test(body2) ? '（ack）' : '（rej 拒收）'));
    } else if (dti === '>') {
      put('状态正文', info.slice(1));
    } else if (dti === 'T') {
      var tel = /^T#([0-9A-Z]{3}),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([01]{8})/.exec(info);
      if (tel) {
        put('遥测序号', tel[1]);
        put('模拟量', 'A1 ' + tel[2] + ' · A2 ' + tel[3] + ' · A3 ' + tel[4] + ' · A4 ' + tel[5] + ' · A5 ' + tel[6]);
        put('数字量', tel[7]);
      } else {
        put('遥测', info.slice(1));
      }
    } else if (dti === '_' || dti === '#') {
      var wt = info.slice(1);
      if (dti === '_' && wt.length >= 6) {
        put('报文里的时间', stampText(wt.slice(0, 6) + 'z'));
        wt = wt.slice(6);
      }
      var wx = weatherOf(wt);
      Object.keys(wx).forEach(function (k) { put(k, wx[k]); });
    } else if (dti === '$') {
      put('NMEA', info.slice(1));
    } else if (dti === '?' || dti === '{') {
      put('内容', info.slice(1));
    } else if (dti === ';' || dti === ')') {
      put('名称', (dti === ';' ? info.slice(1, 10) : info.slice(1, info.indexOf('_') > 0 ? info.indexOf('_') : 4)).trim());
    }

    if (pos) {
      put('位置格式', pos.format + (pos.ambiguity ? '，模糊 ' + pos.ambiguity + ' 位' : ''));
      if (pos.stamp) put('报文里的时间', stampText(pos.stamp));
      put('纬度', pos.lat.toFixed(5) + '°' + (pos.lat >= 0 ? 'N' : 'S'));
      put('经度', pos.lon.toFixed(5) + '°' + (pos.lon >= 0 ? 'E' : 'W'));
      var grid = gridOf(pos.lat, pos.lon);
      if (grid) put('网格', grid);
      put('符号', '表 ' + (pos.table === '/' ? '/' : (pos.table === '\\' ? '\\' : pos.table)) +
        '，代码 ' + pos.symbol);
      var ex = extrasOf(pos.rest);
      if (pos.course == null && ex.course != null) pos.course = ex.course;
      if (pos.speed == null && ex.speed != null) pos.speed = ex.speed;
      if (pos.course) put('航向', pos.course.toFixed(0) + ' 度');
      if (pos.speed != null) put('速度', pos.speed.toFixed(1) + ' km/h');
      if (pos.alt != null || ex.alt != null) put('海拔', (pos.alt != null ? pos.alt : ex.alt).toFixed(0) + ' 米');
      if (ex.phg) put('PHG', ex.phg);
      if (ex.rest && pos.symbol !== '_') put('备注', ex.rest);
      var wx2 = (dti === '_' || dti === '#' || pos.symbol === '_') ? weatherOf(ex.rest || '') : {};
      Object.keys(wx2).forEach(function (k) { if (!rows.some(function (r) { return r[0] === k; })) put(k, wx2[k]); });
    }

    put('整行长度', text.length + ' 字符');

    return { rows: rows };
  }

  function render(text) {
    var result = decode(text);
    if (result.error) {
      out.className = 'idq-out bad';
      out.textContent = result.error;
      return;
    }
    var html = '<dl class="dc-fields">';
    result.rows.forEach(function (row) {
      html += '<div class="dc-field"><dt class="dc-k">' + esc(row[0]) + '</dt>' +
        '<dd class="dc-v">' + esc(row[1]) + '</dd></div>';
    });
    html += '</dl>';
    out.className = 'idq-out good';
    out.innerHTML = html;
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    render(field.value);
  });
  form.addEventListener('reset', function () {
    out.innerHTML = '';
    out.className = 'idq-out';
  });

  var chips = document.querySelectorAll('[data-dc-sample]');
  Array.prototype.forEach.call(chips, function (chip) {
    chip.addEventListener('click', function () {
      field.value = chip.getAttribute('data-dc-sample');
      render(field.value);
      field.focus();
    });
  });

  window.__decodePacket = decode;
})();
