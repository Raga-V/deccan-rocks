// Offline-first service worker. In the field the laptop is unreachable, so nothing here may wait on the network.
const SHELL = 'shell-v1';
const VENDOR = 'vendor-v1';  // CLIP + runtime: big, immutable
const MEDIA = 'media-v1';    // rock photos + voice lines: immutable (filenames are timestamped)
const SHELL_FILES = [
  '/', '/index.html', '/style.css', '/manifest.webmanifest',
  '/js/app.js', '/js/store.js', '/js/clip.js', '/js/geo.js',
  '/icons/icon-192.png', '/icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  const keep = new Set([SHELL, VENDOR, MEDIA]);
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;

  if (url.pathname.startsWith('/vendor/') || url.pathname.startsWith('/media/')) {
    // Cache first: these never change.
    const bucket = url.pathname.startsWith('/vendor/') ? VENDOR : MEDIA;
    e.respondWith(
      caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || fetch(e.request).then(async (res) => {
        if (res.ok && res.status === 200) await (await caches.open(bucket)).put(e.request, res.clone());
        return res;
      })),
    );
    return;
  }

  // App shell: stale-while-revalidate. Serve the cached copy instantly (the hill has no Wi-Fi),
  // refresh it in the background when we're home.
  e.respondWith(
    caches.open(SHELL).then(async (cache) => {
      const hit = await cache.match(e.request, { ignoreSearch: true });
      const refresh = fetch(e.request)
        .then((res) => { if (res.ok) cache.put(e.request, res.clone()); return res; })
        .catch(() => null);
      if (hit) { e.waitUntil(refresh); return hit; }
      return (await refresh) || (await cache.match('/index.html')) || Response.error();
    }),
  );
});
