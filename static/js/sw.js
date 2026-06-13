// Service Worker - alerta.pe Web Push
// v3.0 — 2026-06-12 — Reescrito para zClaude-13d (eliminar precache de archivos viejos)

console.log('[SW alerta.pe] Script cargado');

self.addEventListener('install', (event) => {
    console.log('[SW alerta.pe] Install');
    // skipWaiting para activar inmediatamente sin esperar a que se cierren todas las pestañas
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('[SW alerta.pe] Activate');
    event.waitUntil(
        Promise.all([
            // Limpiar caches viejos del proyecto anterior si existieran
            caches.keys().then((nombres) =>
                Promise.all(
                    nombres.map((n) => {
                        console.log('[SW alerta.pe] Borrando cache vieja:', n);
                        return caches.delete(n);
                    })
                )
            ),
            // Tomar control de todos los clientes inmediatamente
            self.clients.claim(),
        ])
    );
});

self.addEventListener('push', (event) => {
    console.log('[SW alerta.pe] Push recibido', event);

    let data = {};
    try {
        data = event.data.json();
    } catch (e) {
        data = {
            titulo: 'alerta.pe',
            cuerpo: event.data ? event.data.text() : 'Notificación nueva'
        };
    }

    console.log('[SW alerta.pe] Payload:', data);

    const titulo = data.titulo || 'alerta.pe';
    const opciones = {
        body: data.cuerpo || '',
        icon: data.icono || '/static/img/favicon.svg',
        badge: data.icono || '/static/img/favicon.svg',
        data: { url: data.url || '/dashboard' },
        vibrate: [200, 100, 200],
        requireInteraction: false,
        tag: 'alertape-push'
    };

    event.waitUntil(
        self.registration.showNotification(titulo, opciones)
            .then(() => console.log('[SW alerta.pe] Notificación mostrada'))
            .catch((err) => console.error('[SW alerta.pe] Error mostrando notificación', err))
    );
});

self.addEventListener('notificationclick', (event) => {
    console.log('[SW alerta.pe] Click en notificación');
    event.notification.close();
    const url = (event.notification.data && event.notification.data.url) || '/dashboard';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
            for (const win of wins) {
                if (win.url.includes(url) && 'focus' in win) {
                    return win.focus();
                }
            }
            return clients.openWindow(url);
        })
    );
});

self.addEventListener('notificationclose', (event) => {
    console.log('[SW alerta.pe] Notificación cerrada');
});
