/* Grimmory Wishlist: sidebar entry + page + login ping. Plain JS, no dependencies. */
(function () {
  'use strict';
  if (window.__grimmoryWishlist) return;
  window.__grimmoryWishlist = true;

  var API = window.__WL_API || '/wishlist-api';
  var STATIC = window.__WL_STATIC || '/wishlist-static';
  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  var state = { items: [], me: null, query: '', loaded: false, error: '', confirming: null, pending: null,
    pick: null, results: [], sources: [], enabled: null, gaps: null, searchNote: '', open: false, active: -1, who: '',
    collapsed: (function () { try { return JSON.parse(localStorage.getItem('wl-collapsed') || '[]'); } catch (e) { return []; } })(),
    hiddenOpen: false,
    filtersOpen: (function () { try { return localStorage.getItem('wl-filters-open') === '1'; } catch (e) { return false; } })() };
  var searchTimer = 0, searchSeq = 0;
  var root = null, navItem = null, toastEl = null, toastTimer = 0, savedActive = [];

  function token() { try { return localStorage.getItem('accessToken_Internal') || ''; } catch (e) { return ''; } }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function icon(paths) {
    var s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none');
    s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) {
      var p = document.createElementNS(SVG_NS, 'path'); p.setAttribute('d', d); s.appendChild(p);
    });
    return s;
  }
  var ICONS = {
    gift: ['M20 12v10H4V12', 'M2 7h20v5H2z', 'M12 22V7', 'M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z', 'M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z'],
    search: ['M21 21l-4.3-4.3', 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z'],
    plus: ['M12 5v14', 'M5 12h14'],
    book: ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'],
    layers: ['M12 2 2 7l10 5 10-5-10-5z', 'M2 17l10 5 10-5', 'M2 12l10 5 10-5'],
    x: ['M18 6 6 18', 'M6 6l12 12'],
    filter: ['M22 3H2l8 9.46V19l4 2v-8.54L22 3z'],
    chevron: ['M6 9l6 6 6-6'],
    check: ['M20 6 9 17l-5-5'],
    trash: ['M3 6h18', 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6', 'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2']
  };

  // ---- network ---------------------------------------------------------
  function call(method, path, body) {
    var f = nativeFetch || window.fetch;
    var opts = { method: method, headers: { 'Authorization': 'Bearer ' + token() } };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return f(API + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; });
    });
  }

  // ---- toast -----------------------------------------------------------
  function toast(msg) {
    if (!toastEl) { toastEl = el('div', 'wl-toast'); toastEl.setAttribute('role', 'status'); document.body.appendChild(toastEl); }
    toastEl.textContent = msg; toastEl.setAttribute('data-on', '1');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.setAttribute('data-on', '0'); }, 2600);
  }

  // ---- helpers ---------------------------------------------------------
  function ago(iso) {
    var t = Date.parse(iso); if (isNaN(t)) return '';
    var s = Math.round((t - Date.now()) / 1000), a = Math.abs(s);
    var units = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
    if (a < 45) return 'just now';
    try {
      var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
      for (var i = 0; i < units.length; i++) if (a >= units[i][1]) return rtf.format(Math.round(s / units[i][1]), units[i][0]);
    } catch (e) { /* fall through */ }
    return new Date(t).toLocaleDateString();
  }
  // A release date as the sources know it: '2023-11-07', '2023-11' or '2023'.
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function releaseText(d) {
    if (!d) return '';
    var p = String(d).split('-');
    if (p.length === 3) return (+p[2]) + ' ' + MONTHS[(+p[1]) - 1] + ' ' + p[0];
    if (p.length === 2) return MONTHS[(+p[1]) - 1] + ' ' + p[0];
    return p[0];
  }
  function upcoming(d) {
    if (!d) return false;
    var now = new Date(), today = now.getFullYear() + '-' + ('0' + (now.getMonth() + 1)).slice(-2) + '-' + ('0' + now.getDate()).slice(-2);
    return String(d) > today.slice(0, String(d).length);
  }
  function initials(name) {
    var p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase();
  }

  // ---- page ------------------------------------------------------------
  function build() {
    root = el('div', 'wl'); root.id = 'wl-root'; root.hidden = true;
    root.setAttribute('role', 'region'); root.setAttribute('aria-label', 'Wishlist');
    root.addEventListener('keydown', function (e) { if (e.key === 'Escape' && state.confirming) { state.confirming = null; renderList(); } });
    render();
  }

  function render() {
    if (!root) return;
    var keepFocus = document.activeElement && root.contains(document.activeElement) ? document.activeElement.getAttribute('data-k') : null;
    var draft = {};
    ['title', 'author', 'note', 'q'].forEach(function (k) {
      var i = root.querySelector('[data-k="' + k + '"]'); if (i) draft[k] = i.value;
    });
    var pos = root.scrollTop;
    root.textContent = '';

    var head = el('div', 'wl-head');
    var left = el('div');
    var h = el('h1', 'wl-title');
    h.appendChild(document.createTextNode('Wishlist'));
    h.appendChild(el('span', 'wl-count', String(state.items.length)));
    left.appendChild(h);
    left.appendChild(el('p', 'wl-sub', 'Books and series you would like to see in the library.'));
    head.appendChild(left);
    var sw = el('div', 'wl-search');
    sw.appendChild(icon(ICONS.search));
    var q = el('input', 'wl-input'); q.type = 'search'; q.placeholder = 'Search the list'; q.setAttribute('data-k', 'q');
    q.setAttribute('aria-label', 'Search the wishlist'); q.value = draft.q != null ? draft.q : state.query;
    q.addEventListener('input', function () { state.query = q.value; renderList(); filterBadge(); });
    sw.appendChild(q);
    // Filters live behind a toggle so they never crowd the Add form.
    var toggle = el('button', 'wl-btn wl-btn-quiet wl-filter-btn'); toggle.type = 'button'; toggle.setAttribute('data-k', 'filters');
    toggle.setAttribute('aria-controls', 'wl-filters'); toggle.setAttribute('aria-expanded', state.filtersOpen ? 'true' : 'false');
    toggle.appendChild(icon(ICONS.filter)); toggle.appendChild(document.createTextNode('Filters'));
    toggle.appendChild(el('span', 'wl-filter-dot')); toggle.appendChild(icon(ICONS.chevron));
    toggle.addEventListener('click', function () {
      state.filtersOpen = !state.filtersOpen;
      try { localStorage.setItem('wl-filters-open', state.filtersOpen ? '1' : '0'); } catch (e) { /* per-browser nicety only */ }
      var p = root.querySelector('#wl-filters'); if (p) p.hidden = !state.filtersOpen;
      toggle.setAttribute('aria-expanded', state.filtersOpen ? 'true' : 'false');
      if (state.filtersOpen) { var s = root.querySelector('[data-k="q"]'); if (s) s.focus(); }
    });
    head.appendChild(toggle);
    var tools = el('div', 'wl-tools'); tools.id = 'wl-filters'; tools.hidden = !state.filtersOpen;
    var names = []; state.items.forEach(function (it) { if (it.addedBy && names.indexOf(it.addedBy) < 0) names.push(it.addedBy); });
    if (state.me && state.me.admin && names.length > 1) {
      var sel = el('select', 'wl-input wl-select'); sel.setAttribute('aria-label', 'Show requests from'); sel.setAttribute('data-k', 'who');
      var all = el('option', null, 'Everyone'); all.value = ''; sel.appendChild(all);
      names.sort().forEach(function (n) { var o = el('option', null, n); o.value = n; sel.appendChild(o); });
      if (names.indexOf(state.who) < 0) state.who = '';
      sel.value = state.who;
      sel.addEventListener('change', function () { state.who = sel.value; renderList(); filterBadge(); });
      tools.appendChild(sel);
    }
    tools.appendChild(sw); head.appendChild(tools);
    root.appendChild(head);
    filterBadge();
    // Notifications sit at the top, where they are easy to find and switch on
    if (state.me) {
      var pb = el('section', 'wl-push'); pb.setAttribute('data-role', 'push');
      root.appendChild(pb); renderPush();
    }

    var form = el('form', 'wl-form'); form.noValidate = true;
    function field(label, key, ph, opt, max) {
      var f = el('div', 'wl-field'); var l = el('label', null, label);
      var id = 'wl-f-' + key; l.setAttribute('for', id);
      if (opt) { l.appendChild(document.createTextNode(' ')); l.appendChild(el('span', null, '(optional)')); }
      var i = el('input', 'wl-input'); i.id = id; i.type = 'text'; i.placeholder = ph; i.maxLength = max;
      i.setAttribute('data-k', key); i.autocomplete = 'off'; i.value = draft[key] || '';
      i.addEventListener('input', function () {
        if (state.pending) { state.pending = null; setMsg('', ''); }
        if (key === 'title' || key === 'author') { if (state.pick) { state.pick = null; renderPick(); } queueSearch(); }
      });
      if (key === 'title' || key === 'author') {
        i.addEventListener('keydown', onSuggestKey);
        i.addEventListener('focus', function () { if (state.results.length && !state.pick) { state.open = true; renderSuggest(); } });
        if (key === 'title') {
          i.setAttribute('role', 'combobox'); i.setAttribute('aria-autocomplete', 'list');
          i.setAttribute('aria-controls', 'wl-suggest'); i.setAttribute('aria-expanded', 'false');
        }
      }
      f.appendChild(l); f.appendChild(i); return f;
    }
    form.appendChild(field('Title', 'title', 'Search for a book or a whole series', false, 200));
    form.appendChild(field('Author', 'author', 'e.g. Susanna Clarke', true, 120));
    form.appendChild(field('Note', 'note', 'Series, edition, why you want it', true, 300));
    var add = el('button', 'wl-btn wl-btn-primary'); add.type = 'submit';
    add.appendChild(icon(ICONS.plus)); add.appendChild(document.createTextNode('Add'));
    form.appendChild(add);
    form.addEventListener('submit', function (e) { e.preventDefault(); state.open = false; submit(false); });
    var pick = el('div', 'wl-pick'); pick.setAttribute('data-role', 'pick'); form.appendChild(pick);
    var sug = el('div', 'wl-suggest'); sug.id = 'wl-suggest'; sug.setAttribute('data-role', 'suggest'); sug.hidden = true;
    form.appendChild(sug);
    root.appendChild(form);
    renderPick(); renderSuggest();

    var msg = el('div', 'wl-msg'); msg.setAttribute('role', 'status'); msg.setAttribute('data-role', 'msg');
    root.appendChild(msg);

    var list = el('ul', 'wl-list'); list.setAttribute('data-role', 'list');
    root.appendChild(list);
    // Arrived books get a fixed spot between the list and the series gaps, so re-drawing either never moves it.
    var done = el('section', 'wl-done'); done.setAttribute('data-role', 'done'); done.hidden = true;
    root.appendChild(done);
    renderList();
    if (state.me && state.me.admin) {
      var gaps = el('section', 'wl-gaps'); gaps.setAttribute('data-role', 'series');
      root.appendChild(gaps); renderSeries();
    }
    if (state.error) setMsg('error', state.error);
    if (state.pending) showPending(state.pending);

    root.scrollTop = pos;
    if (keepFocus) { var f = root.querySelector('[data-k="' + keepFocus + '"]'); if (f) f.focus(); }
  }

  // ---- book / series search --------------------------------------------
  function thumb(cover, kind, small) {
    var t = el('span', 'wl-thumb' + (small ? ' wl-thumb-sm' : ''));
    function fallback() { t.textContent = ''; t.appendChild(icon(kind === 'series' ? ICONS.layers : ICONS.book)); }
    if (cover) {
      var img = document.createElement('img');
      img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
      img.addEventListener('error', fallback);
      img.src = cover; t.appendChild(img);
    } else fallback();
    return t;
  }
  function queryText() { return (val('title') + ' ' + val('author')).trim(); }
  function queueSearch() {
    clearTimeout(searchTimer);
    if (state.enabled === false) return;
    var q = queryText(), seq = ++searchSeq;
    if (q.length < 2) { state.results = []; state.searchNote = ''; state.open = false; renderSuggest(); return; }
    searchTimer = setTimeout(function () {
      call('GET', '/search?q=' + encodeURIComponent(q)).then(function (r) {
        if (seq !== searchSeq) return;                     // a newer keystroke got ahead of this answer
        if (r.status === 200) {
          state.enabled = r.body.enabled !== false; state.results = r.body.results || []; state.searchNote = '';
          state.sources = r.body.sources || [];
        } else {
          state.results = [];
          state.searchNote = r.status === 429 ? 'Searching too fast, one moment.' : r.status === 401 ? friendly(r)
            : 'Book search is unavailable. You can still type it in and add it.';
        }
        state.active = -1; state.open = !!(state.results.length || state.searchNote); renderSuggest();
      }).catch(function () {
        if (seq !== searchSeq) return;
        state.results = []; state.searchNote = 'Book search is unavailable. You can still type it in and add it.';
        state.open = true; renderSuggest();
      });
    }, 280);
  }
  function renderSuggest() {
    var box = root && root.querySelector('[data-role="suggest"]'); if (!box) return;
    var ti = root.querySelector('[data-k="title"]');
    var show = !!(state.open && !state.pick && (state.results.length || state.searchNote));
    box.hidden = !show; box.textContent = '';
    if (show && ti) box.style.top = (ti.offsetTop + ti.offsetHeight + 4) + 'px';   // just under the Title box
    if (ti) { ti.setAttribute('aria-expanded', show ? 'true' : 'false'); ti.removeAttribute('aria-activedescendant'); }
    if (!show) return;
    if (state.searchNote) box.appendChild(el('p', 'wl-suggest-note', state.searchNote));
    var list = el('ul', 'wl-suggest-list'); list.setAttribute('role', 'listbox'); var last = '';
    state.results.forEach(function (r, i) {
      if (r.kind !== last) {
        last = r.kind;
        var hd = el('li', 'wl-suggest-h', r.kind === 'series' ? 'Whole series' : 'Books'); hd.setAttribute('role', 'presentation'); list.appendChild(hd);
      }
      var li = el('li', 'wl-opt'); li.id = 'wl-opt-' + i; li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', i === state.active ? 'true' : 'false');
      li.appendChild(thumb(r.cover, r.kind, true));
      var main = el('span', 'wl-opt-main'); main.appendChild(el('span', 'wl-opt-title', r.title));
      var bits = []; if (r.author) bits.push(r.author);
      if (r.kind === 'book') {
        if (r.released && !upcoming(r.released)) bits.push(releaseText(r.released));
        else if (!r.released && r.year) bits.push(String(r.year));
        if (r.series) bits.push(r.series);
      }
      if (bits.length) main.appendChild(el('span', 'wl-opt-sub', bits.join(' · ')));
      li.appendChild(main);
      if (r.kind === 'book' && upcoming(r.released))
        li.appendChild(el('span', 'wl-badge wl-badge-soon', 'Out ' + releaseText(r.released)));
      var st = rowState(r);
      if (st.badge) li.appendChild(el('span', 'wl-badge' + (st.off ? ' wl-badge-off' : st.warn ? ' wl-badge-warn' : ''), st.badge));
      if (st.off) { li.classList.add('wl-opt-off'); li.setAttribute('aria-disabled', 'true'); if (r.match) li.title = r.match; }
      li.addEventListener('mousedown', function (e) { e.preventDefault(); choose(r); });
      list.appendChild(li);
    });
    box.appendChild(list);
    var src = state.sources || [];
    var from = src.length > 1 ? src.slice(0, -1).join(', ') + ' and ' + src[src.length - 1] : src[0] || '';
    box.appendChild(el('p', 'wl-suggest-foot', (from ? 'Results from ' + from + '. ' : '') + 'Not listed? Add it as typed.'));
    if (state.active >= 0 && ti) ti.setAttribute('aria-activedescendant', 'wl-opt-' + state.active);
    var a = list.querySelector('[aria-selected="true"]'); if (a && a.scrollIntoView) a.scrollIntoView({ block: 'nearest' });
  }
  function renderPick() {
    var box = root && root.querySelector('[data-role="pick"]'); if (!box) return;
    box.textContent = ''; box.hidden = !state.pick; if (!state.pick) return;
    var p = state.pick;
    box.appendChild(thumb(p.cover, p.kind, true));
    var m = el('span', 'wl-opt-main'); m.appendChild(el('span', 'wl-opt-title', p.title));
    var sub = p.kind === 'series' ? 'Whole series' + (p.count ? ' · ' + p.count + ' books' : '')
      : [p.author, p.released ? (upcoming(p.released) ? 'out ' : '') + releaseText(p.released) : p.year]
          .filter(Boolean).join(' · ');
    if (sub) m.appendChild(el('span', 'wl-opt-sub', sub));
    box.appendChild(m);
    var x = el('button', 'wl-icon-btn'); x.type = 'button'; x.title = 'Pick something else'; x.setAttribute('aria-label', 'Pick something else');
    x.appendChild(icon(ICONS.x));
    x.addEventListener('click', function () {
      state.pick = null; renderPick(); var t = root.querySelector('[data-k="title"]'); if (t) t.focus();
    });
    box.appendChild(x);
  }
  // What the library and the list already hold decides how a suggestion is offered.
  function rowState(r) {
    if (r.kind === 'series') {
      var n = r.count || 0, have = r.have || 0;
      if (n && have >= n) return { off: true, badge: 'All in library' };
      if (r.status === 'wishlist') return { off: true, badge: 'On the list' };
      var b = 'Series' + (n ? ' · ' + n + (n === 1 ? ' book' : ' books') : '');
      return { badge: have ? b + ' · you have ' + have : b };
    }
    if (r.status === 'library') return { off: true, badge: 'In library' };
    if (r.status === 'wishlist') return { off: true, badge: 'On the list' };
    if (r.status === 'similar') return { warn: true, badge: 'Similar in library' };
    return {};
  }
  function choose(r) {
    if (rowState(r).off) return;
    state.pick = r; state.open = false; state.active = -1; state.pending = null; setMsg('', '');
    var t = root.querySelector('[data-k="title"]'), a = root.querySelector('[data-k="author"]');
    if (t) t.value = r.title; if (a) a.value = r.author || '';
    renderSuggest(); renderPick();
    submit(false); // picking a suggestion adds it straight away; a note typed beforehand goes with it
  }
  function onSuggestKey(e) {
    var n = state.results.length;
    if (!n || state.pick) return;
    if (!state.open) { if (e.key === 'ArrowDown') { e.preventDefault(); state.open = true; state.active = 0; renderSuggest(); } return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); state.active = (state.active + 1) % n; renderSuggest(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); state.active = state.active <= 0 ? n - 1 : state.active - 1; renderSuggest(); }
    else if (e.key === 'Enter' && state.active >= 0) { e.preventDefault(); choose(state.results[state.active]); }
    else if (e.key === 'Escape') { state.open = false; renderSuggest(); }
  }
  document.addEventListener('mousedown', function (e) {
    if (!state.open || !root || !e.target.closest) return;
    if (e.target.closest('.wl-suggest, [data-k="title"], [data-k="author"]')) return;
    state.open = false; renderSuggest();
  });

  function setMsg(kind, text) {
    var m = root && root.querySelector('[data-role="msg"]'); if (!m) return null;
    m.textContent = text || ''; if (kind) m.setAttribute('data-kind', kind); else m.removeAttribute('data-kind');
    return m;
  }
  function showPending(p) {
    var m = setMsg('info', p.message); if (!m) return;
    var b = el('button', 'wl-btn wl-btn-quiet', 'Add anyway'); b.type = 'button';
    b.addEventListener('click', function () { submit(true); });
    m.appendChild(b);
  }

  // Marks the Filters button while a search or requester filter is narrowing the list, even when collapsed.
  function filterBadge() {
    var b = root && root.querySelector('.wl-filter-btn'); if (!b) return;
    var on = !!(state.query.trim() || state.who);
    b.classList.toggle('wl-filter-on', on);
    b.title = on ? 'Filters are on' : '';
  }

  function renderList() {
    var list = root.querySelector('[data-role="list"]'); if (!list) return;
    list.textContent = '';
    var q = state.query.trim().toLowerCase();
    var rows = state.items.filter(function (it) {
      if (state.who && it.addedBy !== state.who) return false;
      return !q || (it.title + ' ' + it.author + ' ' + it.note + ' ' + (it.addedBy || '')).toLowerCase().indexOf(q) >= 0;
    });
    var wanted = rows.filter(function (it) { return it.status !== 'available'; });
    var done = rows.filter(function (it) { return it.status === 'available'; });
    var open = state.items.filter(function (it) { return it.status !== 'available'; }).length;
    var cnt = root.querySelector('.wl-count'); if (cnt) cnt.textContent = String(open);
    if (!wanted.length) {
      var li = el('li'); li.style.gridColumn = '1 / -1';
      var box = el('div', 'wl-empty'); box.appendChild(icon(ICONS.gift));
      if (!state.loaded) box.appendChild(el('strong', null, 'Loading…'));
      else if (state.items.length) { box.appendChild(el('strong', null, 'Nothing matches')); box.appendChild(el('p', null, done.length ? 'Nothing waiting; see below for what has arrived.' : 'Try a different search.')); }
      else { box.appendChild(el('strong', null, 'The wishlist is empty')); box.appendChild(el('p', null, 'Add a book above and the admin will see it.')); }
      li.appendChild(box); list.appendChild(li);
    }
    wanted.forEach(function (it) { list.appendChild(card(it)); });
    var sec = root.querySelector('[data-role="done"]'); if (!sec) return;
    sec.textContent = ''; sec.hidden = !done.length;
    if (!done.length) return;
    var head = el('div', 'wl-done-head');
    head.appendChild(el('h2', 'wl-section', 'Now in the library'));
    if (state.me && state.me.admin) {
      var what = done.length === 1 ? '1 book' : done.length + ' books';
      if (state.confirming === 'done') {
        var c = el('span', 'wl-confirm');
        var yes = el('button', 'wl-btn wl-btn-danger', 'Clear ' + what); yes.type = 'button';
        var no = el('button', 'wl-btn wl-btn-quiet', 'Keep'); no.type = 'button';
        yes.addEventListener('click', function () { clearDone(done); });
        no.addEventListener('click', function () { state.confirming = null; renderList(); });
        c.appendChild(yes); c.appendChild(no); head.appendChild(c);
      } else {
        var b = el('button', 'wl-btn wl-btn-quiet', 'Clear now in library'); b.type = 'button';
        b.title = 'Take the ' + what + ' shown here off the wishlist; the books stay in Grimmory';
        b.addEventListener('click', function () { state.confirming = 'done'; renderList(); });
        head.appendChild(b);
      }
    }
    sec.appendChild(head);
    var dl = el('ul', 'wl-list');
    done.forEach(function (it) { dl.appendChild(card(it)); });
    sec.appendChild(dl);
  }

  function card(it) {
    var li = el('li', 'wl-card');
    var row = el('div', 'wl-row'); row.appendChild(thumb(it.cover, it.kind));
    var body = el('div', 'wl-body'); body.appendChild(el('h3', 'wl-book', it.title));
    if (it.author) body.appendChild(el('p', 'wl-author', it.author));
    var badges = el('span', 'wl-badges');
    if (it.kind === 'series') badges.appendChild(el('span', 'wl-badge', 'Whole series' + (it.count ? ' · ' + it.count + ' books' : '')));
    if (it.kind === 'book' && it.released) {
      var soon = upcoming(it.released);
      var rb = el('span', 'wl-badge' + (soon ? ' wl-badge-soon' : ' wl-badge-date'),
        (soon ? 'Out ' : 'Released ') + releaseText(it.released));
      rb.title = soon ? 'Not published yet' : 'Publication date';
      badges.appendChild(rb);
    }
    var lib = it.kind === 'book' && it.status !== 'available' ? it.library : null;
    if (lib) {
      var text = lib.available ? 'Borrow now at ' + lib.name
        : lib.prerelease ? lib.name + ': pre-order'
        : lib.name + ': ' + lib.holds + ' waiting' + (lib.wait ? ' · ~' + lib.wait + ' days' : '');
      var lb = el(lib.url ? 'a' : 'span', 'wl-badge wl-badge-lib' + (lib.available ? ' wl-badge-free' : ''), text);
      if (lib.url) { lb.href = lib.url; lb.target = '_blank'; lb.rel = 'noopener'; }
      lb.title = (lib.available ? 'Free to borrow now' : 'On the library\'s waiting list') +
        (lib.kobo ? ' - can be borrowed straight onto a Kobo' : '');
      badges.appendChild(lb);
    }
    if (it.status === 'available') badges.appendChild(el('span', 'wl-badge wl-badge-ok', it.kind === 'series' ? 'Whole series in the library' : 'In the library'));
    else if (it.kind === 'series' && it.have > 0)
      badges.appendChild(el('span', 'wl-badge wl-badge-warn', it.have + (it.count ? ' of ' + it.count : '') + ' in the library'));
    if (badges.childNodes.length) body.appendChild(badges);
    row.appendChild(body); li.appendChild(row);
    if (it.note) li.appendChild(el('p', 'wl-note', it.note));
    var foot = el('div', 'wl-foot');
    if (it.addedBy) foot.appendChild(el('span', 'wl-avatar', initials(it.addedBy)));
    var who = el('span', 'wl-who'); var line = el('span', 'wl-line');
    if (it.addedBy) {
      line.appendChild(document.createTextNode('Requested by '));
      if (it.mine) line.appendChild(el('span', 'wl-you', 'you')); else line.appendChild(el('strong', 'wl-name', it.addedBy));
    } else line.appendChild(document.createTextNode('Requested'));
    who.appendChild(line); who.appendChild(el('span', 'wl-when', ago(it.addedAt)));
    foot.appendChild(who); foot.appendChild(el('span', 'wl-spacer'));
    if (state.me && state.me.admin && it.status !== 'available') {
      var ok = el('button', 'wl-icon-btn wl-ok'); ok.type = 'button'; ok.title = 'Mark as added to the library';
      ok.setAttribute('aria-label', 'Mark ' + it.title + ' as added to the library'); ok.appendChild(icon(ICONS.check));
      ok.addEventListener('click', function () { markAdded(it); });
      foot.appendChild(ok);
    }
    if (state.me && state.me.admin) {
      if (state.confirming === it.id) {
        var c = el('span', 'wl-confirm');
        var yes = el('button', 'wl-btn wl-btn-danger', 'Remove'); yes.type = 'button';
        var no = el('button', 'wl-btn wl-btn-quiet', 'Keep'); no.type = 'button';
        yes.addEventListener('click', function () { remove(it.id); });
        no.addEventListener('click', function () { state.confirming = null; renderList(); });
        c.appendChild(yes); c.appendChild(no); foot.appendChild(c);
      } else {
        var t = el('button', 'wl-icon-btn'); t.type = 'button'; t.title = 'Remove from wishlist';
        t.setAttribute('aria-label', 'Remove ' + it.title + ' from the wishlist'); t.appendChild(icon(ICONS.trash));
        t.addEventListener('click', function () { state.confirming = it.id; renderList(); });
        foot.appendChild(t);
      }
    }
    li.appendChild(foot); return li;
  }

  // ---- actions ---------------------------------------------------------
  function friendly(r) {
    if (r.status === 401) return 'Your session expired. Sign in again.';
    return (r.body && r.body.message) || 'Something went wrong. Try again.';
  }
  function refresh() {
    return call('GET', '/items').then(function (r) {
      if (r.status === 200) { state.items = r.body.items || []; state.me = r.body.me; state.error = ''; }
      else state.error = friendly(r);
      state.loaded = true; render();
      if (state.me && !push.loaded) loadPush();
      if (state.me && state.me.admin) loadSeries(0);
    }).catch(function () { state.loaded = true; state.error = 'Could not reach the wishlist service.'; render(); });
  }

  // ---- Chrome notifications ----------------------------------------------
  // The owner gets the admin alerts; everyone else only hears when a book they asked for arrives.
  // The service worker lives under /wishlist-static/ so it never takes over Grimmory's own pages.
  var push = { supported: 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window,
    loaded: false, key: '', owner: false, devices: [], sub: null, busy: false, note: '' };
  function pushScope() { return STATIC + '/'; }
  function pushReg() { return navigator.serviceWorker.getRegistration(pushScope()); }
  function loadPush() {
    if (!push.supported) { push.loaded = true; renderPush(); return; }
    Promise.all([call('GET', '/push'), pushReg().then(function (reg) { return reg ? reg.pushManager.getSubscription() : null; })])
      .then(function (res) {
        if (res[0].status === 200) { push.key = res[0].body.key || ''; push.owner = !!res[0].body.owner; push.devices = res[0].body.devices || []; }
        push.sub = res[1]; push.loaded = true; renderPush();
      }).catch(function () { push.loaded = true; renderPush(); });
  }
  function deviceName() {
    var ua = navigator.userAgent;
    var b = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
    var o = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iPhone' : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : 'this device';
    return b + ' on ' + o;
  }
  function keyBytes(k) {
    var s = atob((k + '==='.slice((k.length + 3) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    var a = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a;
  }
  function activated(reg) {
    var w = reg.installing || reg.waiting;
    if (reg.active || !w) return Promise.resolve(reg);
    return new Promise(function (ok) { w.addEventListener('statechange', function () { if (w.state === 'activated') ok(reg); }); });
  }
  function pushBusy(on, note) { push.busy = on; if (note != null) push.note = note; renderPush(); }
  function pushOn() {
    pushBusy(true, '');
    Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') throw new Error(perm === 'denied' ? 'blocked' : 'dismissed');
      return navigator.serviceWorker.register(STATIC + '/push-sw.js', { scope: pushScope() });
    }).then(activated).then(function (reg) {
      // a subscription made with another key cannot be reused
      return reg.pushManager.getSubscription().then(function (old) { return old ? old.unsubscribe() : null; })
        .then(function () {
          return Promise.race([reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(push.key) }),
            new Promise(function (_, no) { setTimeout(function () { no(new Error('Chrome\'s push service did not answer. Try again in a minute.')); }, 20000); })]);
        });
    }).then(function (sub) {
      return call('POST', '/push/subscribe', { subscription: sub.toJSON(), label: deviceName() }).then(function (r) {
        if (r.status !== 200) { sub.unsubscribe(); throw new Error(friendly(r)); }
        push.sub = sub;
        return call('POST', '/push/test', { endpoint: sub.endpoint });
      });
    }).then(function (r) {
      pushBusy(false, r.status === 200 ? 'On. A test notification is on its way.' : 'On, but the test did not go through: ' + friendly(r));
      loadPush();
    })
      .catch(function (e) {
        var m = e && e.message;
        pushBusy(false, m === 'blocked' ? 'Chrome is blocking notifications for this site. Click the icon left of the address bar, allow Notifications, then try again.'
          : m === 'dismissed' ? 'Chrome did not get a yes. Click Turn on again and choose Allow.' : (m || 'Could not switch notifications on.'));
      });
  }
  function pushOff() {
    var sub = push.sub; if (!sub) return;
    pushBusy(true, '');
    call('POST', '/push/unsubscribe', { subscription: { endpoint: sub.endpoint } }).catch(function () {})
      .then(function () { return sub.unsubscribe(); })
      .then(function () { push.sub = null; pushBusy(false, 'Off in this browser.'); loadPush(); },
        function () { pushBusy(false, 'Could not switch notifications off.'); });
  }
  function pushTest() {
    pushBusy(true, '');
    call('POST', '/push/test', { endpoint: push.sub.endpoint }).then(function (r) {
      if (r.status === 404) push.sub = null;       // the server forgot this browser: offer Turn on again
      pushBusy(false, r.status === 200 ? 'Sent. It should pop up in a moment.' : friendly(r));
    }).catch(function () { pushBusy(false, 'Could not reach the wishlist service.'); });
  }
  function renderPush() {
    var box = root && root.querySelector('[data-role="push"]'); if (!box) return;
    box.textContent = '';
    if (!push.loaded) return;
    var head = el('div', 'wl-done-head');
    head.appendChild(el('h2', 'wl-section', 'Notifications'));
    var acts = el('span', 'wl-confirm');
    function btn(text, cls, fn) { var b = el('button', 'wl-btn ' + cls, text); b.type = 'button'; b.disabled = push.busy; b.addEventListener('click', fn); acts.appendChild(b); }
    var known = push.sub && push.devices.length;
    var what = push.owner ? 'sign-ins, wishlist requests, release and library news, backup and health alerts'
      : 'a notification when a book you asked for arrives in the library';
    var line;
    if (!push.supported) line = 'This browser cannot show notifications from Grimmory. Open it in Chrome to switch them on.';
    else if (!push.key) line = 'Notifications are not available: the wishlist service is missing its push support.';
    else if (Notification.permission === 'denied') line = 'Chrome is blocking notifications for this site. Click the icon left of the address bar and allow Notifications.';
    else if (known) {
      line = 'On in this browser. You get ' + what + ', even with Grimmory closed (Chrome has to be running).';
      btn('Send a test', 'wl-btn-quiet', pushTest); btn('Turn off here', 'wl-btn-quiet', pushOff);
    } else {
      line = push.owner ? 'Get ' + what + ' as notifications on this computer.' : 'Get ' + what + '.';
      btn('Turn on', 'wl-btn-primary', pushOn);
    }
    if (acts.childNodes.length) head.appendChild(acts);
    box.appendChild(head);
    box.appendChild(el('p', 'wl-sub', line));
    if (push.devices.length) {
      box.appendChild(el('p', 'wl-sub', 'On in: ' + push.devices.map(function (d) { return d.label; }).join(', ') + '.'));
    }
    if (push.note) { var n = el('p', 'wl-msg', push.note); n.setAttribute('data-kind', 'info'); n.setAttribute('role', 'status'); box.appendChild(n); }
  }

  // ---- missing from your series (admin) ----------------------------------
  var seriesTimer = 0;
  function loadSeries(round) {
    clearTimeout(seriesTimer);
    call('GET', '/series/missing').then(function (r) {
      if (r.status !== 200) return;
      state.gaps = r.body; renderSeries();
      // Hardcover is asked in the background the first time; look again while it works
      if (r.body.pending && round < 20 && isOpen()) seriesTimer = setTimeout(function () { loadSeries(round + 1); }, 4000);
    }).catch(function () {});
  }
  function renderSeries() {
    var box = root && root.querySelector('[data-role="series"]'); if (!box) return;
    box.textContent = '';
    var g = state.gaps; if (!g || !g.enabled) return;
    var sh = el('div', 'wl-done-head');
    sh.appendChild(el('h2', 'wl-section', 'Missing from your series'));
    if (g.series.length > 1) {
      var allShut = g.series.every(function (s) { return state.collapsed.indexOf(s.key) >= 0; });
      var all = el('button', 'wl-btn wl-btn-quiet', allShut ? 'Expand all' : 'Collapse all'); all.type = 'button';
      all.addEventListener('click', function () {
        var keys = g.series.map(function (s) { return s.key; });
        state.collapsed = state.collapsed.filter(function (k) { return keys.indexOf(k) < 0; });
        if (!allShut) state.collapsed = state.collapsed.concat(keys);
        saveCollapsed(); renderSeries();
      });
      sh.appendChild(all);
    }
    box.appendChild(sh);
    if (g.pending) box.appendChild(el('p', 'wl-sub', 'Checking ' + g.pending + ' series on Hardcover…'));
    if (!g.series.length && !g.pending) box.appendChild(el('p', 'wl-sub', 'Nothing missing: you have every published book of the series Hardcover knows.'));
    g.series.forEach(function (s) {
      var shut = state.collapsed.indexOf(s.key) >= 0;
      var card = el('div', 'wl-card wl-gap' + (shut ? ' wl-gap-shut' : ''));
      var top = el('div', 'wl-gap-head');
      var fold = el('button', 'wl-icon-btn wl-fold'); fold.type = 'button';
      fold.title = shut ? 'Show the missing books' : 'Collapse this series';
      fold.setAttribute('aria-expanded', shut ? 'false' : 'true');
      fold.setAttribute('aria-label', (shut ? 'Expand ' : 'Collapse ') + s.name); fold.appendChild(icon(ICONS.chevron));
      fold.addEventListener('click', function () { toggleSeries(s.key); });
      top.appendChild(fold);
      var t = el('div', 'wl-body');
      t.appendChild(el('h3', 'wl-book', s.name));
      var n = s.missing.length + s.upcoming.length;
      t.appendChild(el('p', 'wl-author', (s.author ? s.author + ' · ' : '') + 'you have ' + s.have + ' of ' + s.total +
        (shut ? ' · ' + n + (n === 1 ? ' book' : ' books') + ' to get' : '')));
      t.addEventListener('click', function () { toggleSeries(s.key); });
      top.appendChild(t);
      var no = el('button', 'wl-btn wl-btn-quiet', 'Not interested'); no.type = 'button';
      no.title = 'Stop suggesting books from this series';
      no.addEventListener('click', function () { ignore(s.key); });
      top.appendChild(no); card.appendChild(top);
      if (shut) { box.appendChild(card); return; }
      var ul = el('ul', 'wl-gap-list');
      s.missing.concat(s.upcoming).forEach(function (b) {
        var li = el('li', 'wl-gap-row');
        li.appendChild(thumb(b.cover, 'book', true));
        var bt = el('div', 'wl-body');
        bt.appendChild(el('strong', 'wl-gap-title', '#' + b.position + ' ' + b.title));
        var badges = el('span', 'wl-badges');
        if (b.released) {
          var soon = upcoming(b.released);
          badges.appendChild(el('span', 'wl-badge' + (soon ? ' wl-badge-soon' : ' wl-badge-date'), (soon ? 'Out ' : 'Released ') + releaseText(b.released)));
        }
        if (b.status === 'wishlist') badges.appendChild(el('span', 'wl-badge wl-badge-ok', 'On the wishlist'));
        bt.appendChild(badges); li.appendChild(bt);
        if (b.status !== 'wishlist') {
          var add = el('button', 'wl-btn wl-btn-quiet', 'Add'); add.type = 'button';
          add.setAttribute('aria-label', 'Add ' + b.title + ' to the wishlist');
          add.addEventListener('click', function () {
            add.disabled = true;
            call('POST', '/items', { title: b.title, author: b.author, cover: b.cover, released: b.released,
              note: s.name + ' #' + b.position, force: true }).then(function (r) {
              if (r.status === 201) { toast('Added ' + b.title); refresh(); } else { add.disabled = false; toast(friendly(r)); }
            }).catch(function () { add.disabled = false; toast('Could not reach the wishlist service.'); });
          });
          li.appendChild(add);
        }
        var x = el('button', 'wl-icon-btn'); x.type = 'button'; x.title = 'Not interested in this book';
        x.setAttribute('aria-label', 'Not interested in ' + b.title); x.appendChild(icon(ICONS.x));
        x.addEventListener('click', function () { ignore(b.key); });
        li.appendChild(x);
        ul.appendChild(li);
      });
      card.appendChild(ul); box.appendChild(card);
    });
    if (g.complete && g.complete.length) {
      var c = el('p', 'wl-sub wl-gap-done');
      c.appendChild(el('strong', null, 'Complete: ')); c.appendChild(document.createTextNode(g.complete.join(', ')));
      box.appendChild(c);
    }
    if (g.unknown && g.unknown.length) box.appendChild(el('p', 'wl-sub', 'Not found on Hardcover: ' + g.unknown.join(', ')));
    var hid = g.hidden || [];
    if (hid.length) {
      var hb = el('button', 'wl-btn wl-btn-quiet wl-hidden-btn',
        (state.hiddenOpen ? 'Hide' : 'Show') + ' what you said no to (' + hid.length + ')'); hb.type = 'button';
      hb.setAttribute('aria-expanded', state.hiddenOpen ? 'true' : 'false');
      hb.addEventListener('click', function () { state.hiddenOpen = !state.hiddenOpen; renderSeries(); });
      box.appendChild(hb);
      if (state.hiddenOpen) {
        var hc = el('div', 'wl-card'), hl = el('ul', 'wl-gap-list wl-hidden'); hc.appendChild(hl);
        hid.forEach(function (h) {
          var li = el('li', 'wl-gap-row');
          var bt = el('div', 'wl-body');
          bt.appendChild(el('strong', 'wl-gap-title', h.title ? '#' + h.position + ' ' + h.title : h.series));
          bt.appendChild(el('span', 'wl-author', h.title ? h.series : 'Whole series'));
          li.appendChild(bt);
          var back = el('button', 'wl-btn wl-btn-quiet', 'Show again'); back.type = 'button';
          back.setAttribute('aria-label', 'Suggest ' + (h.title || h.series) + ' again');
          back.addEventListener('click', function () { back.disabled = true; unignore(h.key); });
          li.appendChild(back); hl.appendChild(li);
        });
        box.appendChild(hc);
      }
    }
  }
  function saveCollapsed() { try { localStorage.setItem('wl-collapsed', JSON.stringify(state.collapsed)); } catch (e) {} }
  function toggleSeries(key) {
    var i = state.collapsed.indexOf(key);
    if (i >= 0) state.collapsed.splice(i, 1); else state.collapsed.push(key);
    saveCollapsed(); renderSeries();
  }
  function unignore(key) {
    call('POST', '/series/unignore', { key: key }).then(function (r) {
      if (r.status === 200) { toast('It will be suggested again'); loadSeries(0); } else toast(friendly(r));
    }).catch(function () { toast('Could not reach the wishlist service.'); });
  }
  function ignore(key) {
    call('POST', '/series/ignore', { key: key }).then(function (r) {
      if (r.status === 200) loadSeries(0); else toast(friendly(r));
    }).catch(function () { toast('Could not reach the wishlist service.'); });
  }

  function val(k) { var i = root.querySelector('[data-k="' + k + '"]'); return i ? i.value : ''; }
  function submit(force) {
    var title = val('title').trim();
    if (!title) { setMsg('error', 'Give the book a title.'); var t = root.querySelector('[data-k="title"]'); if (t) t.focus(); return; }
    var btn = root.querySelector('.wl-btn-primary'); if (btn) btn.disabled = true;
    state.pending = null;
    var p = state.pick;
    var payload = { title: title, author: val('author'), note: val('note'), force: !!force };
    if (p) { payload.kind = p.kind; payload.cover = p.cover || ''; payload.count = p.count || 0; payload.released = p.released || ''; }
    call('POST', '/items', payload).then(function (r) {
      if (r.status === 201) {
        state.pick = null; state.results = []; state.open = false; state.searchNote = '';
        ['title', 'author', 'note'].forEach(function (k) { var i = root.querySelector('[data-k="' + k + '"]'); if (i) i.value = ''; });
        toast(p ? 'Added “' + p.title + '” to the wishlist' : 'Added to the wishlist');
        return refresh().then(function () { var t2 = root.querySelector('[data-k="title"]'); if (t2) t2.focus(); });
      }
      if (r.status === 409 && (r.body.error === 'in_library' || r.body.error === 'similar')) { state.pending = r.body; showPending(r.body); }
      else setMsg('error', friendly(r));
      if (btn) btn.disabled = false;
    }).catch(function () { setMsg('error', 'Could not reach the wishlist service.'); if (btn) btn.disabled = false; });
  }
  function markAdded(it) {
    call('POST', '/items/' + it.id + '/available').then(function (r) {
      if (r.status === 200 || r.status === 404) { toast(it.mine ? 'Marked as added' : 'Marked as added. ' + it.addedBy + ' will be told.'); return refresh(); }
      state.error = friendly(r); render();
    });
  }
  function remove(id) {
    call('DELETE', '/items/' + id).then(function (r) {
      state.confirming = null;
      if (r.status === 200 || r.status === 404) { toast('Removed'); return refresh(); }
      state.error = friendly(r); render();
    });
  }

  // Only the arrived books shown (a search or requester filter narrows them); the books stay in Grimmory.
  function clearDone(items) {
    Promise.all(items.map(function (it) {
      return call('DELETE', '/items/' + it.id).then(function (r) { return r.status === 200 || r.status === 404; },
        function () { return false; });
    })).then(function (oks) {
      state.confirming = null;
      var bad = oks.filter(function (ok) { return !ok; }).length;
      toast(bad ? 'Cleared ' + (oks.length - bad) + '; ' + bad + ' could not be removed. Try again.' : 'Cleared');
      return refresh();
    });
  }

  // ---- routing / nav ---------------------------------------------------
  function isOpen() { return location.hash.replace(/^#\/?/, '') === 'wishlist'; }
  function host() { return document.querySelector('main.layout-main') || document.querySelector('.layout-main'); }

  // A fixed top bar (Grimmory's mobile header) covers the top of the page; start below it.
  function applyInset() {
    var h = host(); if (!h || !root || root.hidden) return;
    var keep = root.style.pointerEvents; root.style.pointerEvents = 'none';
    var e = document.elementFromPoint(Math.floor(window.innerWidth / 2), 4);
    root.style.pointerEvents = keep;
    var bottom = 0;
    for (; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      var pos = getComputedStyle(e).position;
      if (pos === 'fixed' || pos === 'sticky') bottom = Math.max(bottom, e.getBoundingClientRect().bottom);
    }
    // Where the page sits with the window scrolled to the top, so how far the visitor had scrolled
    // on the previous page cannot leak into the spacing.
    var top = h.getBoundingClientRect().top + (window.pageYOffset || 0);
    var over = Math.max(0, Math.round(bottom - top));
    root.style.paddingTop = 'calc(' + over + 'px + 14px)';
  }
  window.addEventListener('resize', applyInset);

  // A page opened by navigating starts at the top, whatever the last page's scroll position was.
  function toTop() {
    try { window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); } catch (e) { window.scrollTo(0, 0); }
    for (var n = host(); n && n !== document.documentElement; n = n.parentElement) if (n.scrollTop) n.scrollTop = 0;
    if (root) root.scrollTop = 0;
  }
  // Angular finishes laying the new route out after we open, so measure again as it settles.
  function settleInset() {
    [80, 250, 600, 1200].forEach(function (ms) { setTimeout(function () { if (root && !root.hidden) applyInset(); }, ms); });
  }

  function sync() {
    var h = host(); if (!h) return;
    if (!root) build();
    if (root.parentNode !== h) { if (getComputedStyle(h).position === 'static') h.style.position = 'relative'; h.appendChild(root); }
    var open = isOpen();
    if (open && root.hidden) {
      root.hidden = false; state.confirming = null; toTop(); applyInset(); settleInset();
      Array.prototype.forEach.call(h.children, function (c) { if (c !== root) c.setAttribute('inert', ''); });
      markActive(true); refresh(); checkNotices(0);
    } else if (!open && !root.hidden) {
      root.hidden = true;
      Array.prototype.forEach.call(h.children, function (c) { c.removeAttribute('inert'); });
      markActive(false);
    }
    if (!navItem || !document.contains(navItem)) injectNav();
  }

  function markActive(on) {
    if (!navItem) return;
    var side = navItem.closest('.layout-sidebar, app-menu') || document;
    if (on) {
      savedActive = [];
      Array.prototype.forEach.call(side.querySelectorAll('.active-route, [aria-current="page"]'), function (n) {
        if (navItem.contains(n)) return;
        savedActive.push(n); n.classList.remove('active-route'); n.removeAttribute('aria-current');
      });
      navItem.classList.add('active-route'); navItem.setAttribute('aria-current', 'page');
    } else {
      navItem.classList.remove('active-route'); navItem.removeAttribute('aria-current');
      savedActive.forEach(function (n) { n.classList.add('active-route'); }); savedActive = [];
    }
  }

  function findTemplate() {
    var side = document.querySelector('.layout-sidebar, app-menu, nav');
    if (!side) return null;
    var all = side.querySelectorAll('span, a, div');
    for (var i = 0; i < all.length; i++) {
      var n = all[i];
      if (n.children.length === 0 && n.textContent.trim() === 'Notebook') {
        return n.closest('app-menu-item, li, .sidebar-item-container') || n.parentElement;
      }
    }
    return null;
  }

  function injectNav() {
    var tpl = findTemplate(); if (!tpl || !tpl.parentNode) return;
    var old = document.getElementById('wl-nav'); if (old) old.remove();
    var n = tpl.cloneNode(true);
    n.id = 'wl-nav'; n.classList.remove('active-route'); n.removeAttribute('aria-current');
    var leaves = n.querySelectorAll('*');
    for (var i = 0; i < leaves.length; i++) {
      if (leaves[i].children.length === 0 && leaves[i].textContent.trim() === 'Notebook') { leaves[i].textContent = 'Wishlist'; break; }
    }
    var ic = n.querySelector('svg, i, lucide-icon, app-icon');
    if (ic) {
      var g = icon(ICONS.gift);
      g.setAttribute('class', ic.getAttribute('class') || '');
      // Match the size the other menu icons are drawn at. Measure the real one in the page (the
      // clone is detached, so it has no size), and trust only a believable answer.
      var live = tpl.querySelector('svg, i, lucide-icon, app-icon');
      var r = live ? live.getBoundingClientRect() : null;
      var w = r ? Math.round(Math.max(r.width, r.height)) : 0;
      if (w < 14 || w > 40) w = 24;
      g.setAttribute('width', w); g.setAttribute('height', w);
      g.style.cssText = 'flex:none;width:' + w + 'px;height:' + w + 'px;min-width:' + w + 'px';
      ic.replaceWith(g);
    }
    Array.prototype.forEach.call(n.querySelectorAll('.p-badge, .badge, [class*="end-content"], [class*="count"]'), function (x) { x.remove(); });
    Array.prototype.forEach.call(n.querySelectorAll('[routerlink], [ng-reflect-router-link]'), function (x) {
      x.removeAttribute('routerlink'); x.removeAttribute('ng-reflect-router-link');
    });
    Array.prototype.forEach.call(n.querySelectorAll('a'), function (a) { a.setAttribute('href', '#wishlist'); });
    n.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      location.hash = 'wishlist';
      var mask = document.querySelector('.layout-mask'); if (mask && mask.offsetParent !== null) mask.click();
    }, true);
    tpl.parentNode.insertBefore(n, tpl.nextSibling);
    navItem = n; if (isOpen()) markActive(true);
  }

  // Any navigation elsewhere must close the page.
  ['pushState', 'replaceState'].forEach(function (m) {
    var orig = history[m];
    history[m] = function () { var r = orig.apply(this, arguments); setTimeout(sync, 0); return r; };
  });
  window.addEventListener('hashchange', sync);
  window.addEventListener('popstate', sync);
  var queued = false;
  new MutationObserver(function () {
    if (queued) return; queued = true;
    setTimeout(function () { queued = false; sync(); }, 60);   // not rAF: it stops in background tabs
  }).observe(document.documentElement, { childList: true, subtree: true });

  // ---- "your book arrived" notices (shown on any Grimmory page) -----------
  var noticeBox = null, shown = {}, lastNoticeCheck = 0;
  function checkNotices(tries) {
    if (!token()) { if (tries > 0) setTimeout(function () { checkNotices(tries - 1); }, 500); return; }
    lastNoticeCheck = Date.now();
    call('GET', '/notices').then(function (r) {
      if (r.status === 200) (r.body.notices || []).forEach(showNotice);
    }).catch(function () {});
  }
  function showNotice(n) {
    if (shown[n.id]) return; shown[n.id] = true;
    if (!noticeBox) { noticeBox = el('div', 'wl-notices'); noticeBox.setAttribute('role', 'status'); document.body.appendChild(noticeBox); }
    var c = el('div', 'wl-notice');
    var ic = el('span', 'wl-notice-icon'); ic.appendChild(icon(ICONS.book)); c.appendChild(ic);
    var t = el('div', 'wl-notice-text');
    t.appendChild(el('strong', null, n.title));
    t.appendChild(el('span', null, (n.kind === 'series' ? 'Your series is' : 'Your book is') + ' now in the library' + (n.author ? ' (' + n.author + ')' : '') + '.'));
    c.appendChild(t);
    var b = el('button', 'wl-btn wl-btn-quiet', 'Got it'); b.type = 'button';
    b.addEventListener('click', function () {
      call('POST', '/notices/seen', { ids: [n.id] }).catch(function () {});
      c.remove();
    });
    c.appendChild(b); noticeBox.appendChild(c);
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && Date.now() - lastNoticeCheck > 300000) checkNotices(0);
  });

  // ---- login / visit ping ----------------------------------------------
  function ping(reason, tries) {
    if (!token()) { if (tries > 0) setTimeout(function () { ping(reason, tries - 1); }, 300); return; }
    call('POST', '/seen', { reason: reason }).catch(function () {});
  }
  function isLogin(url, method) {
    return String(method || 'GET').toUpperCase() === 'POST' && /\/api\/v1\/auth\/login(\?|$)/.test(String(url));
  }
  var xo = XMLHttpRequest.prototype.open, xs = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__wl = isLogin(u, m); return xo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (this.__wl) this.addEventListener('load', function () { if (this.status >= 200 && this.status < 300) { ping('login', 10); checkNotices(10); } });
    return xs.apply(this, arguments);
  };
  if (nativeFetch) {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = (init && init.method) || (input && input.method) || 'GET';
      var p = nativeFetch(input, init);
      if (isLogin(url, method)) p.then(function (r) { if (r.ok) { ping('login', 10); checkNotices(10); } }, function () {});
      return p;
    };
  }
  try {
    if (!sessionStorage.getItem('wl-visit')) { sessionStorage.setItem('wl-visit', '1'); setTimeout(function () { ping('visit', 5); }, 1500); }
  } catch (e) { /* storage blocked: skip */ }

  sync();
  setTimeout(function () { checkNotices(6); }, 1800);
})();
