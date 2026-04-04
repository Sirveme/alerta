/**
 * auth.js — Login con DNI + clave, WebAuthn (biometría), logout.
 *
 * Decisiones:
 * - WebAuthn: se detecta si el dispositivo lo soporta y se muestra el botón.
 * - Sonido suave al login exitoso (successfinish-ui-sound.mp3).
 * - Errores se muestran inline sin alerts.
 */

(function () {
    const form = document.getElementById('login-form');
    const errorEl = document.getElementById('login-error');
    const btnBio = document.getElementById('login-bio');
    const btnLogin = document.getElementById('login-btn');
    const soundLogin = document.getElementById('sound-login');

    // Detectar WebAuthn
    if (window.PublicKeyCredential) {
        PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(available => {
            if (available && btnBio) btnBio.style.display = 'block';
        });
    }

    // Login con DNI + clave
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            errorEl.style.display = 'none';
            btnLogin.disabled = true;
            btnLogin.textContent = 'Verificando...';

            const dni = document.getElementById('dni').value;
            const clave = document.getElementById('clave').value;

            try {
                const res = await fetch('/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ dni, clave }),
                });

                const data = await res.json();

                if (!res.ok) {
                    errorEl.textContent = data.detail || 'Error de autenticación';
                    errorEl.style.display = 'block';
                    btnLogin.disabled = false;
                    btnLogin.textContent = 'Ingresar';
                    return;
                }

                // Sonido de éxito
                if (soundLogin) {
                    soundLogin.volume = 0.3;
                    soundLogin.play().catch(() => {});
                }

                // Redirigir al dashboard
                if (data.ok) {
                    setTimeout(() => {
                        window.location.href = data.redirect || '/dashboard';
                    }, 400);
                }

            } catch {
                errorEl.textContent = 'Error de conexión';
                errorEl.style.display = 'block';
                btnLogin.disabled = false;
                btnLogin.textContent = 'Ingresar';
            }
        });
    }

    // Login con biometría (WebAuthn)
    if (btnBio) {
        btnBio.addEventListener('click', async () => {
            errorEl.style.display = 'none';
            const dni = document.getElementById('dni').value;
            if (!dni || dni.length !== 8) {
                errorEl.textContent = 'Ingresa tu DNI primero para usar biometría';
                errorEl.style.display = 'block';
                return;
            }

            try {
                // 1. Obtener challenge
                const beginRes = await fetch('/auth/webauthn/login/begin', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ dni }),
                });

                if (!beginRes.ok) {
                    const err = await beginRes.json();
                    errorEl.textContent = err.detail || 'Biometría no disponible';
                    errorEl.style.display = 'block';
                    return;
                }

                const options = await beginRes.json();

                // 2. Convertir base64url a ArrayBuffer
                const challenge = base64urlToBuffer(options.challenge);
                const allowCreds = (options.allowCredentials || []).map(c => ({
                    id: base64urlToBuffer(c.id),
                    type: c.type,
                }));

                // 3. Solicitar credencial al navegador
                const credential = await navigator.credentials.get({
                    publicKey: {
                        challenge,
                        rpId: options.rpId,
                        allowCredentials: allowCreds,
                        timeout: options.timeout || 60000,
                    },
                });

                // 4. Enviar respuesta al server
                const finishRes = await fetch('/auth/webauthn/login/finish', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id: bufferToBase64url(credential.rawId),
                        rawId: bufferToBase64url(credential.rawId),
                        response: {
                            authenticatorData: bufferToBase64url(credential.response.authenticatorData),
                            clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
                            signature: bufferToBase64url(credential.response.signature),
                        },
                        type: credential.type,
                        user_id: options.user_id,
                    }),
                });

                if (!finishRes.ok) {
                    const err = await finishRes.json();
                    errorEl.textContent = err.detail || 'Error de verificación biométrica';
                    errorEl.style.display = 'block';
                    return;
                }

                if (soundLogin) {
                    soundLogin.volume = 0.3;
                    soundLogin.play().catch(() => {});
                }
                setTimeout(() => window.location.href = '/dashboard', 400);

            } catch (err) {
                errorEl.textContent = 'Biometría cancelada o no disponible';
                errorEl.style.display = 'block';
            }
        });
    }

    // Utilidades base64url <-> ArrayBuffer
    function base64urlToBuffer(base64url) {
        const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
        const pad = base64.length % 4 === 0 ? '' : '='.repeat(4 - (base64.length % 4));
        const binary = atob(base64 + pad);
        const buffer = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) buffer[i] = binary.charCodeAt(i);
        return buffer.buffer;
    }

    function bufferToBase64url(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }
})();
