'use strict';
/* Runs the Mac and Windows double-click files end to end against a fake Grimmory and a fake Kobo. */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

/* Async on purpose: the fake Grimmory runs in this same process and must keep answering. */
function runAsync(cmd, args, opts) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { env: opts.env });
    let stdout = '', stderr = '';
    p.stdout.on('data', (d) => { stdout += d; });
    p.stderr.on('data', (d) => { stderr += d; });
    const t = setTimeout(() => p.kill('SIGKILL'), opts.timeout || 60000);
    p.on('close', (status) => { clearTimeout(t); resolve({ status, stdout, stderr }); });
    p.stdin.end(opts.input);
  });
}
const fake = require('./fake-grimmory.js');
const kobo = require('./fake-kobo.js');
const C = require('../public/kobo-core.js');

const ROOT = path.join(__dirname, '..');
const PWSH = process.env.PWSH || 'pwsh';
function havePwsh() { try { execFileSync(PWSH, ['-NoProfile', '-c', '1']); return true; } catch (e) { return false; } }

const RUNNERS = {
  mac: (env, input) => runAsync('bash', [path.join(ROOT, 'mac', 'Connect my Kobo.command')], {
    input, env: Object.assign({}, process.env, env, { PATH: path.join(__dirname, 'stubs') + ':' + process.env.PATH, TERM: 'dumb' }),
    encoding: 'utf8', timeout: 60000
  }),
  windows: (env, input) => runAsync(PWSH, ['-NoProfile', '-Command',
    "$s=[IO.File]::ReadAllText('" + path.join(ROOT, 'windows', 'Connect my Kobo.cmd') + "'); iex $s"], {
    input, env: Object.assign({}, process.env, env), encoding: 'utf8', timeout: 60000
  })
};

for (const [os, run] of Object.entries(RUNNERS)) {
  test(os + ' file: sets up a fresh Kobo, then finds nothing to do', { skip: os === 'windows' && !havePwsh() && 'no pwsh' }, async () => {
    const g = await fake.start();
    const k = kobo.make();
    const confBefore = k.read('.kobo/Kobo/Kobo eReader.conf');
    try {
      const env = { GRIMMORY_URL: g.url, KOBO_VOLUMES: k.volumes };
      // wrong password once, then right; Enter to go ahead; n = don't eject; Enter to close
      const r = await run(env, ['sam', 'wrong', 'sam', g.state.user.password, '', 'n', '', ''].join('\n') + '\n');
      const out = r.stdout + r.stderr;
      assert.match(out, /did not work/, out);
      assert.match(out, /Turned on Kobo sync/, out);
      assert.match(out, /Done\./, out);
      assert.equal(g.state.settings.syncEnabled, true);
      const conf = k.read('.kobo/Kobo/Kobo eReader.conf').toString();
      assert.equal(conf, kobo.CONF.replace('https://storeapi.kobo.com', g.url + '/api/kobo/' + g.state.settings.token));
      assert.equal(k.read('Books/._Dune.kepub.epub'), null);
      assert.equal(k.read('._.Trashes'), null);
      assert.ok(k.read('Books/._notes.txt'), 'a ._ file that is not AppleDouble stays');
      assert.ok(k.read('Books/Dune.kepub.epub'));

      const backups = fs.readdirSync(path.join(k.root, '.adds/grimmory-setup/backups'));
      assert.equal(backups.length, 1);
      const bdir = path.join(k.root, '.adds/grimmory-setup/backups', backups[0]);
      const m = JSON.parse(fs.readFileSync(path.join(bdir, 'manifest.json'), 'utf8'));
      assert.equal(m.tool, os); assert.equal(m.user, 'sam'); assert.equal(m.serial, 'N418100000001');
      assert.deepStrictEqual(m.changes, ['sync', 'mac-files']);
      const steps = C.undoSteps(m);
      const byPath = Object.fromEntries(steps.map((s) => [s.path, s.action]));
      assert.equal(byPath['.kobo/Kobo/Kobo eReader.conf'], 'restore');
      assert.equal(byPath['Books/._Dune.kepub.epub'], 'restore');
      assert.ok(fs.readFileSync(path.join(bdir, 'files/.kobo/Kobo/Kobo eReader.conf')).equals(confBefore));

      const again = await run(env, ['sam', g.state.user.password, '', ''].join('\n') + '\n');
      assert.match(again.stdout + again.stderr, /already set up/, again.stdout + again.stderr);
      assert.equal(fs.readdirSync(path.join(k.root, '.adds/grimmory-setup/backups')).length, 1);
    } finally { await g.close(); }
  });

  test(os + ' file: stops clearly when the account may not sync a Kobo', { skip: os === 'windows' && !havePwsh() && 'no pwsh' }, async () => {
    const g = await fake.start({ canSync: false });
    const k = kobo.make();
    try {
      const r = await run({ GRIMMORY_URL: g.url, KOBO_VOLUMES: k.volumes }, ['sam', g.state.user.password, '', ''].join('\n') + '\n');
      assert.match(r.stdout + r.stderr, /not allowed to sync a Kobo/);
      assert.ok(k.read('.kobo/Kobo/Kobo eReader.conf').equals(Buffer.from(kobo.CONF)));
    } finally { await g.close(); }
  });

  test(os + ' file: keeps a CRLF settings file and other add-ons as they are', { skip: os === 'windows' && !havePwsh() && 'no pwsh' }, async () => {
    const g = await fake.start({ syncEnabled: true });
    const conf = kobo.CONF.replace(/\n/g, '\r\n');
    const k = kobo.make({ conf, junk: false, files: { '.adds/other-addon/settings.ini': 'x=1\n' } });
    try {
      const r = await run({ GRIMMORY_URL: g.url, KOBO_VOLUMES: k.volumes }, ['sam', g.state.user.password, '', 'n', '', ''].join('\n') + '\n');
      assert.match(r.stdout + r.stderr, /Done\./, r.stdout + r.stderr);
      assert.equal(k.read('.kobo/Kobo/Kobo eReader.conf').toString(), conf.replace('https://storeapi.kobo.com', g.url + '/api/kobo/' + g.state.settings.token));
      assert.equal(k.read('.adds/other-addon/settings.ini').toString(), 'x=1\n', 'other add-ons are left alone');
      assert.ok(!g.state.calls.includes('PUT /api/v1/kobo-settings'), 'sync was already on');
    } finally { await g.close(); }
  });
}
