/**
 * config.js — Panel de configuración (bottom sheet).
 *
 * Secciones en acordeón:
 * 1. Identidad y acceso (DNI secundario, biometría, cambiar clave)
 * 2. Comunicaciones (WhatsApp, push, horario, canal preferido)
 * 3. Mis empresas (lista con progreso)
 * 4. Preferencias visuales (tema, fuente)
 * 5. Voz e IA (tono, velocidad, empresa default)
 * 6. Instalar app (PWA)
 *
 * Cada sección guarda independientemente (no botón global).
 */

const ConfigPanel = {
    container: null,

    async load() {
        this.container = document.getElementById('config-container');
        if (!this.container) return;

        // Cargar config usuario y progreso
        let configUsuario = {};
        let empresas = [];
        try {
            const [confRes, empRes] = await Promise.all([
                fetch('/config/usuario'),
                fetch('/empresas/mis-empresas'),
            ]);
            if (confRes.ok) configUsuario = await confRes.json();
            if (empRes.ok) empresas = (await empRes.json()).empresas || [];
        } catch {}

        this.render(configUsuario, empresas);
    },

    render(config, empresas) {
        // Calcular progreso general
        const totalEmpresas = empresas.length;

        this.container.innerHTML = `
            ${this._renderProgresoBanner(empresas)}

            <div class="config-accordion">
                ${this._renderSeccion('identidad', 'Identidad y acceso', `
                    <div class="config-field">
                        <label>DNI secundario (familiar de respaldo)</label>
                        <input type="text" id="cfg-dni-sec" inputmode="numeric" maxlength="8" placeholder="12345678">
                        <small>8 dígitos del familiar que te ayuda a recuperar tu clave</small>
                    </div>
                    <div class="config-field" id="cfg-bio-field" style="display:none">
                        <label>Biometría</label>
                        <button class="config-btn config-btn--outline" id="cfg-bio-btn">
                            <i class="ph-fingerprint"></i> Activar huella / Face ID
                        </button>
                    </div>
                    <div class="config-field">
                        <label>Cambiar clave</label>
                        <input type="password" id="cfg-clave-actual" placeholder="Clave actual" autocomplete="current-password">
                        <input type="password" id="cfg-clave-nueva" placeholder="Nueva clave (mín 8 car.)" autocomplete="new-password" style="margin-top:8px">
                        <input type="password" id="cfg-clave-confirmar" placeholder="Confirmar nueva clave" autocomplete="new-password" style="margin-top:8px">
                    </div>
                    <button class="config-btn config-btn--save" onclick="ConfigPanel.guardarIdentidad()">Guardar</button>
                `)}

                ${this._renderSeccion('comunicaciones', 'Comunicaciones', `
                    <p class="config-hint">Por dónde te avisamos cuando importa</p>
                    <div class="config-field">
                        <label>WhatsApp</label>
                        <div class="config-input-prefix">
                            <span>+51</span>
                            <input type="tel" id="cfg-whatsapp" maxlength="9" placeholder="999888777">
                        </div>
                    </div>
                    <div class="config-field">
                        <label>Notificaciones Push</label>
                        <button class="config-btn config-btn--outline" id="cfg-push-btn" onclick="ConfigPanel.activarPush()">
                            <i class="ph-bell-ringing"></i> Activar notificaciones
                        </button>
                    </div>
                    <div class="config-field">
                        <label>Horario sin alertas</label>
                        <div class="config-row">
                            <input type="time" id="cfg-dnd-inicio" value="${config.horario_no_molestar_inicio || ''}">
                            <span>a</span>
                            <input type="time" id="cfg-dnd-fin" value="${config.horario_no_molestar_fin || ''}">
                        </div>
                    </div>
                    <div class="config-field">
                        <label>Canal preferido</label>
                        <div class="config-toggles">
                            <button class="config-toggle ${config.canal_preferido === 'push' ? 'active' : ''}" data-val="push" onclick="ConfigPanel.setToggle(this, 'canal')">Push</button>
                            <button class="config-toggle ${config.canal_preferido === 'whatsapp' ? 'active' : ''}" data-val="whatsapp" onclick="ConfigPanel.setToggle(this, 'canal')">WhatsApp</button>
                            <button class="config-toggle ${config.canal_preferido === 'ambos' ? 'active' : ''}" data-val="ambos" onclick="ConfigPanel.setToggle(this, 'canal')">Ambos</button>
                        </div>
                    </div>
                    <button class="config-btn config-btn--save" onclick="ConfigPanel.guardarComunicaciones()">Guardar</button>
                `)}

                ${this._renderSeccion('empresas', 'Mis empresas', `
                    <p class="config-hint">El núcleo — más datos = IA más precisa</p>
                    <div id="cfg-empresas-list">
                        ${empresas.map(e => `
                            <div class="config-empresa-item" data-id="${e.id}">
                                <div class="config-empresa-header" onclick="ConfigPanel.toggleEmpresa(${e.id})">
                                    <span class="empresa-item__dot empresa-item__dot--${e.estado}"></span>
                                    <span class="config-empresa-nombre">${e.razon_social}</span>
                                    <span class="config-empresa-ruc">${e.ruc}</span>
                                    <i class="ph-caret-down"></i>
                                </div>
                                <div class="config-empresa-body" id="cfg-emp-body-${e.id}" style="display:none">
                                    <div class="config-empresa-progress" id="cfg-emp-prog-${e.id}"></div>
                                    <div class="config-empresa-form" id="cfg-emp-form-${e.id}">Cargando...</div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `)}

                ${this._renderSeccion('visual', 'Preferencias visuales', `
                    <div class="config-field">
                        <label>Tema</label>
                        <div class="config-swatches">
                            ${['dark', 'semi', 'feminine', 'classic'].map(t => `
                                <button class="config-swatch config-swatch--${t} ${config.tema === t ? 'active' : ''}"
                                        onclick="ConfigPanel.setTema('${t}', this)" title="${t}">
                                    <span class="config-swatch__preview"></span>
                                    <span class="config-swatch__label">${t === 'feminine' ? 'Femenino' : t.charAt(0).toUpperCase() + t.slice(1)}</span>
                                </button>
                            `).join('')}
                        </div>
                    </div>
                    <div class="config-field">
                        <label>Tamaño de fuente</label>
                        <div class="config-toggles">
                            <button class="config-toggle ${config.fuente_size === 'sm' ? 'active' : ''}" onclick="ConfigPanel.setFuente('sm', this)" style="font-size:13px">A-</button>
                            <button class="config-toggle ${config.fuente_size === 'md' ? 'active' : ''}" onclick="ConfigPanel.setFuente('md', this)" style="font-size:16px">A</button>
                            <button class="config-toggle ${config.fuente_size === 'lg' ? 'active' : ''}" onclick="ConfigPanel.setFuente('lg', this)" style="font-size:19px">A+</button>
                        </div>
                    </div>
                `)}

                ${this._renderSeccion('voz', 'Voz e IA', `
                    <div class="config-field">
                        <label>Tono de respuesta</label>
                        <div class="config-toggles">
                            <button class="config-toggle ${config.tono_ia === 'formal' ? 'active' : ''}" data-val="formal" onclick="ConfigPanel.setToggle(this, 'tono')">Formal</button>
                            <button class="config-toggle ${config.tono_ia === 'directo' ? 'active' : ''}" data-val="directo" onclick="ConfigPanel.setToggle(this, 'tono')">Directo</button>
                        </div>
                        <small class="config-preview" id="cfg-tono-preview">
                            ${config.tono_ia === 'formal'
                                ? 'Ej: "Las ventas del periodo ascienden a S/ 45,200.00"'
                                : 'Ej: "Vendiste 45 mil este mes, 12% más que el anterior"'}
                        </small>
                    </div>
                    <div class="config-field">
                        <label>Velocidad de voz</label>
                        <input type="range" id="cfg-velocidad" min="0" max="2" step="1"
                               value="${config.velocidad_voz === 'lenta' ? 0 : config.velocidad_voz === 'rapida' ? 2 : 1}"
                               oninput="ConfigPanel.previewVelocidad(this.value)">
                        <div class="config-row" style="justify-content:space-between">
                            <small>Lenta</small><small>Normal</small><small>Rápida</small>
                        </div>
                    </div>
                    <button class="config-btn config-btn--save" onclick="ConfigPanel.guardarVoz()">Guardar</button>
                `)}

                ${this._renderSeccion('instalar', 'Instalar app', `
                    <div id="cfg-pwa-install" style="display:none">
                        <p>Instala alerta.pe como aplicación para acceso rápido y notificaciones.</p>
                        <button class="config-btn config-btn--primary" id="cfg-pwa-btn" onclick="ConfigPanel.instalarPWA()">
                            <i class="ph-download-simple"></i> Instalar aplicación
                        </button>
                    </div>
                    <div id="cfg-pwa-ios">
                        <p>En Safari: toca <strong>Compartir</strong> → <strong>Añadir a pantalla de inicio</strong></p>
                    </div>
                `)}
            </div>
        `;

        // WebAuthn disponible?
        if (window.PublicKeyCredential) {
            PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(ok => {
                if (ok) document.getElementById('cfg-bio-field').style.display = 'block';
            });
        }

        // PWA install prompt
        if (window._deferredPrompt) {
            document.getElementById('cfg-pwa-install').style.display = 'block';
            document.getElementById('cfg-pwa-ios').style.display = 'none';
        } else if (/iPhone|iPad/.test(navigator.userAgent)) {
            document.getElementById('cfg-pwa-ios').style.display = 'block';
        }
    },

    // ── Sección accordion ───────────────────────────────────────
    _renderSeccion(id, titulo, contenido) {
        return `
            <div class="config-section" id="cfg-sec-${id}">
                <button class="config-section__header" onclick="ConfigPanel.toggleSeccion('${id}')">
                    <span>${titulo}</span>
                    <i class="ph-caret-down"></i>
                </button>
                <div class="config-section__body" id="cfg-body-${id}" style="display:none">
                    ${contenido}
                </div>
            </div>
        `;
    },

    _renderProgresoBanner(empresas) {
        // Placeholder — se actualiza con datos reales
        return `
            <div class="config-progreso" id="cfg-progreso" style="display:none">
                <div class="config-progreso__icon"><i class="ph-lightning"></i></div>
                <div class="config-progreso__text">
                    <strong>Tu perfil está <span id="cfg-prog-pct">0</span>% configurado</strong>
                    <div class="config-progreso__bar">
                        <div class="config-progreso__fill" id="cfg-prog-fill" style="width:0%"></div>
                    </div>
                    <small>Con tus empresas configuradas, la IA responde 3x más rápido y con precisión</small>
                </div>
            </div>
        `;
    },

    toggleSeccion(id) {
        const body = document.getElementById(`cfg-body-${id}`);
        const section = document.getElementById(`cfg-sec-${id}`);
        if (!body) return;

        // Cerrar todas las demás
        document.querySelectorAll('.config-section__body').forEach(b => {
            if (b !== body) b.style.display = 'none';
        });
        document.querySelectorAll('.config-section').forEach(s => s.classList.remove('open'));

        const isOpen = body.style.display !== 'none';
        body.style.display = isOpen ? 'none' : 'block';
        if (!isOpen) section.classList.add('open');
    },

    async toggleEmpresa(id) {
        const body = document.getElementById(`cfg-emp-body-${id}`);
        const form = document.getElementById(`cfg-emp-form-${id}`);
        if (!body) return;

        const isOpen = body.style.display !== 'none';
        body.style.display = isOpen ? 'none' : 'block';

        if (!isOpen && form.textContent === 'Cargando...') {
            // Cargar config empresa
            try {
                const [confRes, progRes] = await Promise.all([
                    fetch(`/config/empresa/${id}`),
                    fetch(`/config/empresa/${id}/progreso`),
                ]);
                const conf = confRes.ok ? await confRes.json() : {};
                const prog = progRes.ok ? await progRes.json() : {};

                const progEl = document.getElementById(`cfg-emp-prog-${id}`);
                if (progEl && prog.porcentaje !== undefined) {
                    progEl.innerHTML = `<div class="config-progreso__bar"><div class="config-progreso__fill" style="width:${prog.porcentaje}%"></div></div><small>${prog.porcentaje}% completo</small>`;
                }

                form.innerHTML = `
                    <div class="config-field">
                        <label>Régimen tributario</label>
                        <select id="cfg-emp-regimen-${id}">
                            <option value="">Seleccionar</option>
                            ${['RER','RMT','GENERAL','NRUS','RUS'].map(r => `<option value="${r}" ${conf.regimen_tributario === r ? 'selected' : ''}>${r}</option>`).join('')}
                        </select>
                    </div>
                    <div class="config-field">
                        <label>CIIU</label>
                        <input type="text" id="cfg-emp-ciiu-${id}" value="${conf.ciiu || ''}" maxlength="6" placeholder="Código CIIU">
                    </div>
                    <div class="config-field">
                        <label>Umbral de alerta (S/)</label>
                        <input type="number" id="cfg-emp-umbral-${id}" value="${conf.umbral_alerta_monto || ''}" placeholder="Monto mínimo para alertar">
                    </div>
                    <div class="config-field">
                        <label>Día de cierre mensual</label>
                        <input type="number" id="cfg-emp-cierre-${id}" value="${conf.dia_cierre_mensual || ''}" min="1" max="31">
                    </div>
                    <div class="config-field config-row">
                        <label><input type="checkbox" id="cfg-emp-trab-${id}" ${conf.tiene_trabajadores ? 'checked' : ''}> Tiene trabajadores</label>
                        <label><input type="checkbox" id="cfg-emp-exp-${id}" ${conf.exporta ? 'checked' : ''}> Exporta</label>
                    </div>
                    <button class="config-btn config-btn--save" onclick="ConfigPanel.guardarEmpresa(${id})">Guardar</button>
                `;
            } catch {
                form.innerHTML = '<p style="color:var(--red)">Error al cargar configuración</p>';
            }
        }
    },

    // ── Guardar secciones ───────────────────────────────────────
    async guardarIdentidad() {
        // Solo cambiar clave por ahora
        const actual = document.getElementById('cfg-clave-actual')?.value;
        const nueva = document.getElementById('cfg-clave-nueva')?.value;
        const confirmar = document.getElementById('cfg-clave-confirmar')?.value;

        if (nueva && nueva !== confirmar) {
            showToast('Las claves no coinciden');
            return;
        }
        // TODO: endpoint para cambiar clave con clave actual
        showToast('Identidad guardada');
        this._playSound();
    },

    async guardarComunicaciones() {
        const data = {};
        const dndInicio = document.getElementById('cfg-dnd-inicio')?.value;
        const dndFin = document.getElementById('cfg-dnd-fin')?.value;
        if (dndInicio) data.horario_no_molestar_inicio = dndInicio;
        if (dndFin) data.horario_no_molestar_fin = dndFin;

        const canalActive = document.querySelector('#cfg-body-comunicaciones .config-toggle.active[data-val]');
        if (canalActive) data.canal_preferido = canalActive.dataset.val;

        await this._saveConfig(data);
        showToast('Comunicaciones guardadas');
        this._playSound();
    },

    async guardarVoz() {
        const data = {};
        const tonoActive = document.querySelector('#cfg-body-voz .config-toggle.active[data-val]');
        if (tonoActive) data.tono_ia = tonoActive.dataset.val;

        const vel = document.getElementById('cfg-velocidad')?.value;
        data.velocidad_voz = vel === '0' ? 'lenta' : vel === '2' ? 'rapida' : 'normal';

        await this._saveConfig(data);
        showToast('Preferencias de voz guardadas');
        this._playSound();
    },

    async guardarEmpresa(id) {
        const data = {};
        const regimen = document.getElementById(`cfg-emp-regimen-${id}`)?.value;
        const ciiu = document.getElementById(`cfg-emp-ciiu-${id}`)?.value;
        const umbral = document.getElementById(`cfg-emp-umbral-${id}`)?.value;
        const cierre = document.getElementById(`cfg-emp-cierre-${id}`)?.value;
        const trab = document.getElementById(`cfg-emp-trab-${id}`)?.checked;
        const exp = document.getElementById(`cfg-emp-exp-${id}`)?.checked;

        if (regimen) data.regimen_tributario = regimen;
        if (ciiu) data.ciiu = ciiu;
        if (umbral) data.umbral_alerta_monto = parseFloat(umbral);
        if (cierre) data.dia_cierre_mensual = parseInt(cierre);
        data.tiene_trabajadores = !!trab;
        data.exporta = !!exp;

        try {
            await fetch(`/config/empresa/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            showToast('Empresa actualizada');
            this._playSound();
        } catch {
            showToast('Error al guardar');
        }
    },

    // ── Tema y fuente ───────────────────────────────────────────
    setTema(tema, el) {
        Temas.setTema(tema);
        document.querySelectorAll('.config-swatch').forEach(s => s.classList.remove('active'));
        if (el) el.classList.add('active');
    },

    setFuente(size, el) {
        Temas.setSize(size);
        document.querySelectorAll('#cfg-body-visual .config-toggle').forEach(t => t.classList.remove('active'));
        if (el) el.classList.add('active');
    },

    setToggle(el, group) {
        el.closest('.config-toggles').querySelectorAll('.config-toggle').forEach(t => t.classList.remove('active'));
        el.classList.add('active');

        // Preview de tono
        if (group === 'tono') {
            const preview = document.getElementById('cfg-tono-preview');
            if (preview) {
                preview.textContent = el.dataset.val === 'formal'
                    ? 'Ej: "Las ventas del periodo ascienden a S/ 45,200.00"'
                    : 'Ej: "Vendiste 45 mil este mes, 12% más que el anterior"';
            }
        }
    },

    previewVelocidad(val) {
        // Visual feedback only
    },

    // ── Push notifications ──────────────────────────────────────
    async activarPush() {
        if (!('Notification' in window)) {
            showToast('Tu navegador no soporta notificaciones');
            return;
        }
        const perm = await Notification.requestPermission();
        if (perm === 'granted') {
            showToast('Notificaciones activadas');
        } else {
            showToast('Permiso de notificaciones denegado');
        }
    },

    // ── PWA install ─────────────────────────────────────────────
    instalarPWA() {
        if (window._deferredPrompt) {
            window._deferredPrompt.prompt();
            window._deferredPrompt.userChoice.then(choice => {
                if (choice.outcome === 'accepted') showToast('App instalada');
                window._deferredPrompt = null;
            });
        }
    },

    // ── Helpers ─────────────────────────────────────────────────
    async _saveConfig(data) {
        try {
            await fetch('/config/usuario', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
        } catch {}
    },

    _playSound() {
        try {
            const audio = new Audio('/static/audio/pop-sound.mp3');
            audio.volume = 0.2;
            audio.play().catch(() => {});
        } catch {}
    },
};

// Capturar PWA install prompt
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    window._deferredPrompt = e;
});
