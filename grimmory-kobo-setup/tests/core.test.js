'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const C = require('../public/kobo-core.js');

const EP = 'https://library.example/api/kobo/0f3c2b1a-1111-2222-3333-444455556666';

// Every case the three settings-file editors (page, Mac file, Windows file) must agree on.
const CASES = {
  stock: '[A]\nx=1\n\n[OneStoreServices]\napi_endpoint=https://storeapi.kobo.com\nimage_host=h\n\n[B]\ny=2\n',
  crlf: '[A]\r\nx=1\r\n[OneStoreServices]\r\napi_endpoint=https://storeapi.kobo.com\r\nimage_host=h\r\n',
  missingKey: '[OneStoreServices]\nimage_host=h\n\n\n[B]\ny=2\n',
  missingKeyCrlf: '[OneStoreServices]\r\nimage_host=h\r\n\r\n[B]\r\ny=2\r\n',
  lastNoNewline: '[A]\nx=1\n[OneStoreServices]\nimage_host=h',
  spaced: '[OneStoreServices]\n  api_endpoint = ' + EP + '  \n',
  unchanged: '[OneStoreServices]\napi_endpoint=' + EP + '\n',
  otherServer: '[OneStoreServices]\napi_endpoint=https://old.example/api/kobo/abcdefgh12345678\noauth=o\n',
  equalsInValue: '[OneStoreServices]\napi_endpoint=https://x/?a=b\n',
  noSection: '[A]\nx=1\n',
  duplicate: '[OneStoreServices]\napi_endpoint=a\napi_endpoint=b\n',
  keyInOtherSection: '[Other]\napi_endpoint=keep\n[OneStoreServices]\nimage_host=h\n'
};

function js(text) {
  try { const r = C.setValue(text, EP); return { what: r.what, text: r.text }; } catch (e) { return { what: /times/.test(e.message) ? 'duplicate' : 'nosection' }; }
}

test('settings file: replaced, inserted, unchanged', () => {
  let r = C.setValue(CASES.stock, EP);
  assert.equal(r.what, 'replaced');
  assert.equal(r.text, CASES.stock.replace('https://storeapi.kobo.com', EP));
  r = C.setValue(CASES.crlf, EP);
  assert.equal(r.text, CASES.crlf.replace('https://storeapi.kobo.com', EP));
  r = C.setValue(CASES.missingKey, EP);
  assert.equal(r.what, 'inserted');
  assert.equal(r.text, '[OneStoreServices]\nimage_host=h\napi_endpoint=' + EP + '\n\n\n[B]\ny=2\n');
  r = C.setValue(CASES.missingKeyCrlf, EP);
  assert.equal(r.text, '[OneStoreServices]\r\nimage_host=h\r\napi_endpoint=' + EP + '\r\n\r\n[B]\r\ny=2\r\n');
  assert.equal(C.setValue(CASES.lastNoNewline, EP).text, CASES.lastNoNewline + '\napi_endpoint=' + EP);
  assert.equal(C.setValue(CASES.spaced, EP).what, 'unchanged');
  assert.equal(C.setValue(CASES.keyInOtherSection, EP).text, '[Other]\napi_endpoint=keep\n[OneStoreServices]\nimage_host=h\napi_endpoint=' + EP + '\n');
  assert.throws(() => C.setValue(CASES.noSection, EP), /first start-up/);
  assert.throws(() => C.setValue(CASES.duplicate, EP), /2 times/);
  assert.equal(C.findValue(CASES.equalsInValue), 'https://x/?a=b');
  assert.equal(C.findValue(CASES.keyInOtherSection), null);
});

test('Mac file edits the settings exactly like the page', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'mac', 'Connect my Kobo.command'), 'utf8');
  const fn = script.match(/^set_conf\(\) \{[\s\S]*?^\}$/m)[0];
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conf-'));
  for (const [name, text] of Object.entries(CASES)) {
    fs.writeFileSync(path.join(dir, 'in'), text);
    try { fs.unlinkSync(path.join(dir, 'out')); } catch (e) {}
    let code = 0, what = '';
    try { what = execFileSync('bash', ['-c', fn + '\nset_conf "$1" "$2" "$3"', 'x', path.join(dir, 'in'), path.join(dir, 'out'), EP]).toString().trim(); }
    catch (e) { code = e.status; }
    const got = code === 0 ? { what, text: fs.readFileSync(path.join(dir, 'out'), 'utf8') }
      : { what: { 3: 'unchanged', 4: 'nosection', 5: 'duplicate' }[code] || 'exit ' + code };
    const want = js(text);
    if (want.what === 'unchanged') delete want.text;
    assert.deepStrictEqual(got, want, name);
  }
});

const PWSH = process.env.PWSH || 'pwsh';
function havePwsh() { try { execFileSync(PWSH, ['-NoProfile', '-c', '1']); return true; } catch (e) { return false; } }

test('Windows file edits the settings exactly like the page', { skip: !havePwsh() && 'no pwsh' }, () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'windows', 'Connect my Kobo.cmd'), 'utf8');
  const fn = script.match(/^function Set-ConfValue[\s\S]*?^\}$/m)[0];
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conf-'));
  fs.writeFileSync(path.join(dir, 'f.ps1'), fn + `
$out = @{}
foreach ($f in Get-ChildItem -LiteralPath $args[0] -Filter '*.case') {
  $r = Set-ConfValue ([IO.File]::ReadAllText($f.FullName)) $args[1]
  $out[$f.BaseName] = @{ what = $r.what; text = $(if ($r.what -eq 'unchanged') { $null } else { $r.text }) }
}
$out | ConvertTo-Json -Depth 3 -Compress
`);
  for (const [name, text] of Object.entries(CASES)) fs.writeFileSync(path.join(dir, name + '.case'), text);
  const got = JSON.parse(execFileSync(PWSH, ['-NoProfile', '-File', path.join(dir, 'f.ps1'), dir, EP]).toString());
  for (const [name, text] of Object.entries(CASES)) {
    const want = js(text);
    const g = { what: got[name].what };
    if (got[name].text !== null && got[name].text !== undefined) g.text = got[name].text;
    if (want.what === 'unchanged') delete want.text;
    assert.deepStrictEqual(g, want, name);
  }
});

test('version file, endpoints, AppleDouble', () => {
  assert.deepStrictEqual(C.parseVersion('N4181,4.1.15,4.38.23038,4.1.15,4.1.15,00000000-0000-0000-0000-000000000387\n'),
    { serial: 'N4181', firmware: '4.38.23038', model: '00000000-0000-0000-0000-000000000387' });
  assert.equal(C.firmwareMajor('5.1.184318'), 5);
  assert.throws(() => C.parseVersion(''), /version file is empty/);
  assert.equal(C.endpointFor('https://lib.example/', 'abcdefgh-123'), 'https://lib.example/api/kobo/abcdefgh-123');
  assert.throws(() => C.endpointFor('https://lib.example', 'bad token'));
  assert.equal(C.maskEndpoint(EP), 'https://library.example/api/kobo/0f3c...6666');
  assert.equal(C.describeEndpoint('https://storeapi.kobo.com', 'x'), 'Kobo\'s own store (not connected to Grimmory)');
  assert.match(C.describeEndpoint(EP, 'https://library.example'), /^this Grimmory/);
  assert.match(C.describeEndpoint(EP, 'https://other.example'), /^another server/);
  assert.ok(C.isAppleDouble(new Uint8Array([0, 5, 0x16, 7, 1])));
  assert.ok(!C.isAppleDouble(new Uint8Array([0x2e, 0x5f, 1, 2])));
});

test('backup manifest and undo steps', () => {
  const m = C.manifest({ tool: 'web', user: 'sam', serial: 'N1', changes: ['sync'], date: new Date(0) },
    [{ path: '.kobo/Kobo/Kobo eReader.conf', existed: true }, { path: '.adds/other-addon/settings.ini', existed: false }]);
  assert.equal(m.created, '1970-01-01T00:00:00.000Z');
  assert.deepStrictEqual(C.undoSteps(m), [
    { path: '.kobo/Kobo/Kobo eReader.conf', action: 'restore' }, { path: '.adds/other-addon/settings.ini', action: 'remove' }]);
  assert.throws(() => C.undoSteps({ version: 1, entries: [{ path: '../etc/passwd', existed: true }] }), /outside/);
  assert.throws(() => C.undoSteps({ version: 1, entries: [{ path: '/abs', existed: true }] }), /outside/);
  assert.throws(() => C.undoSteps({ version: 2, entries: [] }), /understands/);
});

test('zip writer makes a zip unzip can read', { skip: (() => { try { execFileSync('unzip', ['-v']); return false; } catch (e) { return 'no unzip'; } })() }, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zip-'));
  const big = new Uint8Array(70000).map((_, i) => i % 251);
  fs.writeFileSync(path.join(dir, 'b.zip'), C.makeZip([
    { name: '.kobo/Kobo/Kobo eReader.conf', data: new TextEncoder().encode('[A]\n') },
    { name: '.adds/big.bin', data: big }]));
  execFileSync('unzip', ['-q', 'b.zip'], { cwd: dir });
  assert.equal(fs.readFileSync(path.join(dir, '.kobo/Kobo/Kobo eReader.conf'), 'utf8'), '[A]\n');
  assert.deepStrictEqual(new Uint8Array(fs.readFileSync(path.join(dir, '.adds/big.bin'))), big);
  assert.equal(C.crc32(new TextEncoder().encode('123456789')), 0xCBF43926);
});
