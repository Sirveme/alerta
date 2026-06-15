/* ═══════════════════════════════════════════════════════════════════
   sw.js — Service Worker alerta.pe (zAlerta-01 B.7)
   Offline-first para la UI: cachea assets estáticos. Las páginas HTML
   usan network-first (datos frescos), con fallback a caché si no hay red.
   Push: handler listo; el ENVÍO real es una fase aparte.
   ═══════════════════════════════════════════════════════════════════ */

const CACHE = 'alertape-v2';
const ASSETS = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/tema.js',
  '/static/js/ia.js',
  '/static/img/favicon.svg',
  '/static/img/icono.svg',
  '/manifest.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;            // no cachear POST (login, voz, etc.)
  const url = new URL(req.url);

  // Assets estáticos: cache-first
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copia = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copia));
        return res;
      }))
    );
    return;
  }

  // HTML / navegación: network-first con fallback a caché
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(() => caches.match(req).then((hit) => hit ||
        new Response('<h1>Sin conexión</h1><p>Reabrí la app cuando tengas red.</p>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } })))
    );
  }
});

// ── Push (preparado; el envío real es fase aparte) ──
self.addEventListener('push', (e) => {
  let data = { title: 'alerta.pe', body: 'Tenés una novedad de SUNAT.' };
  try { if (e.data) data = e.data.json(); } catch (_) {}
  e.waitUntil(self.registration.showNotification(data.title || 'alerta.pe', {
    body: data.body || '',
    icon: '/static/img/icono.svg',
    badge: '/static/img/icono.svg',
    data: data.url || '/',
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data || '/'));
});
