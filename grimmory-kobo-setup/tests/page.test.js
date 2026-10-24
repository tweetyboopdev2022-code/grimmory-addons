'use strict';
/* Drives the setup page in Chromium. The Kobo is a folder in the browser's private file system
   (OPFS), which has the same handle API as a real folder the person picks, so the page's own code
   runs unchanged; only the folder picker is replaced. Needs Playwright (skipped without it). */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const fake = require('./fake-grimmory.js');
const kobo = require('./fake-kobo.js');

let chromium = null;
try { ({ chromium } = require('playwright')); } catch (e) {
  try { ({ chromium } = require(path.join(execFileSync('npm', ['root', '-g']).toString().trim(), 'playwright'))); } catch (e2) { /* none */ }
}
const PUBLIC = path.join(__dirname, '..', 'public');

/* Copy a fake Kobo from disk into OPFS/KOBOeReader. */
async function loadKobo(page, dir) {
  const files = [];
  (function walk(d, rel) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const r = rel ? rel + '/' + e.name : e.name;
      if (e.isDirectory()) walk(path.join(d, e.name), r);
      else files.push([r, fs.readFileSync(path.join(d, e.name)).toString('base64')]);
    }
  })(dir, '');
  await page.evaluate(async (files) => {
    const opfs = await navigator.storage.getDirectory();
    try { await opfs.removeEntry('KOBOeReader', { recursive: true }); } catch (e) { /* first time */ }
    const root = await opfs.getDirectoryHandle('KOBOeReader', { create: true });
    for (const [rel, b64] of files) {
      const parts = rel.split('/'); let d = root;
      for (const p of parts.slice(0, -1)) d = await d.getDirectoryHandle(p, { create: true });
      const w = await (await d.getFileHandle(parts[parts.length - 1], { create: true })).createWritable();
      await w.write(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))); await w.close();
    }
  }, files);
}

/* Every file on the OPFS Kobo, as {path: base64}. */
async function readKobo(page) {
  return page.evaluate(async () => {
    const out = {};
    const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('KOBOeReader');
    async function walk(d, rel) {
      for await (const e of d.values()) {
        const r = rel ? rel + '/' + e.name : e.name;
        if (e.kind === 'directory') await walk(e, r);
        else {
          const b = new Uint8Array(await (await e.getFile()).arrayBuffer());
          let s = ''; for (const x of b) s += String.fromCharCode(x);
          out[r] = btoa(s);
        }
      }
    }
    await walk(root, '');
    return out;
  });
}

const buf = (b64) => Buffer.from(b64, 'base64');

test('setup page: sets up sync and cleanup, then undoes it',
  { skip: !chromium && 'no playwright', timeout: 120000 }, async () => {
    const g = await fake.start({ name: 'Sam' });
    const browser = await chromium.launch();
    try {
      const page = await (await browser.newContext({ acceptDownloads: true })).newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e)));
      page.on('dialog', (d) => d.accept());
      await page.addInitScript(() => {
        window.showDirectoryPicker = async () => {
          const d = await (await navigator.storage.getDirectory()).getDirectoryHandle('KOBOeReader');
          d.requestPermission = async () => 'granted';
          return d;
        };
      });
      await page.goto(g.url + '/kobo-setup/kobo-core.js');
      await page.evaluate((t) => localStorage.setItem('accessToken_Internal', t), g.state.access);
      const k = kobo.make({ files: { '.adds/other-addon/settings.ini': 'x=1\n' } });
      await loadKobo(page, k.root);

      await page.goto(g.url + '/kobo-setup/');
      await page.waitForSelector('#step-pick:not([hidden])');
      assert.match(await page.textContent('#who'), /Signed in as Sam/);
      await page.click('#pick');
      await page.waitForSelector('#step-plan:not([hidden])');
      assert.match(await page.textContent('#kobo-info'), /N418100000001/);
      assert.match(await page.textContent('#kobo-info'), /Kobo's own store/);
      await page.waitForSelector('#c-junk');
      assert.match(await page.textContent('#choices'), /Delete 2 leftover Mac files/);

      await page.check('#c-autoadd');
      await page.click('#apply');
      await page.waitForSelector('#done:not([hidden])', { timeout: 30000 });
      assert.equal(await page.textContent('#run-title'), 'Done');

      const after = await readKobo(page);
      const endpoint = g.url + '/api/kobo/' + g.state.settings.token;
      assert.equal(buf(after['.kobo/Kobo/Kobo eReader.conf']).toString(), kobo.CONF.replace('https://storeapi.kobo.com', endpoint));
      assert.equal(after['Books/._Dune.kepub.epub'], undefined);
      assert.equal(after['._.Trashes'], undefined);
      assert.ok(after['Books/._notes.txt']);
      assert.equal(g.state.settings.syncEnabled, true);
      assert.equal(g.state.settings.autoAddToShelf, true);
      const manifests = Object.keys(after).filter((p) => /^\.adds\/grimmory-setup\/backups\/[^/]+\/manifest\.json$/.test(p));
      assert.equal(manifests.length, 1);
      const m = JSON.parse(buf(after[manifests[0]]).toString());
      assert.equal(m.tool, 'web');
      assert.deepStrictEqual(m.changes, ['sync', 'mac-files']);

      // Saving a backup to the computer
      const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#download-backup')]);
      const zipDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kobo-zip-'));
      await dl.saveAs(path.join(zipDir, 'b.zip'));
      execFileSync('unzip', ['-q', 'b.zip'], { cwd: zipDir });
      assert.equal(fs.readFileSync(path.join(zipDir, '.kobo/KoboReader.sqlite'), 'utf8'), 'SQLite format 3\0fake database');
      assert.ok(fs.existsSync(path.join(zipDir, '.adds/other-addon/settings.ini')));
      assert.ok(!fs.existsSync(path.join(zipDir, '.adds/grimmory-setup')), 'its own backups are left out');

      // Undo
      await page.waitForSelector('#undo-list button');
      await page.click('#undo-list button');
      await page.waitForFunction(() => document.getElementById('run-title').textContent === 'Undone', null, { timeout: 30000 });
      const undone = await readKobo(page);
      assert.equal(buf(undone['.kobo/Kobo/Kobo eReader.conf']).toString(), kobo.CONF);
      assert.ok(undone['Books/._Dune.kepub.epub'], 'the Mac files came back too');
      assert.ok(!Object.keys(undone).some((p) => p.startsWith('.adds/grimmory-setup/backups/')), 'the used backup is cleared');
      assert.deepStrictEqual(errors, []);
    } finally {
      await browser.close();
      await g.close();
    }
  });

test('setup page: a Kobo already connected to this account needs nothing',
  { skip: !chromium && 'no playwright', timeout: 120000 }, async () => {
    const g = await fake.start({ syncEnabled: true });
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
      await page.addInitScript(() => {
        window.showDirectoryPicker = async () => {
          const d = await (await navigator.storage.getDirectory()).getDirectoryHandle('KOBOeReader');
          d.requestPermission = async () => 'granted';
          return d;
        };
      });
      await page.goto(g.url + '/kobo-setup/kobo-core.js');
      await page.evaluate((t) => localStorage.setItem('accessToken_Internal', t), g.state.access);
      const endpoint = g.url + '/api/kobo/' + g.state.settings.token;
      const k = kobo.make({ junk: false, conf: kobo.CONF.replace('https://storeapi.kobo.com', endpoint) });
      await loadKobo(page, k.root);
      await page.goto(g.url + '/kobo-setup/');
      await page.click('#pick');
      await page.waitForSelector('#step-plan:not([hidden])');
      assert.match(await page.textContent('#kobo-info'), /this Grimmory/);
      assert.match(await page.textContent('#choices'), /Already connected/);
      await page.click('#apply');
      await page.waitForSelector('#done:not([hidden])', { timeout: 30000 });
      assert.match(await page.textContent('#log'), /Nothing on the Kobo needed changing/);
      const after = await readKobo(page);
      assert.ok(!Object.keys(after).some((p) => p.startsWith('.adds/grimmory-setup/')), 'no backup when nothing changes');
      assert.ok(!g.state.calls.includes('PUT /api/v1/kobo-settings'));
      assert.deepStrictEqual(errors, []);
    } finally {
      await browser.close();
      await g.close();
    }
  });

test('setup page: asks people without a sign-in to sign in', { skip: !chromium && 'no playwright' }, async () => {
  const g = await fake.start();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(g.url + '/kobo-setup/');
    await page.waitForSelector('#signin:not([hidden])');
    assert.equal(await page.isHidden('#step-pick'), true);
  } finally { await browser.close(); await g.close(); }
});

test('link.js: the "Set up my Kobo" box appears only inside Grimmory\'s Kobo sync settings', { skip: !chromium && 'no playwright' }, async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.route('http://grimmory.test/**', (r) => r.fulfill({ contentType: 'text/html', body: '<div id="app"><nav>Notebook</nav></div>' }));
    await page.goto('http://grimmory.test/settings?tab=device');
    await page.addScriptTag({ path: path.join(PUBLIC, 'link.js') });
    await page.waitForTimeout(1500);
    assert.equal(await page.$('#ks-setup-box'), null, 'no Kobo settings on the page, no box');
    // Angular renders the Kobo section later, only for accounts allowed to sync a Kobo
    await page.evaluate(() => {
      document.getElementById('app').insertAdjacentHTML('beforeend',
        '<app-kobo-sync-setting-component><div class="main-container"><div class="settings-header"><h2>Kobo</h2></div>' +
        '<div class="settings-content">form</div></div></app-kobo-sync-setting-component>');
    });
    await page.waitForSelector('#ks-setup-box');
    assert.equal(await page.getAttribute('#ks-setup-box a', 'href'), '/kobo-setup/');
    assert.equal(await page.evaluate(() => document.querySelector('.settings-header').nextElementSibling.id), 'ks-setup-box');
    await page.waitForTimeout(2500);
    assert.equal(await page.$$eval('#ks-setup-box', (n) => n.length), 1, 'added once');
  } finally { await browser.close(); }
});
