/* A Kobo on disk, as far as the setup tools can tell: version file, settings, database, a book,
   and some macOS ._ leftovers (plus one ._ file that is not AppleDouble and must survive). */
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');

const CONF = [
  '[ApplicationPreferences]', 'CurrentLocale=en_US', '',
  '[OneStoreServices]', 'api_endpoint=https://storeapi.kobo.com', 'image_host=https://cdn.kobo.com/book-images/',
  'oauth_host=https://oauth.kobo.com', '',
  '[PowerOptions]', 'AutoOffMinutes=60', ''
].join('\n');

const APPLEDOUBLE = Buffer.concat([Buffer.from([0x00, 0x05, 0x16, 0x07, 0x00, 0x02, 0x00, 0x00]), Buffer.alloc(40)]);

function write(root, rel, data) {
  const p = path.join(root, ...rel.split('/'));
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, data);
}

function make(opts = {}) {
  const volumes = fs.mkdtempSync(path.join(os.tmpdir(), 'kobo-volumes-'));
  const root = path.join(volumes, 'KOBOeReader');
  write(root, '.kobo/version', opts.version || 'N418100000001,4.1.15,4.38.23038,4.1.15,4.1.15,00000000-0000-0000-0000-000000000387\n');
  write(root, '.kobo/Kobo/Kobo eReader.conf', opts.conf !== undefined ? opts.conf : CONF);
  write(root, '.kobo/KoboReader.sqlite', Buffer.from('SQLite format 3\0fake database'));
  write(root, 'Books/Dune.kepub.epub', 'not really a book');
  if (opts.junk !== false) {
    write(root, 'Books/._Dune.kepub.epub', APPLEDOUBLE);
    write(root, '._.Trashes', APPLEDOUBLE);
    write(root, 'Books/._notes.txt', 'just a file whose name starts with ._');
  }
  for (const [rel, data] of Object.entries(opts.files || {})) write(root, rel, data);
  return { volumes, root, read: (rel) => { try { return fs.readFileSync(path.join(root, ...rel.split('/'))); } catch (e) { return null; } } };
}

module.exports = { make, CONF, write };
