const CACHE_NAME = 'apex-terminal-v1';
const ASSETS_TO_CACHE = [
    '/',
    '/static/style.css',
    '/manifest.json',
    '/icon-192.png'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(ASSETS_TO_CACHE).catch(err => console.error("Cache addAll failed:", err)))
    );
});

self.addEventListener('fetch', event => {
    // Solo cachear GET
    if (event.request.method !== 'GET') return;

    // No cachear API
    if (event.request.url.includes('/api/')) return;

    event.respondWith(
        caches.match(event.request)
            .then(response => {
                // Fallback a red si no está en cache
                return response || fetch(event.request).then(fetchRes => {
                    return caches.open(CACHE_NAME).then(cache => {
                        // Guardar en cache para la próxima vez
                        cache.put(event.request, fetchRes.clone());
                        return fetchRes;
                    });
                });
            }).catch(() => {
                // Si todo falla (offline)
                return new Response("Offline Mode active. API not reachable.");
            })
    );
});
