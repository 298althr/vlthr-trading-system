const CACHE_NAME = 'vlthr-v3.5';
const SHELL = [
  '/favicon.svg',
  '/manifest.json'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(names => Promise.all(
      names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const { request } = e;
  const url = new URL(request.url);

  // Never cache HTML, JS, CSS, or API calls — always fetch fresh after rebuild
  const isAppAsset = url.pathname.endsWith('.html') ||
                     url.pathname.endsWith('.js') ||
                     url.pathname.endsWith('.css') ||
                     url.pathname.startsWith('/api/');

  if (isAppAsset) {
    e.respondWith(
      fetch(request).catch(() => caches.match(request))
    );
    return;
  }

  // For other requests (favicon, manifest, images): cache first, network fallback
  e.respondWith(
    caches.match(request).then(response => {
      if (response) return response;
      return fetch(request).then(netRes => {
        if (netRes && netRes.status === 200) {
          const clone = netRes.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
        }
        return netRes;
      });
    })
  );
});

self.addEventListener('message', e => {
  if (e.data === 'skipWaiting') self.skipWaiting();
});

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  e.waitUntil(
    self.registration.showNotification(data.title || 'VLTHR Signal', {
      body: data.body || 'New trading signal detected',
      icon: '/favicon.svg',
      badge: '/favicon.svg',
      tag: data.tag || 'signal'
    })
  );
});
