/* A stand-in for Grimmory: the four API calls the Kobo setup tools make, plus /kobo-setup/ served
   from public/ the way nginx serves it. */
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');

const PUBLIC = path.join(__dirname, '..', 'public');
const TYPES = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.zip': 'application/zip' };

function start(opts = {}) {
  const state = {
    user: { id: 7, username: opts.username || 'sam', name: opts.name || 'Sam', password: opts.password || 'p@ss "word"' },
    canSync: opts.canSync !== false,
    settings: { id: 1, userId: '7', token: opts.token || '0f3c2b1a-1111-2222-3333-444455556666', syncEnabled: !!opts.syncEnabled,
      progressMarkAsReadingThreshold: 1, progressMarkAsFinishedThreshold: 99, autoAddToShelf: false,
      hardcoverApiKey: opts.hardcoverApiKey || null, hardcoverSyncEnabled: false },
    access: 'access-' + Math.random().toString(36).slice(2),
    calls: []
  };
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      state.calls.push(req.method + ' ' + req.url);
      const send = (status, obj) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(obj === undefined ? '' : JSON.stringify(obj)); };
      const authed = req.headers.authorization === 'Bearer ' + state.access;
      const url = req.url.split('?')[0];
      if (req.method === 'POST' && url === '/api/v1/auth/login') {
        let j = {}; try { j = JSON.parse(body); } catch (e) { return send(400, {}); }
        if (j.username === state.user.username && j.password === state.user.password) return send(200, { accessToken: state.access, refreshToken: 'r', expires: 1 });
        return send(401, { message: 'bad' });
      }
      if (url === '/api/v1/users/me') return authed ? send(200, { id: 7, username: state.user.username, name: state.user.name }) : send(401, {});
      if (url === '/api/v1/kobo-settings') {
        if (!authed) return send(401, {});
        if (!state.canSync) return send(403, {});
        if (req.method === 'GET') return send(200, state.settings);
        if (req.method === 'PUT') {
          const j = JSON.parse(body);
          state.settings.syncEnabled = !!j.syncEnabled;
          state.settings.autoAddToShelf = !!j.autoAddToShelf;
          return send(200, state.settings);
        }
      }
      if (url.startsWith('/kobo-setup/')) {
        let rel = decodeURIComponent(url.slice('/kobo-setup/'.length)) || 'index.html';
        const file = path.join(PUBLIC, rel);
        if (!file.startsWith(PUBLIC) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
        res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
        return res.end(fs.readFileSync(file));
      }
      res.writeHead(404); res.end();
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => {
    resolve({ url: 'http://127.0.0.1:' + server.address().port, state, close: () => new Promise((r) => server.close(r)) });
  }));
}

module.exports = { start };
