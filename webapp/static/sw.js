/* ═══════════════════════════════════════════════════════════════════
   sw.js — Service Worker alerta.pe (zAlerta-01 B.7)
   Offline-first para la UI: cachea assets estáticos. Las páginas HTML
   usan network-first (datos frescos), con fallback a caché si no hay red.
   Push: handler listo; el ENVÍO real es una fase aparte.
   ═══════════════════════════════════════════════════════════════════ */

const CACHE = 'alertape-v11';
const ASSETS = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/tema.js',
  '/static/js/ia.js',
  '/static/js/push.js',
  '/static/js/dock.js',
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
        new Response('<h1>Sin conexión</h1><p>Reabre la app cuando tengas red.</p>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } })))
    );
  }
});

// ── Push (zAlerta-07 / zAlerta-12): muestra el aviso enviado por el worker ──
self.addEventListener('push', (e) => {
  let data = { title: 'alerta.pe', body: 'Tienes una novedad de SUNAT.', url: '/resumen' };
  try { if (e.data) data = e.data.json(); } catch (_) {}
  const opts = {
    body: data.body || '',
    icon: '/static/img/icono.svg',
    badge: '/static/img/icono.svg',
    data: { url: data.url || '/resumen' },
  };
  // Acciones GRACIAS / ENTRAR (si el navegador las soporta). Si no, al tocar
  // se abre la app (equivale a ENTRAR) y el GRACIAS se ofrece dentro de la app.
  if (data.acciones) {
    opts.actions = [
      { action: 'gracias', title: 'GRACIAS' },
      { action: 'entrar', title: 'ENTRAR' },
    ];
  }
  e.waitUntil(self.registration.showNotification(data.title || 'alerta.pe', opts));
});

// Al tocar: GRACIAS registra la lectura (métrica) sin abrir; ENTRAR / toque
// normal abre la WebApp en el resumen.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const destino = (e.notification.data && e.notification.data.url) || '/resumen';

  if (e.action === 'gracias') {
    e.waitUntil(
      fetch('/api/alerta/vista', { method: 'POST', credentials: 'include' })
        .catch(() => {}));
    return;   // GRACIAS no abre la app
  }

  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ('focus' in w) { w.navigate && w.navigate(destino); return w.focus(); }
      }
      return clients.openWindow(destino);
    })
  );
});
