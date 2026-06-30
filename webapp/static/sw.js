/* ═══════════════════════════════════════════════════════════════════
   sw.js — Service Worker alerta.pe (zAlerta-01 B.7)
   Offline-first para la UI: cachea assets estáticos. Las páginas HTML
   usan network-first (datos frescos), con fallback a caché si no hay red.
   Push: handler listo; el ENVÍO real es una fase aparte.
   ═══════════════════════════════════════════════════════════════════ */

const CACHE = 'alertape-v34';
// Nunca cachear video ni respuestas parciales (Range/206): Cache Storage no
// admite 206 y lanzaría en cache.put (zAlerta-31 TEMA A).
const RE_VIDEO = /\.(mp4|webm|mov|m4v)(\?|$)/i;
const ASSETS = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/tema.js',
  '/static/js/ia.js',
  '/static/js/push.js',
  '/static/js/dock.js',
  '/static/js/onboarding.js',
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

  // Video o petición con Range → directo a la red, SIN cachear (evita el error
  // 'put' sobre 206 Partial Content y no llena el caché con binarios grandes).
  if (url.pathname.startsWith('/static/vid/') || RE_VIDEO.test(url.pathname)
      || req.headers.has('range')) {
    e.respondWith(fetch(req));
    return;
  }

  // Assets estáticos: cache-first
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        // Solo cachear respuestas COMPLETAS y OK (nunca 206/opaque/error).
        // Cualquier fallo de caché jamás debe romper la respuesta al usuario.
        if (res && res.status === 200 && res.type === 'basic') {
          const copia = res.clone();
          caches.open(CACHE)
            .then((c) => c.put(req, copia))
            .catch(() => {});
        }
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

// ── Push (zAlerta-17): aviso enriquecido (icono + imagen leyenda + acciones) ──
self.addEventListener('push', (e) => {
  let data = { title: 'Novedades en tu Buzón SUNAT',
               body: 'Tienes novedades de SUNAT.', url: '/resumen?from=push' };
  try { if (e.data) data = e.data.json(); } catch (_) {}
  const opts = {
    body: data.body || '',
    icon: '/static/img/icono.svg',     // logo alerta.pe (colapsado)
    badge: '/static/img/icono.svg',
    data: { url: data.url || '/resumen?from=push' },
  };
  // Imagen grande FIJA = leyenda de colores del semáforo (la sirve el backend).
  // Si el archivo no existe, el navegador simplemente la ignora (degrada bien).
  if (data.image) opts.image = data.image;
  // Acciones GRACIAS / RESUMEN (si el navegador las soporta). Si no, al tocar
  // se abre /resumen (equivale a RESUMEN).
  if (data.acciones) {
    opts.actions = [
      { action: 'gracias', title: 'GRACIAS' },
      { action: 'resumen', title: 'RESUMEN' },
    ];
  }
  e.waitUntil(self.registration.showNotification(
    data.title || 'Novedades en tu Buzón SUNAT', opts));
});

// GRACIAS → registra la lectura (métrica) sin abrir. RESUMEN / toque → abre
// /resumen (con ?from=push para el splash de bienvenida).
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const destino = (e.notification.data && e.notification.data.url) || '/resumen?from=push';

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
