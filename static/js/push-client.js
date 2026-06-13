// Cliente Web Push para suscribirse al servicio

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; ++i) arr[i] = raw.charCodeAt(i);
    return arr;
}

async function suscribirsePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        alert('Tu navegador no soporta notificaciones push.');
        return false;
    }

    // Pedir permiso
    const permiso = await Notification.requestPermission();
    if (permiso !== 'granted') {
        alert('Permiso de notificaciones denegado.');
        return false;
    }

    // Registrar SW
    await navigator.serviceWorker.register('/sw.js', { scope: '/' });

    // IMPORTANTE: esperar a que el SW esté activo (no solo registrado)
    const reg = await navigator.serviceWorker.ready;

    // Obtener clave pública del backend
    const keyResp = await fetch('/push/vapid-public-key');
    const { public_key } = await keyResp.json();
    if (!public_key) {
        alert('VAPID no configurado en el servidor.');
        return false;
    }

    // IMPORTANTE: si ya existe una suscripción atada a OTRA applicationServerKey
    // (ej: VAPID regeneradas), el push service rechaza con 400. La quitamos
    // para crear una nueva atada a la clave actual.
    const existente = await reg.pushManager.getSubscription();
    if (existente) {
        await existente.unsubscribe();
    }

    // Suscribirse
    const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key),
    });

    // Enviar al backend
    const resp = await fetch('/push/suscribir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subscription.toJSON()),
    });

    return resp.ok;
}

async function enviarPushPrueba() {
    const btn = document.getElementById('btn-push-prueba');
    const txtOriginal = btn ? btn.textContent : '';
    
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Enviando...';
    }
    
    try {
        const resp = await fetch('/push/test', { method: 'POST' });
        if (!resp.ok) {
            alert('❌ Error al enviar push de prueba.');
            return;
        }
        const data = await resp.json();
        
        // Mostrar resultado en el div de status, no en alert
        const status = document.getElementById('push-status');
        if (data.enviadas > 0 && data.fallidas === 0) {
            // Éxito silencioso - solo mensaje suave en el div
            if (status) {
                status.innerHTML = '✓ Push enviado. Revisa la notificación del sistema.';
                status.style.color = '#66bb6a';
                // Limpiar mensaje después de 5 seg
                setTimeout(() => {
                    if (status) status.innerHTML = '';
                }, 5000);
            }
        } else {
            // Solo alertar si HUBO problemas
            alert(
                `⚠ Push con problemas:\n\n` +
                `Enviadas: ${data.enviadas}\n` +
                `Fallidas: ${data.fallidas}\n` +
                `Desactivadas: ${data.desactivadas}`
            );
        }
    } catch (err) {
        alert('❌ Error: ' + err.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = txtOriginal;
        }
    }
}