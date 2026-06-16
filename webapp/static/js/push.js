/* ═══════════════════════════════════════════════════════════════════
   push.js — alerta.pe (zAlerta-07 C.1)
   Suscripción a Web Push. NO pide permiso al cargar: ofrece un banner
   "Activar avisos"; el permiso se pide solo al tocar el botón.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;

  const banner = document.getElementById('push-banner');
  const DISMISS = 'alertape_push_dismiss';

  function urlB64ToUint8Array(base64) {
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const s = (base64 + pad).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(s);
    return Uint8Array.from(Array.from(raw, (c) => c.charCodeAt(0)));
  }

  async function subscribe() {
    const reg = await navigator.serviceWorker.ready;
    const res = await fetch('/push/clave-publica');
    const { public_key } = await res.json();
    if (!public_key) throw new Error('sin clave VAPID');
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(public_key),
      });
    }
    await fetch('/push/suscribir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });
  }

  async function activar() {
    try {
      const perm = await Notification.requestPermission();
      if (perm === 'granted') await subscribe();
    } catch (_) { /* sin romper la UI */ }
    if (banner) banner.hidden = true;
  }

  function maybeShow() {
    // Ya concedido: re-asegurar la suscripción en el servidor, sin banner.
    if (Notification.permission === 'granted') { subscribe().catch(() => {}); return; }
    if (Notification.permission === 'denied') return;
    if (localStorage.getItem(DISMISS)) return;
    if (banner) banner.hidden = false;
  }

  document.getElementById('push-activar')?.addEventListener('click', activar);
  document.getElementById('push-cerrar')?.addEventListener('click', () => {
    localStorage.setItem(DISMISS, '1');
    if (banner) banner.hidden = true;
  });

  window.addEventListener('load', maybeShow);
})();
