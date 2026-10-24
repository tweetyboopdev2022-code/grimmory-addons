/* Adds a "Set up my Kobo" box to Grimmory's Kobo sync settings (Settings → Devices). Grimmory only
   shows that section to admins and to accounts allowed to sync a Kobo, so the box follows the same
   permission. The page itself lives outside Grimmory's app, at /kobo-setup/. Plain JS, no dependencies.

   Deliberately light: no observer on the page. Once a second it checks the address, and only on
   /settings does it look for the Kobo section; everywhere else it does nothing. */
(function () {
  'use strict';
  var ID = 'ks-setup-box';

  function el(tag, css, text) {
    var n = document.createElement(tag);
    if (css) n.style.cssText = css;
    if (text) n.textContent = text;
    return n;
  }

  function box() {
    var b = el('div', 'display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;' +
      'margin:12px 0 20px;padding:14px 16px;border-radius:10px;' +
      'border:1px solid var(--p-content-border-color, rgba(127,127,127,.35));' +
      'background:var(--p-content-background, transparent)');
    b.id = ID;
    var text = el('div', 'display:flex;flex-direction:column;gap:2px;min-width:220px;flex:1');
    text.appendChild(el('strong', null, 'Set up a Kobo automatically'));
    text.appendChild(el('span', 'opacity:.75;font-size:.92em',
      'Plug your Kobo into this computer and connect it to your account in one step. ' +
      'Works in Chrome or Edge; Mac and Windows double-click files are on the same page.'));
    var a = el('a', 'display:inline-block;padding:8px 16px;border-radius:8px;font-weight:600;text-decoration:none;' +
      'background:var(--p-primary-color, #3b82f6);color:var(--p-primary-contrast-color, #fff)', 'Set up my Kobo');
    a.href = '/kobo-setup/';
    b.appendChild(text); b.appendChild(a);
    return b;
  }

  function check() {
    if (location.pathname.indexOf('/settings') !== 0) return;
    if (document.getElementById(ID)) return;
    var header = document.querySelector('app-kobo-sync-setting-component .settings-header');
    if (!header || !header.parentNode) return;
    // Outside the part of the section Angular redraws: right after its title block.
    header.parentNode.insertBefore(box(), header.nextSibling);
  }

  setInterval(function () { try { check(); } catch (e) { /* never get in Grimmory's way */ } }, 1000);
})();
