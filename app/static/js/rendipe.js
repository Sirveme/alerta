/**
 * rendipe.js — Logica del modulo RendiPe (rendicion de viaticos)
 *
 * Vanilla JS module. Maneja:
 * - Calculo automatico de dias y total de viaticos
 * - Captura de foto (camara del dispositivo)
 * - Generacion de informe con IA
 * - Autocomplete de servidor por DNI
 * - Navegacion del wizard de pasos
 */

const rendipe = (() => {
    'use strict';

    // ── CALCULO DE DIAS ──────────────────────────────────────
    function calcularDias() {
        const inicio = document.getElementById('fecha_inicio');
        const fin = document.getElementById('fecha_fin');
        const diasEl = document.getElementById('dias_comision');
        if (!inicio || !fin || !diasEl) return 0;

        const d1 = new Date(inicio.value);
        const d2 = new Date(fin.value);

        if (isNaN(d1.getTime()) || isNaN(d2.getTime()) || d2 < d1) {
            diasEl.value = '';
            return 0;
        }

        const diff = Math.ceil((d2 - d1) / (1000 * 60 * 60 * 24)) + 1;
        diasEl.value = diff;
        calcularTotal();
        return diff;
    }

    // ── CALCULO DE TOTAL VIATICOS ────────────────────────────
    function calcularTotal() {
        const porDia = parseFloat(document.getElementById('viaticos_por_dia')?.value) || 0;
        const dias = parseInt(document.getElementById('dias_comision')?.value) || 0;
        const totalEl = document.getElementById('total_viaticos');

        if (!totalEl) return 0;

        const total = porDia * dias;
        totalEl.value = total.toFixed(2);

        // Update display if present
        const displayEl = document.getElementById('total_viaticos_display');
        if (displayEl) {
            displayEl.textContent = 'S/ ' + total.toLocaleString('es-PE', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });
        }

        return total;
    }

    // ── CAPTURA DE FOTO ──────────────────────────────────────
    function tomarFoto(comisionId) {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.capture = 'environment';

        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('foto', file);

            // Show loading state
            const btn = document.getElementById('btn-camara');
            if (btn) {
                btn.disabled = true;
                btn.querySelector('i')?.classList.replace('ph-camera', 'ph-spinner');
            }

            try {
                const res = await fetch(`/rendipe/comisiones/${comisionId}/gastos/foto`, {
                    method: 'POST',
                    body: formData
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    showToast(err.detail || 'Error al subir la foto');
                    return;
                }

                const data = await res.json();
                showToast('Gasto registrado correctamente');

                // Reload expense list
                if (typeof htmx !== 'undefined') {
                    const lista = document.getElementById('gastos-lista');
                    if (lista) htmx.trigger(lista, 'refresh');
                }

                // Refresh saldo display
                actualizarSaldo(comisionId);

            } catch {
                showToast('Error de conexion');
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.querySelector('i')?.classList.replace('ph-spinner', 'ph-camera');
                }
            }
        });

        input.click();
    }

    // ── GENERAR INFORME CON IA ───────────────────────────────
    async function generarInformeIA(comisionId) {
        const container = document.getElementById('informe-container');
        const btn = document.getElementById('btn-generar-ia');

        if (!container) return;

        // Show loading
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Generando...';
        }

        const loading = document.createElement('div');
        loading.className = 'rendipe-ia-loading';
        loading.id = 'ia-loading';
        loading.innerHTML = '<div class="rendipe-ia-loading__dots"><span></span><span></span><span></span></div> Generando borrador con IA...';
        container.prepend(loading);

        try {
            const res = await fetch(`/rendipe/comisiones/${comisionId}/informe/generar`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || 'Error al generar informe');
                return;
            }

            const data = await res.json();

            // Fill in the textarea fields
            const campos = ['antecedentes', 'objetivos', 'actividades', 'resultados', 'conclusiones', 'recomendaciones'];
            campos.forEach(campo => {
                const el = document.getElementById(`informe_${campo}`);
                if (el && data[campo]) {
                    el.value = data[campo];
                }
            });

            showToast('Borrador generado');

        } catch {
            showToast('Error de conexion al generar informe');
        } finally {
            const loadingEl = document.getElementById('ia-loading');
            if (loadingEl) loadingEl.remove();
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Generar borrador con IA';
            }
        }
    }

    // ── AUTOCOMPLETE SERVIDOR POR DNI ────────────────────────
    function initAutocompleteDNI() {
        const input = document.getElementById('servidor_dni');
        const list = document.getElementById('servidor-autocomplete-list');
        const nombreEl = document.getElementById('servidor_nombre');

        if (!input || !list) return;

        let debounceTimer = null;

        input.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            const dni = input.value.trim();

            if (dni.length < 4) {
                list.classList.remove('rendipe-autocomplete__list--open');
                return;
            }

            debounceTimer = setTimeout(async () => {
                try {
                    const res = await fetch(`/rendipe/servidores/?dni=${encodeURIComponent(dni)}`);
                    if (!res.ok) return;
                    const data = await res.json();

                    if (!data.items || data.items.length === 0) {
                        list.classList.remove('rendipe-autocomplete__list--open');
                        return;
                    }

                    list.innerHTML = data.items.map(s => `
                        <div class="rendipe-autocomplete__item"
                             data-dni="${s.dni}"
                             data-nombre="${s.nombres} ${s.apellidos}"
                             data-cargo="${s.cargo || ''}"
                             data-id="${s.id}">
                            <strong>${s.dni}</strong> — ${s.nombres} ${s.apellidos}
                        </div>
                    `).join('');
                    list.classList.add('rendipe-autocomplete__list--open');
                } catch {
                    list.classList.remove('rendipe-autocomplete__list--open');
                }
            }, 300);
        });

        list.addEventListener('click', (e) => {
            const item = e.target.closest('.rendipe-autocomplete__item');
            if (!item) return;

            input.value = item.dataset.dni;
            if (nombreEl) nombreEl.value = item.dataset.nombre;

            const cargoEl = document.getElementById('servidor_cargo');
            if (cargoEl && item.dataset.cargo) cargoEl.value = item.dataset.cargo;

            const idEl = document.getElementById('servidor_id');
            if (idEl) idEl.value = item.dataset.id;

            list.classList.remove('rendipe-autocomplete__list--open');
        });

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.rendipe-autocomplete')) {
                list.classList.remove('rendipe-autocomplete__list--open');
            }
        });
    }

    // ── STEP FORM NAVIGATION ─────────────────────────────────
    let currentStep = 1;
    const totalSteps = 5;

    function initWizard() {
        currentStep = 1;
        updateStepUI();

        // Bind date inputs for auto-calculation
        const fechaInicio = document.getElementById('fecha_inicio');
        const fechaFin = document.getElementById('fecha_fin');
        const viaticoPorDia = document.getElementById('viaticos_por_dia');

        if (fechaInicio) fechaInicio.addEventListener('change', calcularDias);
        if (fechaFin) fechaFin.addEventListener('change', calcularDias);
        if (viaticoPorDia) viaticoPorDia.addEventListener('input', calcularTotal);

        initAutocompleteDNI();
    }

    function nextStep() {
        if (currentStep >= totalSteps) return;

        // Validate current step
        if (!validateStep(currentStep)) return;

        currentStep++;
        updateStepUI();

        // If going to summary step, populate it
        if (currentStep === totalSteps) {
            populateSummary();
        }
    }

    function prevStep() {
        if (currentStep <= 1) return;
        currentStep--;
        updateStepUI();
    }

    function goToStep(step) {
        if (step < 1 || step > totalSteps) return;
        currentStep = step;
        updateStepUI();
    }

    function updateStepUI() {
        // Update circles
        for (let i = 1; i <= totalSteps; i++) {
            const circle = document.getElementById(`step-circle-${i}`);
            const line = document.getElementById(`step-line-${i}`);
            const panel = document.getElementById(`step-panel-${i}`);

            if (circle) {
                circle.classList.remove('step-indicator__circle--active', 'step-indicator__circle--done');
                if (i === currentStep) {
                    circle.classList.add('step-indicator__circle--active');
                } else if (i < currentStep) {
                    circle.classList.add('step-indicator__circle--done');
                    circle.innerHTML = '<i class="ph-check"></i>';
                } else {
                    circle.textContent = i;
                }
            }

            if (line) {
                line.classList.toggle('step-indicator__line--done', i < currentStep);
            }

            if (panel) {
                panel.classList.toggle('step-panel--active', i === currentStep);
            }
        }
    }

    function validateStep(step) {
        const panel = document.getElementById(`step-panel-${step}`);
        if (!panel) return true;

        const required = panel.querySelectorAll('[required]');
        let valid = true;

        required.forEach(el => {
            if (!el.value.trim()) {
                el.style.borderColor = 'var(--red)';
                valid = false;
                setTimeout(() => { el.style.borderColor = ''; }, 2000);
            }
        });

        if (!valid && typeof showToast === 'function') {
            showToast('Completa los campos obligatorios');
        }

        return valid;
    }

    function populateSummary() {
        const fields = {
            'Servidor': document.getElementById('servidor_nombre')?.value || document.getElementById('servidor_dni')?.value || '',
            'DNI': document.getElementById('servidor_dni')?.value || '',
            'Destino': document.getElementById('destino')?.value || '',
            'Fecha inicio': document.getElementById('fecha_inicio')?.value || '',
            'Fecha fin': document.getElementById('fecha_fin')?.value || '',
            'Dias': document.getElementById('dias_comision')?.value || '',
            'Viatico por dia': 'S/ ' + (document.getElementById('viaticos_por_dia')?.value || '0'),
            'Total viaticos': 'S/ ' + (document.getElementById('total_viaticos')?.value || '0.00'),
            'N. Resolucion': document.getElementById('numero_resolucion')?.value || '',
        };

        const container = document.getElementById('step-summary');
        if (!container) return;

        container.innerHTML = Object.entries(fields).map(([key, val]) => `
            <div class="step-summary__row">
                <span class="step-summary__key">${key}</span>
                <span class="step-summary__val">${val}</span>
            </div>
        `).join('');
    }

    // ── ACTUALIZAR SALDO ─────────────────────────────────────
    async function actualizarSaldo(comisionId) {
        try {
            const res = await fetch(`/rendipe/comisiones/${comisionId}/saldo`);
            if (!res.ok) return;
            const data = await res.json();

            const saldoEl = document.getElementById('saldo-disponible');
            const barFill = document.getElementById('saldo-bar-fill');
            const gastadoEl = document.getElementById('saldo-gastado');
            const asignadoEl = document.getElementById('saldo-asignado');

            if (saldoEl) {
                saldoEl.textContent = 'S/ ' + Number(data.saldo_disponible).toLocaleString('es-PE', { minimumFractionDigits: 2 });

                saldoEl.classList.remove('campo-header__saldo--warning', 'campo-header__saldo--danger');
                const pct = data.asignado > 0 ? (data.gastado / data.asignado) * 100 : 0;
                if (pct >= 90) saldoEl.classList.add('campo-header__saldo--danger');
                else if (pct >= 70) saldoEl.classList.add('campo-header__saldo--warning');
            }

            if (barFill) {
                const pct = data.asignado > 0 ? Math.min((data.gastado / data.asignado) * 100, 100) : 0;
                barFill.style.width = pct + '%';
                barFill.classList.remove('saldo-bar__fill--warning', 'saldo-bar__fill--danger');
                if (pct >= 90) barFill.classList.add('saldo-bar__fill--danger');
                else if (pct >= 70) barFill.classList.add('saldo-bar__fill--warning');
            }

            if (gastadoEl) gastadoEl.textContent = 'S/ ' + Number(data.gastado).toLocaleString('es-PE', { minimumFractionDigits: 2 });
            if (asignadoEl) asignadoEl.textContent = 'S/ ' + Number(data.asignado).toLocaleString('es-PE', { minimumFractionDigits: 2 });

        } catch { /* silently fail */ }
    }

    // ── TOAST HELPER ─────────────────────────────────────────
    function showToast(msg) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg);
            return;
        }
        const toast = document.getElementById('toast');
        const toastMsg = document.getElementById('toast-msg');
        if (!toast || !toastMsg) return;
        toastMsg.textContent = msg;
        toast.hidden = false;
        setTimeout(() => { toast.hidden = true; }, 3000);
    }

    // ── SUBMIT COMISION FORM ─────────────────────────────────
    async function submitComision(e) {
        if (e) e.preventDefault();

        const form = document.getElementById('comision-form');
        if (!form) return;

        const formData = new FormData(form);
        const payload = Object.fromEntries(formData.entries());

        const btn = form.querySelector('[type="submit"]');
        if (btn) { btn.disabled = true; btn.textContent = 'Guardando...'; }

        try {
            const res = await fetch('/rendipe/comisiones/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || 'Error al crear comision');
                return;
            }

            const data = await res.json();
            showToast('Comision creada exitosamente');
            window.location.href = `/rendipe/comisiones/${data.id}`;

        } catch {
            showToast('Error de conexion');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Confirmar y crear'; }
        }
    }

    // ── SUBMIT INFORME ───────────────────────────────────────
    async function guardarInforme(comisionId) {
        const campos = ['antecedentes', 'objetivos', 'actividades', 'resultados', 'conclusiones', 'recomendaciones'];
        const payload = {};
        campos.forEach(c => {
            const el = document.getElementById(`informe_${c}`);
            if (el) payload[c] = el.value;
        });

        try {
            const res = await fetch(`/rendipe/comisiones/${comisionId}/informe`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                showToast('Informe guardado');
            } else {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || 'Error al guardar');
            }
        } catch {
            showToast('Error de conexion');
        }
    }

    // ── SESION 8: GPS, MAPA, DJ, EXTERIOR, SELFIE ─────────────

    /**
     * Obtiene la posicion GPS actual del dispositivo.
     * Retorna Promise<{lat, lon}>.
     */
    function obtenerGPS() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) {
                reject(new Error('Geolocalizacion no disponible en este dispositivo'));
                return;
            }
            navigator.geolocation.getCurrentPosition(
                (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
                (err) => reject(new Error('No se pudo obtener la ubicacion: ' + err.message)),
                { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
            );
        });
    }

    /**
     * Geocodifica el texto del campo lugar_especifico usando Nominatim (OSM).
     * Muestra el resultado en el mapa Leaflet.
     */
    async function geocodificarLugar() {
        const lugarInput = document.getElementById('lugar_especifico');
        const latInput = document.getElementById('lugar_latitud');
        const lonInput = document.getElementById('lugar_longitud');
        const mapaDiv = document.getElementById('mapa-lugar');

        if (!lugarInput || !lugarInput.value.trim()) {
            showToast('Ingresa el lugar especifico primero');
            return;
        }

        const query = lugarInput.value.trim();

        try {
            const res = await fetch(
                `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=1`,
                { headers: { 'Accept-Language': 'es' } }
            );
            const data = await res.json();

            if (!data || data.length === 0) {
                showToast('No se encontro el lugar. Intenta con mas detalle.');
                return;
            }

            const lat = parseFloat(data[0].lat);
            const lon = parseFloat(data[0].lon);

            if (latInput) latInput.value = lat.toFixed(7);
            if (lonInput) lonInput.value = lon.toFixed(7);

            // Mostrar mapa
            if (mapaDiv) {
                mapaDiv.style.display = 'block';
                inicializarMapaComision(lat, lon);
            }

            showToast('Lugar encontrado: ' + data[0].display_name.substring(0, 60));
        } catch {
            showToast('Error al buscar el lugar');
        }
    }

    /**
     * Inicializa o actualiza el mapa Leaflet en el formulario de comision.
     */
    let _mapaComision = null;
    let _mapaMarker = null;
    let _mapaCircle = null;

    function inicializarMapaComision(lat, lon) {
        const mapaDiv = document.getElementById('mapa-lugar');
        if (!mapaDiv) return;

        const radio = parseInt(document.getElementById('lugar_radio_metros')?.value) || 300;

        if (_mapaComision) {
            _mapaComision.setView([lat, lon], 15);
            if (_mapaMarker) _mapaMarker.setLatLng([lat, lon]);
            else _mapaMarker = L.marker([lat, lon]).addTo(_mapaComision);
            if (_mapaCircle) { _mapaCircle.setLatLng([lat, lon]); _mapaCircle.setRadius(radio); }
            else _mapaCircle = L.circle([lat, lon], { radius: radio, color: '#3b82f6', fillOpacity: 0.15 }).addTo(_mapaComision);
            return;
        }

        _mapaComision = L.map(mapaDiv).setView([lat, lon], 15);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap'
        }).addTo(_mapaComision);

        _mapaMarker = L.marker([lat, lon], { draggable: true }).addTo(_mapaComision);
        _mapaCircle = L.circle([lat, lon], { radius: radio, color: '#3b82f6', fillOpacity: 0.15 }).addTo(_mapaComision);

        // Permitir mover el marcador para ajustar coordenadas
        _mapaMarker.on('dragend', () => {
            const pos = _mapaMarker.getLatLng();
            const latInput = document.getElementById('lugar_latitud');
            const lonInput = document.getElementById('lugar_longitud');
            if (latInput) latInput.value = pos.lat.toFixed(7);
            if (lonInput) lonInput.value = pos.lng.toFixed(7);
            if (_mapaCircle) _mapaCircle.setLatLng(pos);
        });
    }

    /**
     * Captura selfie y registra asistencia con GPS.
     * Usa el ultimo gasto pendiente de la comision o crea referencia al comision_id.
     */
    async function capturarSelfie(comisionId) {
        const resultDiv = document.getElementById('asistencia-resultado');
        const btn = document.getElementById('btn-selfie');

        // 1. Obtener GPS
        let coords;
        try {
            if (btn) { btn.disabled = true; btn.textContent = 'Obteniendo GPS...'; }
            coords = await obtenerGPS();
        } catch (e) {
            showToast(e.message);
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="ph-user-focus"></i> Selfie + GPS'; }
            return;
        }

        // 2. Capturar selfie
        if (btn) btn.textContent = 'Abriendo camara...';

        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.capture = 'user';

        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) {
                if (btn) { btn.disabled = false; btn.innerHTML = '<i class="ph-user-focus"></i> Selfie + GPS'; }
                return;
            }

            if (btn) btn.textContent = 'Registrando asistencia...';

            // Necesitamos un gasto_id. Obtener los gastos de hoy para tomar el primero pendiente
            try {
                const gastosRes = await fetch(`/rendipe/comisiones/${comisionId}/gastos`);
                const gastos = await gastosRes.json();
                const gastoPendiente = gastos.find(g => !g.asistencia_validada);

                if (!gastoPendiente) {
                    showToast('No hay gastos pendientes para marcar asistencia');
                    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="ph-user-focus"></i> Selfie + GPS'; }
                    return;
                }

                const formData = new FormData();
                formData.append('foto', file);

                const res = await fetch(
                    `/rendipe/gastos/${gastoPendiente.id}/asistencia?lat=${coords.lat}&lon=${coords.lon}`,
                    { method: 'POST', body: formData }
                );

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    showToast(err.detail || 'Error al registrar asistencia');
                    return;
                }

                const data = await res.json();

                // Mostrar resultado
                if (resultDiv) {
                    resultDiv.style.display = 'block';
                    const esValida = data.asistencia_validada;
                    resultDiv.style.background = esValida ? 'var(--green-bg, #dcfce7)' : 'var(--yellow-bg, #fef9c3)';
                    resultDiv.innerHTML = `
                        <strong>${esValida ? 'Asistencia validada' : 'Fuera de rango'}</strong><br>
                        Distancia: ${data.distancia_metros !== null ? data.distancia_metros + 'm' : 'N/D'}
                        ${data.radio_tolerancia ? ' (radio: ' + data.radio_tolerancia + 'm)' : ''}<br>
                        <span style="font-size:0.7rem;color:var(--text-3);">${data.mensaje || ''}</span>
                    `;
                }

                showToast(data.mensaje || 'Asistencia registrada');

            } catch {
                showToast('Error de conexion al registrar asistencia');
            } finally {
                if (btn) { btn.disabled = false; btn.innerHTML = '<i class="ph-user-focus"></i> Selfie + GPS'; }
            }
        });

        input.click();
    }

    /**
     * Guarda un gasto por Declaracion Jurada.
     */
    async function guardarDJ(comisionId) {
        const rubro = document.getElementById('dj_rubro')?.value;
        const monto = parseFloat(document.getElementById('dj_monto')?.value);
        const descripcion = document.getElementById('dj_descripcion')?.value;
        const establecimiento = document.getElementById('dj_establecimiento')?.value;
        const motivo = document.getElementById('dj_motivo')?.value;
        const fecha = document.getElementById('dj_fecha')?.value;

        if (!monto || monto <= 0) { showToast('Ingresa un monto valido'); return; }
        if (!descripcion) { showToast('Ingresa una descripcion'); return; }
        if (!fecha) { showToast('Selecciona la fecha del gasto'); return; }

        try {
            const res = await fetch(`/rendipe/comisiones/${comisionId}/gastos/dj`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    rubro: rubro,
                    monto: monto,
                    descripcion: descripcion,
                    establecimiento: establecimiento || '',
                    motivo_sin_ce: motivo,
                    fecha_gasto: fecha
                })
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || 'Error al guardar DJ');
                return;
            }

            const data = await res.json();

            // Mostrar advertencias si las hay
            if (data.advertencias && data.advertencias.length > 0) {
                showToast('DJ guardada con advertencias: ' + data.advertencias[0]);
            } else {
                showToast('Declaracion Jurada registrada');
            }

            // Cerrar modal y refrescar lista
            document.getElementById('modal-dj').style.display = 'none';

            // Limpiar campos
            document.getElementById('dj_monto').value = '';
            document.getElementById('dj_descripcion').value = '';
            document.getElementById('dj_establecimiento').value = '';

            // Refrescar lista de gastos
            if (typeof htmx !== 'undefined') {
                const lista = document.getElementById('gastos-lista');
                if (lista) htmx.trigger(lista, 'refresh');
            }
            actualizarSaldo(comisionId);

        } catch {
            showToast('Error de conexion');
        }
    }

    /**
     * Guarda un gasto en moneda extranjera.
     */
    async function guardarGastoExterior(comisionId) {
        const rubro = document.getElementById('ext_rubro')?.value;
        const monto = parseFloat(document.getElementById('ext_monto')?.value);
        const moneda = document.getElementById('ext_moneda')?.value;
        const tc = parseFloat(document.getElementById('ext_tc')?.value);
        const descripcion = document.getElementById('ext_descripcion')?.value;
        const establecimiento = document.getElementById('ext_establecimiento')?.value;
        const fecha = document.getElementById('ext_fecha')?.value;

        if (!monto || monto <= 0) { showToast('Ingresa un monto valido'); return; }
        if (!tc || tc <= 0) { showToast('Ingresa el tipo de cambio'); return; }
        if (!fecha) { showToast('Selecciona la fecha del gasto'); return; }

        try {
            const res = await fetch(`/rendipe/comisiones/${comisionId}/gastos/exterior`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    rubro: rubro,
                    monto_ext: monto,
                    moneda_ext: moneda,
                    descripcion: descripcion || '',
                    establecimiento: establecimiento || '',
                    fecha_gasto: fecha,
                    tipo_cambio: tc
                })
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showToast(err.detail || 'Error al guardar gasto exterior');
                return;
            }

            const data = await res.json();
            showToast(`Gasto exterior registrado: ${moneda} ${monto} = PEN ${data.monto_pen}`);

            // Cerrar modal y refrescar
            document.getElementById('modal-exterior').style.display = 'none';
            document.getElementById('ext_monto').value = '';
            document.getElementById('ext_descripcion').value = '';
            document.getElementById('ext_establecimiento').value = '';
            document.getElementById('ext_tc').value = '';

            if (typeof htmx !== 'undefined') {
                const lista = document.getElementById('gastos-lista');
                if (lista) htmx.trigger(lista, 'refresh');
            }
            actualizarSaldo(comisionId);

        } catch {
            showToast('Error de conexion');
        }
    }

    // ── PUBLIC API ────────────────────────────────────────────
    return {
        calcularDias,
        calcularTotal,
        tomarFoto,
        generarInformeIA,
        initWizard,
        nextStep,
        prevStep,
        goToStep,
        submitComision,
        guardarInforme,
        actualizarSaldo,
        initAutocompleteDNI,
        geocodificarLugar,
        inicializarMapaComision,
        capturarSelfie,
        guardarDJ,
        guardarGastoExterior,
        obtenerGPS,
        marcarAsistencia: capturarSelfie
    };
})();

// Auto-init wizard if form is present
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('step-panel-1')) {
        rendipe.initWizard();
    }
});
