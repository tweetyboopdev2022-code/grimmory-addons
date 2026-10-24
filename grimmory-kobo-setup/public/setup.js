/* Kobo setup page: connects a plugged-in Kobo to this Grimmory, from Chrome or Edge.
 *
 * Turns on Grimmory's Kobo sync for the signed-in account, writes the sync address into the Kobo's
 * settings and tidies macOS junk files, through the browser's File System Access API. Before changing anything it keeps a
 * copy of every file it will change on the Kobo itself (.adds/grimmory-setup/backups/<time>/), and
 * the Undo list puts any of those back. Plain JS, no dependencies.
 */
(function () {
  'use strict';
  var C = window.KoboCore, P = C.PATHS;
  var ORIGIN = location.origin;

  var state = { me: null, settings: null, canSync: true, kobo: null, info: null, scan: null, busy: false };

  function $(id) { return document.getElementById(id); }
  function show(id, on) { $(id).hidden = !on; }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  // ---- Grimmory -------------------------------------------------------------
  function token() { try { return localStorage.getItem('accessToken_Internal') || ''; } catch (e) { return ''; } }

  function api(method, path, body) {
    var opts = { method: method, headers: { Authorization: 'Bearer ' + token() } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(ORIGIN + path, opts).then(function (r) {
      return r.text().then(function (t) {
        var j = null; try { j = t ? JSON.parse(t) : null; } catch (e) { /* not JSON */ }
        return { status: r.status, body: j };
      });
    });
  }

  // ---- the Kobo's files, through the directory the person picked -------------
  function parts(path) { return path.split('/').filter(Boolean); }

  async function dirOf(root, path, create) {
    var p = parts(path), d = root;
    for (var i = 0; i < p.length - 1; i++) d = await d.getDirectoryHandle(p[i], { create: !!create });
    return { dir: d, name: p[p.length - 1] };
  }

  function missing(e) { return e && (e.name === 'NotFoundError' || e.name === 'TypeMismatchError'); }

  async function readFile(root, path) {
    try {
      var x = await dirOf(root, path, false);
      var fh = await x.dir.getFileHandle(x.name);
      return new Uint8Array(await (await fh.getFile()).arrayBuffer());
    } catch (e) { if (missing(e)) return null; throw e; }
  }

  async function readText(root, path) {
    var b = await readFile(root, path);
    return b === null ? null : new TextDecoder('utf-8').decode(b);
  }

  async function writeFile(root, path, data) {
    var x = await dirOf(root, path, true);
    var fh = await x.dir.getFileHandle(x.name, { create: true });
    var w = await fh.createWritable();      // written beside the file, swapped in on close
    await w.write(typeof data === 'string' ? new TextEncoder().encode(data) : data);
    await w.close();
  }

  async function removeEntry(root, path, recursive) {
    try {
      var x = await dirOf(root, path, false);
      await x.dir.removeEntry(x.name, { recursive: !!recursive });
      return true;
    } catch (e) { if (missing(e)) return false; throw e; }
  }

  async function exists(root, path) { return (await readFile(root, path)) !== null; }

  /* Every file under `path`, as paths relative to the Kobo. */
  async function listFiles(root, path, skip) {
    var out = [];
    var start;
    try { start = path ? (await dirOf(root, path + '/x', false)).dir : root; } catch (e) { if (missing(e)) return out; throw e; }
    async function walk(d, prefix) {
      for await (var entry of d.values()) {
        var rel = prefix + entry.name;
        if (skip && skip(rel, entry.kind)) continue;
        if (entry.kind === 'directory') await walk(entry, rel + '/');
        else out.push(rel);
      }
    }
    await walk(start, path ? path + '/' : '');
    return out;
  }

  function same(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  // ---- step 1: pick the Kobo ------------------------------------------------
  async function pick() {
    show('pick-error', false);
    var root;
    try {
      root = await window.showDirectoryPicker({ id: 'kobo', mode: 'readwrite' });
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      return pickError('The browser would not open that folder: ' + (e && e.message || e));
    }
    if (root.name === '.kobo' || root.name === '.adds') {
      return pickError('That is a folder inside the Kobo. Choose the whole drive, KOBOeReader, instead.');
    }
    var version = await readText(root, P.version);
    if (version === null) {
      return pickError('"' + root.name + '" is not a Kobo: it has no .kobo/version file. Choose the drive called KOBOeReader.');
    }
    var perm = await root.requestPermission({ mode: 'readwrite' });
    if (perm !== 'granted') return pickError('The page needs permission to edit files on the Kobo. Choose the Kobo again and click Allow.');
    try { state.info = C.parseVersion(version); } catch (e) { return pickError(e.message); }
    state.kobo = root;
    await inspect();
  }

  function pickError(msg) { $('pick-error').textContent = msg; show('pick-error', true); }

  /* What is on the Kobo now: the parts this page needs. */
  async function inspect(quiet) {
    var root = state.kobo;
    var conf = await readText(root, P.conf);
    state.scan = {
      conf: conf,
      endpoint: conf === null ? null : C.findValue(conf),
      confReady: conf !== null && C.hasSection(conf),
      junk: null
    };
    renderPlan(quiet);
    scanJunk();
    renderUndo();
  }

  /* macOS's ._ companion files, only the ones that really are AppleDouble. */
  async function scanJunk() {
    var root = state.kobo, found = [];
    var names = await listFiles(root, '', function (rel, kind) {
      return kind === 'directory' && rel === '.adds/grimmory-setup';
    });
    for (var i = 0; i < names.length; i++) {
      var base = names[i].split('/').pop();
      if (base.indexOf('._') !== 0) continue;
      var x = await dirOf(root, names[i], false);
      var f = await (await x.dir.getFileHandle(x.name)).getFile();
      var head = new Uint8Array(await f.slice(0, 4).arrayBuffer());
      if (C.isAppleDouble(head)) found.push(names[i]);
    }
    if (root !== state.kobo) return;
    state.scan.junk = found;
    renderPlan(true);
  }

  // ---- step 2: the plan -----------------------------------------------------
  function info(dl, k, v) { dl.appendChild(el('dt', null, k)); dl.appendChild(el('dd', null, v)); }

  function warn(text) { var p = el('p', 'ks-warn', text); $('warnings').appendChild(p); }

  function choice(id, label, detail, checked, disabled) {
    var row = el('label', 'ks-choice');
    var box = el('input'); box.type = 'checkbox'; box.id = id; box.checked = !!checked; box.disabled = !!disabled;
    var txt = el('span', 'ks-choice-text');
    txt.appendChild(el('strong', null, label));
    if (detail) txt.appendChild(el('span', 'ks-detail', detail));
    row.appendChild(box); row.appendChild(txt);
    return row;
  }

  function renderPlan(quiet) {
    var s = state.scan, kb = state.info;
    var keep = {};
    Array.prototype.forEach.call(document.querySelectorAll('#choices input'), function (i) {
      keep[i.id] = i.type === 'checkbox' ? i.checked : i.value;
    });

    var dl = $('kobo-info'); dl.textContent = '';
    info(dl, 'Kobo', state.kobo.name + ' (serial ' + kb.serial + ')');
    info(dl, 'Firmware', kb.firmware || 'unknown');
    info(dl, 'Syncs with', C.describeEndpoint(s.endpoint, ORIGIN));

    $('warnings').textContent = '';
    if (!s.confReady) warn('This Kobo has not finished its first start-up (its settings have no store section yet). ' +
      'Finish setting it up on the Kobo, then choose it here again.');
    if (!state.canSync) warn('Your account is not allowed to sync a Kobo yet. Ask the library\'s owner to tick "Sync Kobo" on your account, then reload this page.');

    var fs = $('choices'); fs.textContent = '';
    fs.appendChild(el('legend', 'ks-legend', 'This will:'));
    var who = state.me ? (state.me.name || state.me.username) : 'you';
    var syncDone = s.endpoint && s.endpoint === currentEndpoint();
    fs.appendChild(choice('c-sync', 'Connect this Kobo to the library as ' + who,
      syncDone ? 'Already connected to your account. Leave this ticked to check it again.'
        : 'Changes where the Kobo syncs from: ' + C.describeEndpoint(s.endpoint, ORIGIN) + ' becomes your library account.',
      keep['c-sync'] !== undefined ? keep['c-sync'] : true, !state.canSync || !s.confReady));
    if (state.settings) {
      fs.appendChild(choice('c-autoadd', 'Send new books to my Kobo automatically',
        'New books in the library go on your Kobo shelf by themselves. Untick to pick books yourself.',
        keep['c-autoadd'] !== undefined ? keep['c-autoadd'] : !!state.settings.autoAddToShelf, !state.canSync));
    }

    if (s.junk === null) {
      fs.appendChild(el('p', 'ks-detail', 'Checking the Kobo for leftover Mac files...'));
    } else if (s.junk.length) {
      fs.appendChild(choice('c-junk', 'Delete ' + s.junk.length + ' leftover Mac file' + (s.junk.length === 1 ? '' : 's'),
        'Files starting with ._ that a Mac leaves behind. The Kobo can show them as broken books.',
        keep['c-junk'] !== undefined ? keep['c-junk'] : true));
    }

    show('step-plan', true); show('tools', true);
    if (!quiet) $('step-plan').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function currentEndpoint() {
    try { return state.settings ? C.endpointFor(ORIGIN, state.settings.token) : null; } catch (e) { return null; }
  }

  function checked(id) { var n = $(id); return !!(n && n.checked && !n.disabled); }

  // ---- step 3: do it --------------------------------------------------------
  function log(text, cls) {
    var li = el('li', cls || 'ks-ok', text);
    $('log').appendChild(li);
    return li;
  }

  async function apply(ev) {
    ev.preventDefault();
    if (state.busy) return;
    var s = state.scan, root = state.kobo;
    var want = {
      sync: checked('c-sync'), autoadd: checked('c-autoadd'), junk: checked('c-junk')
    };
    state.busy = true; $('apply').disabled = true;
    $('log').textContent = ''; show('done', false); show('step-run', true);
    $('run-title').textContent = 'Setting up';
    $('step-run').scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
      var writes = [];      // {path, data} in the order they are written
      var removes = [];     // paths to delete
      var changes = [];

      // Grimmory side first: it hands out the sync key.
      var endpoint = null;
      var st = state.settings;
      if (st) {
        if ((want.sync && !st.syncEnabled) || want.autoadd !== !!st.autoAddToShelf) {
          var body = Object.assign({}, st, { syncEnabled: want.sync ? true : st.syncEnabled, autoAddToShelf: want.autoadd });
          var r = await api('PUT', '/api/v1/kobo-settings', body);
          if (r.status !== 200) throw new Error('The library would not save your Kobo settings (' + r.status + ').');
          state.settings = r.body || body;
          log(want.sync && !st.syncEnabled ? 'Turned on Kobo sync for your account.' : 'Saved your Kobo settings in the library.');
        }
        if (want.sync) endpoint = C.endpointFor(ORIGIN, state.settings.token);
      }

      // Work out every file this run writes or deletes, before touching any.
      var conf = await readText(root, P.conf);
      var newConf = null;
      if (endpoint) {
        if (conf === null) throw new Error('The Kobo\'s settings file is missing. Let the Kobo finish starting up, then try again.');
        var res = C.setValue(conf, endpoint);
        if (res.what !== 'unchanged') { newConf = res.text; changes.push('sync'); }
      }
      if (want.junk && s.junk) { removes = removes.concat(s.junk); changes.push('mac-files'); }
      if (newConf !== null) writes.push({ path: P.conf, data: newConf });   // the settings file last

      if (!writes.length && !removes.length) {
        log('Nothing on the Kobo needed changing.');
      } else {
        // Backup first: a copy of everything that is about to change, kept on the Kobo.
        var stampDir = P.backups + '/' + C.stamp();
        var entries = [];
        var touched = writes.map(function (w) { return w.path; }).concat(removes);
        for (var i = 0; i < touched.length; i++) {
          var old = await readFile(root, touched[i]);
          entries.push({ path: touched[i], existed: old !== null });
          if (old !== null) await writeFile(root, stampDir + '/files/' + touched[i], old);
        }
        var man = C.manifest({ tool: 'web', user: state.me && state.me.username, serial: state.info.serial, changes: changes }, entries);
        await writeFile(root, stampDir + '/manifest.json', JSON.stringify(man, null, 2) + '\n');
        log('Backed up ' + entries.filter(function (e) { return e.existed; }).length + ' file(s) on the Kobo before changing them.');

        for (var w = 0; w < writes.length; w++) {
          await writeFile(root, writes[w].path, writes[w].data);
          var back = await readFile(root, writes[w].path);
          var want8 = typeof writes[w].data === 'string' ? new TextEncoder().encode(writes[w].data) : writes[w].data;
          if (!same(back, want8)) throw new Error('After writing ' + writes[w].path + ', the Kobo reads back something different.');
          log(describeWrite(writes[w].path, endpoint));
        }
        for (var d = 0; d < removes.length; d++) await removeEntry(root, removes[d]);
        if (want.junk && s.junk && s.junk.length) log('Deleted ' + s.junk.length + ' leftover Mac file(s).');
      }

      if (endpoint) {
        var check = C.findValue(await readText(root, P.conf));
        if (check !== endpoint) throw new Error('The Kobo\'s settings do not show the new sync address after writing.');
        if (newConf === null) log('The Kobo was already connected to your account.');
      }
      $('run-title').textContent = 'Done';
      show('done-sync', !!endpoint);
      show('done', true);
    } catch (e) {
      log((e && e.message) || String(e), 'ks-fail');
      log('Stopped. Anything already changed can be put back with Undo below.', 'ks-fail');
      $('run-title').textContent = 'Something went wrong';
    } finally {
      state.busy = false; $('apply').disabled = false;
      if (state.kobo) { try { await inspect(true); } catch (e) { /* the Kobo was unplugged */ } }
    }
  }

  function describeWrite(path, endpoint) {
    switch (path) {
      case P.conf: return 'Connected the Kobo to your account (' + C.maskEndpoint(endpoint) + ').';
      default: return 'Wrote ' + path + '.';
    }
  }

  // ---- backups: undo and save to computer -----------------------------------
  async function backupsOnKobo() {
    var root = state.kobo, out = [];
    var d;
    try { d = (await dirOf(root, P.backups + '/x', false)).dir; } catch (e) { if (missing(e)) return out; throw e; }
    for await (var entry of d.values()) {
      if (entry.kind !== 'directory') continue;
      var txt = await readText(root, P.backups + '/' + entry.name + '/manifest.json');
      if (!txt) continue;
      try { out.push({ dir: entry.name, m: JSON.parse(txt) }); } catch (e) { /* half-written, skip */ }
    }
    out.sort(function (a, b) { return a.dir < b.dir ? 1 : -1; });
    return out;
  }

  var CHANGE_WORDS = { sync: 'connected to the library', 'mac-files': 'deleted Mac files' };

  async function renderUndo() {
    var box = $('undo-list'); box.textContent = '';
    var list = await backupsOnKobo();
    if (!list.length) { box.appendChild(el('p', 'ks-detail', 'No changes made by this page on this Kobo yet.')); return; }
    box.appendChild(el('h3', null, 'Changes you can undo (newest first)'));
    var ul = el('ul');
    list.slice(0, 10).forEach(function (b) {
      var li = el('li');
      var when = new Date(b.m.created);
      var what = (b.m.changes || []).map(function (c) { return CHANGE_WORDS[c] || c; }).join(', ') || 'changes';
      var how = { web: 'this page', mac: 'the Mac file', windows: 'the Windows file' }[b.m.tool] || b.m.tool;
      li.appendChild(el('span', null, when.toLocaleString() + ': ' + what + ' (' + how + (b.m.user ? ', ' + b.m.user : '') + ')'));
      var btn = el('button', 'ks-btn ks-small', 'Undo'); btn.type = 'button';
      btn.addEventListener('click', function () { undo(b); });
      li.appendChild(btn); ul.appendChild(li);
    });
    box.appendChild(ul);
  }

  async function undo(b) {
    if (state.busy) return;
    if (!confirm('Put this Kobo back the way it was before that change?')) return;
    var root = state.kobo, base = P.backups + '/' + b.dir;
    state.busy = true;
    $('log').textContent = ''; show('done', false); show('step-run', true);
    $('run-title').textContent = 'Undoing';
    try {
      var steps = C.undoSteps(b.m);
      for (var i = 0; i < steps.length; i++) {
        if (steps[i].action === 'restore') {
          var data = await readFile(root, base + '/files/' + steps[i].path);
          if (data === null) throw new Error('The backup copy of ' + steps[i].path + ' is missing.');
          await writeFile(root, steps[i].path, data);
        } else {
          await removeEntry(root, steps[i].path);
        }
      }
      await removeEntry(root, base, true);
      log('Put back ' + steps.length + ' file(s).');
      $('run-title').textContent = 'Undone';
    } catch (e) {
      log((e && e.message) || String(e), 'ks-fail');
      $('run-title').textContent = 'Undo did not finish';
    } finally {
      state.busy = false;
      try { await inspect(true); } catch (e) { /* unplugged */ }
    }
  }

  async function downloadBackup() {
    var btn = $('download-backup'), root = state.kobo;
    btn.disabled = true; var label = btn.textContent; btn.textContent = 'Reading the Kobo...';
    try {
      var paths = [P.version, P.conf, P.db].concat(await listFiles(root, '.adds', function (rel, kind) {
        return kind === 'directory' && rel === '.adds/grimmory-setup';
      }));
      var files = [];
      for (var i = 0; i < paths.length; i++) {
        var data = await readFile(root, paths[i]);
        if (data !== null) files.push({ name: paths[i], data: data });
      }
      files.push({ name: 'README.txt', data: new TextEncoder().encode(
        'Backup of Kobo ' + state.info.serial + ' made ' + new Date().toString() + '.\r\n\r\n' +
        'To restore: plug the Kobo in and copy the .kobo and .adds folders from here onto the Kobo drive,\r\n' +
        'replacing what is there. KoboReader.sqlite holds reading progress, shelves and highlights: putting\r\n' +
        'it back returns them to how they were on this date.\r\n') });
      var blob = new Blob([C.makeZip(files)], { type: 'application/zip' });
      var a = el('a'); a.href = URL.createObjectURL(blob);
      a.download = 'kobo-backup-' + state.info.serial + '-' + C.stamp() + '.zip';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 60000);
    } catch (e) {
      alert('Could not make the backup: ' + ((e && e.message) || e));
    } finally {
      btn.disabled = false; btn.textContent = label;
    }
  }

  // ---- start ----------------------------------------------------------------
  async function start() {
    $('pick').addEventListener('click', pick);
    $('plan-form').addEventListener('submit', apply);
    $('download-backup').addEventListener('click', downloadBackup);
    var fsa = typeof window.showDirectoryPicker === 'function';
    if (!token()) { show('signin', true); return; }
    var me = await api('GET', '/api/v1/users/me');
    if (me.status === 401 || me.status === 403) { show('signin', true); return; }
    state.me = me.body;
    if (state.me) { $('who').textContent = 'Signed in as ' + (state.me.name || state.me.username) + '.'; show('who', true); }
    var ks = await api('GET', '/api/v1/kobo-settings');
    if (ks.status === 200) state.settings = ks.body;
    else state.canSync = false;
    if (!fsa) { show('nobrowser', true); return; }
    show('step-pick', true);
  }

  start().catch(function (e) { pickError('The page could not start: ' + ((e && e.message) || e)); show('step-pick', true); });
})();
