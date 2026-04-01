/* alerta.pe — Service Worker v1.0 */
const CACHE = 'alertape-v1';

const PRECACHE = [
  '/',
  '/pioneros',
  '/static/manifest.json',
  'https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@400;500;600;700&display=swap',
];

/* Instalar: cachea recursos estáticos */
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

/* Activar: limpia caches viejos */
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

/* Fetch: network-first para API, cache-first para estáticos */
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  /* API siempre va a la red */
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({ error: 'Sin conexión' }), {
          headers: { 'Content-Type': 'application/json' }
        })
      )
    );
    return;
  }

  /* Estáticos: cache-first */
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        /* Solo cachear respuestas válidas */
        if (!res || res.status !== 200 || res.type === 'opaque') return res;
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }).catch(() => caches.match('/'));
    })
  );
});

/* Push notifications (para uso futuro) */
self.addEventListener('push', e => {
  if (!e.data) return;
  const data = e.data.json();
  const tipo  = data.tipo || 'info';
  const iconMap = { urgent: '🔴', warn: '🟡', info: '🔵' };
  const icon = iconMap[tipo] || '🔔';

  e.waitUntil(
    self.registration.showNotification(`${icon} alerta.pe`, {
      body:  data.mensaje || 'Nueva alerta',
      icon:  '/static/img/icon-192.png',
      badge: '/static/img/icon-192.png',
      tag:   data.id || 'alerta',
      data:  { url: data.url || '/pioneros' },
      vibrate: tipo === 'urgent' ? [200, 100, 200] : [100],
      requireInteraction: tipo === 'urgent',
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(list => {
      const url = e.notification.data?.url || '/';
      const existing = list.find(c => c.url.includes(url) && 'focus' in c);
      if (existing) return existing.focus();
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});