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

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Solo cachear archivos estáticos con extensión conocida
  const esEstatico = /\.(css|js|png|svg|webp|woff2?|mp3|ico|woff)$/.test(url.pathname);

  if (!esEstatico) {
    // Rutas dinámicas: pasar directo al servidor sin interceptar
    return;
  }

  // Cache-first solo para archivos estáticos
  event.respondWith(
    caches.match(event.request).then(cached =>
      cached || fetch(event.request).then(res => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open('alertape-v1').then(c => c.put(event.request, clone));
        }
        return res;
      })
    )
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
