/**
 * portal.js — Lógica del formulario del portal reenviame.pe.
 *
 * - Auto-completar nombre al ingresar RUC
 * - Drag & drop de archivos
 * - Detección de tipo XML/PDF
 * - Submit + mostrar resultado
 * - Copiar link personalizado
 */

(function () {
    // ── Auto-completar RUC ──────────────────────────────────────
    const rucInputs = document.querySelectorAll('[data-ruc-autocomplete]');
    rucInputs.forEach(input => {
        const targetId = input.dataset.rucAutocomplete;
        let timer = null;

        input.addEventListener('input', () => {
            clearTimeout(timer);
            const ruc = input.value.trim();
            const target = document.getElementById(targetId);
            if (target) target.textContent = '';

            if (ruc.length === 11 && /^\d+$/.test(ruc)) {
                timer = setTimeout(async () => {
                    try {
                        const res = await fetch(`/api/ruc/${ruc}`);
                        if (res.ok) {
                            const data = await res.json();
                            if (target && data.razon_social) {
                                target.textContent = data.razon_social;
                            }
                        }
                    } catch {}
                }, 300);
            }
        });
    });

    // ── Tipo de archivo ─────────────────────────────────────────
    const tipoBtns = document.querySelectorAll('.p-tipo-btn');
    const dropzone = document.getElementById('p-dropzone');
    const fileInput = document.getElementById('p-file-input');
    const datosForm = document.getElementById('p-datos-form');
    let tipoSeleccionado = 'xml';
    let archivoSeleccionado = null;

    tipoBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tipoBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            tipoSeleccionado = btn.dataset.tipo;

            if (tipoSeleccionado === 'datos') {
                if (dropzone) dropzone.style.display = 'none';
                if (datosForm) datosForm.style.display = 'block';
            } else {
                if (dropzone) dropzone.style.display = 'block';
                if (datosForm) datosForm.style.display = 'none';
            }
        });
    });

    // ── Drag & Drop ─────────────────────────────────────────────
    if (dropzone) {
        dropzone.addEventListener('click', () => fileInput && fileInput.click());
        dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        });
    }

    if (fileInput) {
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length) handleFile(fileInput.files[0]);
        });
    }

    function handleFile(file) {
        archivoSeleccionado = file;
        const nombre = file.name;
        const fileNameEl = document.getElementById('p-filename');
        if (fileNameEl) {
            fileNameEl.textContent = nombre;
            fileNameEl.classList.add('p-dropzone__file');
        }

        // Detectar tipo por extensión
        if (nombre.endsWith('.xml')) {
            tipoSeleccionado = 'xml';
        } else if (nombre.endsWith('.pdf')) {
            tipoSeleccionado = 'pdf';
        }
        tipoBtns.forEach(b => {
            b.classList.toggle('active', b.dataset.tipo === tipoSeleccionado);
        });
    }

    // ── Submit ──────────────────────────────────────────────────
    const form = document.getElementById('p-form');
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const rucEmisor = document.getElementById('p-ruc-emisor').value.trim();
            const rucReceptor = document.getElementById('p-ruc-receptor').value.trim();
            const emailNotif = document.getElementById('p-email')?.value.trim() || '';

            if (!rucEmisor || rucEmisor.length !== 11) {
                alert('Ingresa un RUC emisor válido (11 dígitos)');
                return;
            }
            if (!rucReceptor || rucReceptor.length !== 11) {
                alert('Ingresa un RUC receptor válido (11 dígitos)');
                return;
            }

            const spinner = document.getElementById('p-spinner');
            const resultado = document.getElementById('p-resultado');
            const formCard = document.querySelector('.p-form-card');

            if (spinner) spinner.classList.add('visible');
            if (resultado) resultado.classList.remove('visible');

            let url, body;

            if (tipoSeleccionado === 'datos') {
                // Ingreso manual
                url = '/enviar/datos';
                body = JSON.stringify({
                    ruc_emisor: rucEmisor,
                    ruc_receptor: rucReceptor,
                    tipo: document.getElementById('p-tipo-comp')?.value || 'factura',
                    serie: document.getElementById('p-serie')?.value || '',
                    correlativo: document.getElementById('p-correlativo')?.value || '',
                    fecha_emision: document.getElementById('p-fecha')?.value || null,
                    total: parseFloat(document.getElementById('p-total')?.value) || null,
                });
            } else {
                // Archivo XML o PDF
                if (!archivoSeleccionado) {
                    alert('Selecciona un archivo');
                    if (spinner) spinner.classList.remove('visible');
                    return;
                }
                url = tipoSeleccionado === 'xml' ? '/enviar/xml' : '/enviar/pdf';
                body = new FormData();
                body.append('ruc_emisor', rucEmisor);
                body.append('ruc_receptor', rucReceptor);
                body.append(tipoSeleccionado === 'xml' ? 'archivo_xml' : 'archivo_pdf', archivoSeleccionado);
                if (emailNotif) body.append('email_notif', emailNotif);
            }

            try {
                const fetchOpts = tipoSeleccionado === 'datos'
                    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }
                    : { method: 'POST', body };

                const res = await fetch(url, fetchOpts);
                const data = await res.json();

                if (spinner) spinner.classList.remove('visible');

                mostrarResultado(data);
            } catch (err) {
                if (spinner) spinner.classList.remove('visible');
                alert('Error de conexión. Intenta de nuevo.');
            }
        });
    }

    function mostrarResultado(data) {
        const resultado = document.getElementById('p-resultado');
        if (!resultado) return;

        const estado = data.estado_validacion || 'pendiente';
        const acuseUrl = data.url_acuse || '';

        let clase, icono, titulo, texto;
        if (estado === 'valido') {
            clase = 'p-resultado-card--valido';
            icono = '✅';
            titulo = 'Comprobante válido';
            texto = 'Verificado y aceptado por SUNAT. Tu acuse está listo.';
        } else if (estado === 'observado') {
            clase = 'p-resultado-card--observado';
            icono = '⚠️';
            titulo = 'Con observaciones';
            texto = 'El comprobante tiene observaciones menores. Revisa los detalles.';
        } else {
            clase = 'p-resultado-card--error';
            icono = '❌';
            titulo = 'Rechazado';
            texto = data.errores && data.errores.length
                ? data.errores.map(e => typeof e === 'string' ? e : e.descripcion || e.tipo).join('. ')
                : 'El comprobante no pudo ser validado.';
        }

        resultado.innerHTML = `
            <div class="p-resultado-card ${clase}">
                <div class="p-resultado__icon">${icono}</div>
                <div class="p-resultado__titulo">${titulo}</div>
                <div class="p-resultado__texto">${texto}</div>
                ${acuseUrl ? `
                <div class="p-resultado__acuse">
                    <span>Acuse de recepción generado</span>
                    <a href="${acuseUrl}" class="p-resultado__acuse-btn" target="_blank">Descargar PDF</a>
                </div>
                ` : ''}
            </div>
        `;
        resultado.classList.add('visible');
    }

    // ── Copiar link personalizado ───────────────────────────────
    const copyBtn = document.getElementById('p-copy-link');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            const ruc = document.getElementById('p-comprador-ruc')?.value || '';
            const link = `${window.location.origin}/?receptor=${ruc}`;
            navigator.clipboard.writeText(link).then(() => {
                copyBtn.textContent = '¡Copiado!';
                setTimeout(() => { copyBtn.textContent = 'Copiar mi link personalizado'; }, 2000);
            });
        });
    }
})();
