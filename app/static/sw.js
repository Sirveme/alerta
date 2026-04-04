/* alerta.pe — Service Worker v2.0
 *
 * Estrategia:
 * - Cache-first para estáticos (CSS, JS, fonts, imágenes, audio)
 * - Network-first para API y rutas autenticadas
 * - Offline fallback para páginas HTML
 */
const CACHE = 'alertape-v2';

const PRECACHE = [
  '/',
  '/login',
  '/dashboard',
  '/pioneros',
  '/static/manifest.json',
  '/static/css/base.css',
  '/static/css/cabecera.css',
  '/static/js/temas.js',
  '/static/js/auth.js',
  '/static/js/cabecera.js',
  '/static/js/config.js',
];

/* Instalar: cachear recursos estáticos */
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

/* Activar: limpiar caches viejos */
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

/* Fetch */
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  /* API y auth: network-first */
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/auth/') ||
      url.pathname.startsWith('/config/') ||
      url.pathname.startsWith('/empresas/')) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({ error: 'Sin conexión' }), {
          headers: { 'Content-Type': 'application/json' },
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
        if (!res || res.status !== 200 || res.type === 'opaque') return res;
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }).catch(() => {
        /* Offline fallback para HTML */
        if (e.request.mode === 'navigate') {
          return caches.match('/login');
        }
      });
    })
  );
});

/* Push notifications */
self.addEventListener('push', e => {
  if (!e.data) return;
  const data = e.data.json();
  const tipo = data.tipo || 'info';
  const iconMap = { urgente: '🔴', importante: '🟡', info: '🔵' };
  const icon = iconMap[tipo] || '🔔';

  e.waitUntil(
    self.registration.showNotification(`${icon} alerta.pe`, {
      body: data.mensaje || 'Nueva alerta',
      icon: '/static/img/icon-192.png',
      badge: '/static/img/icon-192.png',
      tag: data.id || 'alerta',
      data: { url: data.url || '/dashboard' },
      vibrate: tipo === 'urgente' ? [200, 100, 200] : [100],
      requireInteraction: tipo === 'urgente',
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(list => {
      const url = e.notification.data?.url || '/dashboard';
      const existing = list.find(c => c.url.includes(url) && 'focus' in c);
      if (existing) return existing.focus();
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
