/* Grimmory Wishlist: shows push messages as Chrome notifications, even with Grimmory closed.
   Scoped to /wishlist-static/ so it never takes over Grimmory's own pages. */
'use strict';
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(m.title || 'Grimmory', {
    body: m.body || '',
    icon: '/favicon.ico',
    data: { url: m.url || '/' },
    // a failed backup stays on screen until dismissed; the rest fade like any notification
    requireInteraction: !!m.urgent
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) if (list[i].url === url && 'focus' in list[i]) return list[i].focus();
    return self.clients.openWindow(url);
  }));
});
