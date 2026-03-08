const CACHE_NAME = 'apex-terminal-v2';
const ASSETS_TO_CACHE = [
    '/',
    '/static/style.css',
    '/manifest.json',
    '/icon-192.png'
];

self.addEventListener('install', event => {
    // Force new service worker to install immediately
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(ASSETS_TO_CACHE).catch(err => console.error("Cache addAll failed:", err)))
    );
});

self.addEventListener('activate', event => {
    // Clean up old caches
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;
    if (event.request.url.includes('/api/')) return;
    if (event.request.url.includes('socket.io')) return;

    // Network First Strategy
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // If network succeeds, update cache
                const resClone = response.clone();
                caches.open(CACHE_NAME).then(cache => {
                    cache.put(event.request, resClone);
                });
                return response;
            })
            .catch(() => {
                // Network failed, fallback to cache
                return caches.match(event.request).then(cachedResponse => {
                    if (cachedResponse) return cachedResponse;
                    return new Response("Offline Mode active. API not reachable.");
                });
            })
    );
});
