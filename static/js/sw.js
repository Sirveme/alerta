// Service Worker - Alerta.pe Web Push
console.log('[SW] Cargado');

self.addEventListener('install', (event) => {
    console.log('[SW] Install');
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('[SW] Activate');
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
    console.log('[SW] Push recibido', event);
    
    let data = {};
    try {
        data = event.data.json();
    } catch (e) {
        data = {
            titulo: 'Alerta.pe',
            cuerpo: event.data ? event.data.text() : 'Notificación nueva'
        };
    }
    
    console.log('[SW] Payload:', data);
    
    const titulo = data.titulo || 'Alerta.pe';
    const opciones = {
        body: data.cuerpo || '',
        icon: data.icono || '/static/img/icon-192.png',
        badge: '/static/img/icon-192.png',
        data: { url: data.url || '/dashboard' },
        vibrate: [200, 100, 200],
        requireInteraction: false,
        tag: 'alertape-push'
    };
    
    event.waitUntil(
        self.registration.showNotification(titulo, opciones)
            .then(() => console.log('[SW] Notificación mostrada'))
            .catch(err => console.error('[SW] Error mostrando notificación', err))
    );
});

self.addEventListener('notificationclick', (event) => {
    console.log('[SW] Notification click', event);
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