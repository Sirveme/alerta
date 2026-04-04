/**
 * cabecera.js — Comportamiento de la cabecera adaptable.
 *
 * - Scroll down > 60px → colapsa (44px)
 * - Scroll up → expande (72px)
 * - Top de página → siempre expandido
 * - Hora actualizada cada minuto
 * - Carga empresas y selector
 * - Botón de voz con Web Speech API
 */

(function () {
    const cabecera = document.getElementById('cabecera');
    if (!cabecera) return;

    let lastScrollY = 0;
    const SCROLL_THRESHOLD = 60;

    // ── Scroll: colapsar/expandir ──────────────────────────────
    window.addEventListener('scroll', () => {
        const currentY = window.scrollY;

        if (currentY <= 10) {
            // Top de página → siempre expandido
            cabecera.classList.remove('cabecera--collapsed');
            cabecera.classList.add('cabecera--expanded');
        } else if (currentY > lastScrollY && currentY > SCROLL_THRESHOLD) {
            // Scroll down → colapsar
            cabecera.classList.remove('cabecera--expanded');
            cabecera.classList.add('cabecera--collapsed');
        } else if (currentY < lastScrollY) {
            // Scroll up → expandir
            cabecera.classList.remove('cabecera--collapsed');
            cabecera.classList.add('cabecera--expanded');
        }

        lastScrollY = currentY;
    }, { passive: true });

    // ── Hora ────────────────────────────────────────────────────
    const horaEl = document.getElementById('cabecera-hora');
    function actualizarHora() {
        if (!horaEl) return;
        const now = new Date();
        const h = now.getHours();
        const m = now.getMinutes().toString().padStart(2, '0');
        const ampm = h >= 12 ? 'pm' : 'am';
        const h12 = h % 12 || 12;
        horaEl.textContent = `${h12}:${m} ${ampm}`;
    }
    actualizarHora();
    setInterval(actualizarHora, 60000);

    // ── Empresas ────────────────────────────────────────────────
    const btnEmpresa = document.getElementById('btn-empresa');
    const sheetEmpresas = document.getElementById('sheet-empresas');
    const listaEmpresas = document.getElementById('lista-empresas');
    const buscarEmpresa = document.getElementById('buscar-empresa');
    const empresaNombre = document.getElementById('empresa-nombre');
    const empresaMeta = document.getElementById('empresa-meta');
    const empresaDot = document.getElementById('empresa-dot');

    let empresasData = [];

    async function cargarEmpresas() {
        try {
            const res = await fetch('/empresas/mis-empresas');
            if (!res.ok) return;
            const data = await res.json();
            empresasData = data.empresas || [];

            if (empresasData.length > 5 && buscarEmpresa) {
                buscarEmpresa.style.display = 'block';
            }

            // Obtener empresa activa del user data en localStorage
            const user = JSON.parse(localStorage.getItem('alerta_user') || '{}');
            const activa = empresasData.find(e => e.id === user.empresa_activa_id);
            if (activa) {
                actualizarEmpresaUI(activa);
            } else if (empresasData.length > 0) {
                actualizarEmpresaUI(empresasData[0]);
            }

            // Badge de alertas
            const totalAlertas = empresasData.reduce((sum, e) => sum + (e.alertas_activas || 0), 0);
            const badge = document.getElementById('alertas-badge');
            if (badge) {
                if (totalAlertas > 0) {
                    badge.textContent = totalAlertas;
                    badge.style.display = 'flex';
                } else {
                    badge.style.display = 'none';
                }
            }
        } catch {}
    }

    function actualizarEmpresaUI(empresa) {
        if (empresaNombre) empresaNombre.textContent = empresa.razon_social || empresa.nombre_comercial || 'Sin nombre';
        if (empresaMeta) empresaMeta.textContent = ''; // Se podría mostrar región, régimen, etc.
        if (empresaDot) {
            empresaDot.style.background =
                empresa.estado === 'alertas' ? 'var(--red)' :
                empresa.estado === 'pendientes' ? 'var(--gold)' : 'var(--green)';
        }
    }

    function renderListaEmpresas(filter = '') {
        if (!listaEmpresas) return;
        const filtered = filter
            ? empresasData.filter(e => e.razon_social.toLowerCase().includes(filter.toLowerCase()))
            : empresasData;

        const user = JSON.parse(localStorage.getItem('alerta_user') || '{}');

        listaEmpresas.innerHTML = filtered.map(e => `
            <div class="empresa-item ${e.id === user.empresa_activa_id ? 'active' : ''}"
                 data-id="${e.id}">
                <span class="empresa-item__dot empresa-item__dot--${e.estado}"></span>
                <div class="empresa-item__info">
                    <div class="empresa-item__nombre">${e.razon_social}</div>
                    <div class="empresa-item__ruc">RUC ${e.ruc}</div>
                </div>
            </div>
        `).join('');

        // Click handlers
        listaEmpresas.querySelectorAll('.empresa-item').forEach(item => {
            item.addEventListener('click', () => cambiarEmpresa(parseInt(item.dataset.id)));
        });
    }

    async function cambiarEmpresa(empresaId) {
        try {
            const res = await fetch('/auth/cambiar-empresa', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ empresa_id: empresaId }),
            });
            if (!res.ok) return;
            const data = await res.json();

            // Actualizar user data local
            const user = JSON.parse(localStorage.getItem('alerta_user') || '{}');
            user.empresa_activa_id = empresaId;
            localStorage.setItem('alerta_user', JSON.stringify(user));

            // Actualizar UI
            const empresa = empresasData.find(e => e.id === empresaId);
            if (empresa) actualizarEmpresaUI(empresa);

            // Cerrar sheet
            cerrarSheet(sheetEmpresas);

            // Recargar contenido (HTMX o reload parcial)
            showToast('Empresa cambiada: ' + (data.empresa_nombre || ''));

            // Recargar dashboard si estamos ahí
            if (window.location.pathname === '/dashboard') {
                window.location.reload();
            }
        } catch {}
    }

    // Abrir sheet empresas
    if (btnEmpresa && sheetEmpresas) {
        btnEmpresa.addEventListener('click', () => {
            renderListaEmpresas();
            abrirSheet(sheetEmpresas);
        });
    }

    if (buscarEmpresa) {
        buscarEmpresa.addEventListener('input', (e) => renderListaEmpresas(e.target.value));
    }

    // ── Menú usuario ────────────────────────────────────────────
    const btnUserMenu = document.getElementById('btn-user-menu');
    const sheetUserMenu = document.getElementById('sheet-user-menu');

    if (btnUserMenu && sheetUserMenu) {
        btnUserMenu.addEventListener('click', () => abrirSheet(sheetUserMenu));
    }

    // Logout
    const navLogout = document.getElementById('nav-logout');
    if (navLogout) {
        navLogout.addEventListener('click', async (e) => {
            e.preventDefault();
            await fetch('/auth/logout', { method: 'POST' });
            localStorage.removeItem('alerta_user');
            window.location.href = '/login';
        });
    }

    // Config
    const navConfig = document.getElementById('nav-configuracion');
    const btnConfig = document.getElementById('btn-config');
    const sheetConfig = document.getElementById('sheet-config');

    function abrirConfig() {
        cerrarSheet(sheetUserMenu);
        if (sheetConfig) {
            abrirSheet(sheetConfig);
            if (typeof ConfigPanel !== 'undefined') ConfigPanel.load();
        }
    }

    if (navConfig) navConfig.addEventListener('click', (e) => { e.preventDefault(); abrirConfig(); });
    if (btnConfig) btnConfig.addEventListener('click', abrirConfig);

    const btnCloseConfig = document.getElementById('btn-close-config');
    if (btnCloseConfig && sheetConfig) {
        btnCloseConfig.addEventListener('click', () => cerrarSheet(sheetConfig));
    }

    // ── Voz ─────────────────────────────────────────────────────
    const btnVoz = document.getElementById('btn-voz');
    const btnVozDash = document.getElementById('btn-voz-dash');

    function iniciarVoz() {
        if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
            showToast('Tu navegador no soporta reconocimiento de voz');
            return;
        }

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const recognition = new SpeechRecognition();
        recognition.lang = 'es-PE';
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;

        if (btnVoz) btnVoz.classList.add('listening');

        recognition.onresult = async (event) => {
            const transcript = event.results[0][0].transcript;
            if (btnVoz) btnVoz.classList.remove('listening');
            showToast('Procesando: "' + transcript + '"');

            // Aquí se enviaría a POST /api/voz/consulta cuando el endpoint exista
            // Por ahora, feedback visual
            try {
                const utterance = new SpeechSynthesisUtterance('Recibido: ' + transcript);
                utterance.lang = 'es-PE';
                utterance.rate = 1;
                speechSynthesis.speak(utterance);
            } catch {}
        };

        recognition.onerror = () => {
            if (btnVoz) btnVoz.classList.remove('listening');
        };

        recognition.onend = () => {
            if (btnVoz) btnVoz.classList.remove('listening');
        };

        recognition.start();
    }

    if (btnVoz) btnVoz.addEventListener('click', iniciarVoz);
    if (btnVozDash) btnVozDash.addEventListener('click', (e) => { e.preventDefault(); iniciarVoz(); });

    // ── Sheets helpers ──────────────────────────────────────────
    function abrirSheet(sheet) {
        if (!sheet) return;
        sheet.hidden = false;
        const backdrop = sheet.querySelector('.sheet__backdrop');
        if (backdrop) backdrop.addEventListener('click', () => cerrarSheet(sheet), { once: true });
    }

    function cerrarSheet(sheet) {
        if (!sheet) return;
        sheet.hidden = true;
    }

    // ── Toast ───────────────────────────────────────────────────
    window.showToast = function (msg, duration = 3000) {
        const toast = document.getElementById('toast');
        const toastMsg = document.getElementById('toast-msg');
        if (!toast || !toastMsg) return;
        toastMsg.textContent = msg;
        toast.hidden = false;
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => { toast.hidden = true; }, duration);
    };

    // ── Init ────────────────────────────────────────────────────
    cargarEmpresas();
})();
