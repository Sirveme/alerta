// Manejo de login en cliente.
// El formulario de login usa POST normal (server-side redirect), así que este
// archivo queda como placeholder para mejoras futuras (validación, refresh
// automático del access token vía /auth/refresh, etc.).

// Refresh proactivo del access token antes de que expire (opcional).
async function refrescarToken() {
    try {
        await fetch('/auth/refresh', { method: 'POST' });
    } catch (e) {
        // silencioso: si falla, el siguiente request protegido redirige a login
    }
}
