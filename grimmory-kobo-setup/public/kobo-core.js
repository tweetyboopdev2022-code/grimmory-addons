/* Kobo setup: the parts that do not touch the page or the disk, so tests can run them in Node.
 *
 * Kobo eReader.conf is a Qt settings file
 * the Kobo rewrites itself, so it is never parsed and re-serialised: the one line that matters is
 * found and replaced, and every other byte is handed back as it was.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.KoboCore = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var SECTION = 'OneStoreServices';
  var KEY = 'api_endpoint';
  var STOCK_PREFIX = 'https://storeapi.kobo.com';

  var PATHS = {
    conf: '.kobo/Kobo/Kobo eReader.conf',
    version: '.kobo/version',
    db: '.kobo/KoboReader.sqlite',
    backups: '.adds/grimmory-setup/backups'
  };

  function ConfError(message) { var e = new Error(message); e.name = 'ConfError'; return e; }

  var SECTION_RE = /^\s*\[([^\]]+)\]\s*$/;

  function sectionBounds(lines, section) {
    var start = -1;
    for (var i = 0; i < lines.length; i++) {
      var m = SECTION_RE.exec(lines[i].replace(/\r$/, ''));
      if (m) {
        if (start >= 0) return [start, i];
        if (m[1] === section) start = i;
      }
    }
    return start >= 0 ? [start, lines.length] : null;
  }

  function keyLines(lines, bounds, key) {
    var re = new RegExp('^\\s*' + key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*=');
    var hits = [];
    for (var i = bounds[0] + 1; i < bounds[1]; i++) if (re.test(lines[i])) hits.push(i);
    return hits;
  }

  function findValue(text, section, key) {
    var lines = text.split('\n');
    var b = sectionBounds(lines, section || SECTION);
    if (!b) return null;
    var hits = keyLines(lines, b, key || KEY);
    if (!hits.length) return null;
    return lines[hits[0]].split('=').slice(1).join('=').trim();
  }

  function hasSection(text, section) { return sectionBounds(text.split('\n'), section || SECTION) !== null; }

  /* Returns {text, what}: what is 'replaced', 'inserted' or 'unchanged'. */
  function setValue(text, value, section, key) {
    section = section || SECTION; key = key || KEY;
    var lines = text.split('\n');
    var b = sectionBounds(lines, section);
    if (!b) {
      throw ConfError('This Kobo\'s settings file has no [' + section + '] section, so it has not finished ' +
        'its first start-up. Finish setting up the Kobo, then try again. Nothing was changed.');
    }
    var hits = keyLines(lines, b, key);
    if (hits.length > 1) {
      throw ConfError(key + ' appears ' + hits.length + ' times in the Kobo\'s settings; refusing to guess ' +
        'which one to change. Nothing was changed.');
    }
    var crCount = 0;
    for (var j = 0; j < lines.length; j++) if (/\r$/.test(lines[j])) crCount++;
    var cr = crCount > Math.floor(lines.length / 2) ? '\r' : '';
    if (hits.length) {
      var i = hits[0];
      var current = lines[i].split('=').slice(1).join('=').trim();
      if (current === value) return { text: text, what: 'unchanged' };
      lines[i] = key + '=' + value + (/\r$/.test(lines[i]) ? '\r' : '');
      return { text: lines.join('\n'), what: 'replaced' };
    }
    // Some firmware drops the key; put it back at the end of the section.
    var last = b[1] - 1;
    while (last > b[0] && !lines[last].trim()) last--;
    lines.splice(last + 1, 0, key + '=' + value + cr);
    return { text: lines.join('\n'), what: 'inserted' };
  }

  function isStock(value) { return !!value && value.indexOf(STOCK_PREFIX) === 0; }

  var TOKEN_RE = /^[A-Za-z0-9_-]{8,}$/;

  function endpointFor(origin, token) {
    if (!TOKEN_RE.test(token || '')) throw ConfError('Grimmory returned a sync key that does not look right.');
    var o = String(origin).replace(/\/+$/, '');
    if (!/^https?:\/\/[^\s"'\\,;/]+$/.test(o)) throw ConfError('The server address does not look right: ' + o);
    return o + '/api/kobo/' + token;
  }

  function tokenOf(endpoint) {
    var m = /\/api\/kobo\/([^/\s?#]+)\/?$/.exec(endpoint || '');
    return m ? m[1] : null;
  }

  function maskEndpoint(endpoint) {
    if (!endpoint) return '(none)';
    if (isStock(endpoint)) return endpoint;
    var t = tokenOf(endpoint);
    if (!t) return endpoint.replace(/^(https?:\/\/[^/]+).*$/, '$1/...');
    var shown = t.length >= 12 ? t.slice(0, 4) + '...' + t.slice(-4) : t.slice(0, 2) + '...';
    return endpoint.slice(0, endpoint.lastIndexOf(t)) + shown;
  }

  /* What the current endpoint means, in words for a person. */
  function describeEndpoint(endpoint, origin) {
    if (endpoint === null || endpoint === undefined) return 'not set';
    if (isStock(endpoint)) return 'Kobo\'s own store (not connected to Grimmory)';
    var o = String(origin || '').replace(/\/+$/, '');
    if (o && endpoint.indexOf(o + '/api/kobo/') === 0) return 'this Grimmory (' + maskEndpoint(endpoint) + ')';
    return 'another server (' + maskEndpoint(endpoint) + ')';
  }

  var BUILD_RE = /^\d+\.\d+\.\d{4,}$/;

  /* .kobo/version is one comma-separated line: serial, versions..., model id. The firmware is the
     field shaped like a build number (4.45.23697); the hardware fields beside it look like 4.9.77. */
  function parseVersion(text) {
    var f = String(text || '').trim().split(',').map(function (s) { return s.trim(); });
    if (!f.length || !f[0]) throw ConfError('This does not look like a Kobo: its version file is empty.');
    var firmware = '';
    for (var i = 1; i < f.length; i++) if (BUILD_RE.test(f[i])) { firmware = f[i]; break; }
    if (!firmware && f.length > 2) firmware = f[2];
    return { serial: f[0], firmware: firmware, model: f.length > 1 ? f[f.length - 1] : '' };
  }

  function firmwareMajor(firmware) {
    var n = parseInt(String(firmware || '').split('.')[0], 10);
    return isNaN(n) ? null : n;
  }

  var APPLEDOUBLE = [0x00, 0x05, 0x16, 0x07];
  function isAppleDouble(bytes) {
    if (!bytes || bytes.length < 4) return false;
    for (var i = 0; i < 4; i++) if (bytes[i] !== APPLEDOUBLE[i]) return false;
    return true;
  }

  function stamp(date) {
    var d = date || new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  }

  /* ---- zip (stored, no compression): the "download a backup" file ---- */
  var CRC_TABLE = (function () {
    var t = new Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    var c = 0xFFFFFFFF;
    for (var i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  function utf8(s) { return new TextEncoder().encode(s); }

  /* files: [{name, data: Uint8Array, date?: Date}] -> Uint8Array. Zip64 is not needed: a Kobo's
     settings, database and mods are far under 4 GB. */
  function makeZip(files) {
    var chunks = [], central = [], offset = 0;
    function u16(v) { return [v & 0xFF, (v >>> 8) & 0xFF]; }
    function u32(v) { return [v & 0xFF, (v >>> 8) & 0xFF, (v >>> 16) & 0xFF, (v >>> 24) & 0xFF]; }
    files.forEach(function (f) {
      var name = utf8(f.name), data = f.data, crc = crc32(data);
      var d = f.date || new Date();
      var time = (d.getHours() << 11) | (d.getMinutes() << 5) | Math.floor(d.getSeconds() / 2);
      var day = ((Math.max(d.getFullYear(), 1980) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
      var head = [].concat([0x50, 0x4B, 0x03, 0x04], u16(20), u16(0x0800), u16(0), u16(time), u16(day),
        u32(crc), u32(data.length), u32(data.length), u16(name.length), u16(0));
      chunks.push(new Uint8Array(head), name, data);
      central.push(new Uint8Array([].concat([0x50, 0x4B, 0x01, 0x02], u16(20), u16(20), u16(0x0800), u16(0),
        u16(time), u16(day), u32(crc), u32(data.length), u32(data.length), u16(name.length), u16(0), u16(0),
        u16(0), u16(0), u32(0), u32(offset))), name);
      offset += head.length + name.length + data.length;
    });
    var cdSize = central.reduce(function (s, c) { return s + c.length; }, 0);
    var end = new Uint8Array([].concat([0x50, 0x4B, 0x05, 0x06], u16(0), u16(0), u16(files.length),
      u16(files.length), u32(cdSize), u32(offset), u16(0)));
    var all = chunks.concat(central, [end]);
    var total = all.reduce(function (s, c) { return s + c.length; }, 0);
    var out = new Uint8Array(total), pos = 0;
    all.forEach(function (c) { out.set(c, pos); pos += c.length; });
    return out;
  }

  /* ---- the on-Kobo backup every tool here writes before it changes anything ----
     .adds/grimmory-setup/backups/<stamp>/manifest.json and files/<path> for each file that existed.
     The page, the Mac file and the Windows file all write this same layout, so any of them can
     undo a change made by another. */
  function manifest(info, entries) {
    return {
      version: 1,
      created: (info.date || new Date()).toISOString(),
      tool: info.tool,
      user: info.user || '',
      serial: info.serial || '',
      changes: info.changes || [],
      entries: entries.map(function (e) { return { path: e.path, existed: !!e.existed }; })
    };
  }

  /* What undoing a manifest does, in order: put back what existed, remove what was added. */
  function undoSteps(m) {
    if (!m || m.version !== 1 || !Array.isArray(m.entries)) throw ConfError('That backup is not one this page understands.');
    return m.entries.map(function (e) {
      if (typeof e.path !== 'string' || !e.path || /(^|\/)\.\.(\/|$)/.test(e.path) || e.path.charAt(0) === '/') {
        throw ConfError('That backup lists a file outside the Kobo; refusing to use it.');
      }
      return { path: e.path, action: e.existed ? 'restore' : 'remove' };
    });
  }

  return {
    SECTION: SECTION, KEY: KEY, PATHS: PATHS,
    findValue: findValue, hasSection: hasSection, setValue: setValue, isStock: isStock,
    endpointFor: endpointFor, tokenOf: tokenOf, maskEndpoint: maskEndpoint, describeEndpoint: describeEndpoint,
    parseVersion: parseVersion, firmwareMajor: firmwareMajor,
    isAppleDouble: isAppleDouble, stamp: stamp, crc32: crc32, makeZip: makeZip,
    manifest: manifest, undoSteps: undoSteps
  };
});
