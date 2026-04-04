/**
 * cards.js — Generador de tarjetas UI para alerta.pe
 *
 * Cada tarjeta recibe datos JSON y genera HTML estilizado.
 * Se insertan en el panel de respuesta de voz o en el dashboard.
 */

const CardTypes = {

    RESUMEN_MES: (d) => `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--blue"><i class="ph-chart-pie-slice"></i></div>
                <div><div class="card__title">Resumen ${d.mes || ''}/${d.anio || ''}</div></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
                <div><div class="card__monto card__monto--green">S/ ${fmt(d.cobrado)}</div><div class="card__subtitle">Cobrado</div></div>
                <div><div class="card__monto card__monto--gold">S/ ${fmt(d.pendiente)}</div><div class="card__subtitle">Pendiente</div></div>
                <div><div class="card__monto card__monto--red">${d.alertas || 0}</div><div class="card__subtitle">Alertas</div></div>
                <div><div class="card__monto">${d.comprobantes || 0}</div><div class="card__subtitle">Comprobantes</div></div>
            </div>
        </div>`,

    PAGO: (d) => `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--green"><i class="ph-${d.canal === 'yape' || d.canal === 'plin' ? 'lightning' : 'bank'}"></i></div>
                <div>
                    <div class="card__title">${d.canal ? d.canal.toUpperCase() : ''} — S/ ${fmt(d.monto)}</div>
                    <div class="card__subtitle">${d.fecha_pago || ''} · ${d.pagador_nombre || 'Sin nombre'}</div>
                </div>
            </div>
            <div class="card__body">
                <span class="card__semaforo card__semaforo--${d.estado === 'cruzado' ? 'valido' : d.estado === 'pendiente_cruce' ? 'observado' : 'bloqueado'}">
                    ${d.estado === 'cruzado' ? '✅ Cruzado' : d.estado === 'pendiente_cruce' ? '⚠️ Pendiente' : '🔴 ' + (d.estado || '').replace(/_/g,' ')}
                </span>
            </div>
            ${d.numero_operacion ? `<div class="card__subtitle">Op: ${d.numero_operacion}</div>` : ''}
        </div>`,

    COMPROBANTE: (d) => `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--blue"><i class="ph-file-text"></i></div>
                <div>
                    <div class="card__title">${(d.tipo||'').replace(/_/g,' ').toUpperCase()} ${d.serie}-${d.correlativo}</div>
                    <div class="card__subtitle">${d.ruc_emisor} · ${d.fecha_emision || ''}</div>
                </div>
            </div>
            <div class="card__body">
                <div class="card__monto">S/ ${fmt(d.total)}</div>
                ${d.estado_validacion ? `<span class="card__semaforo card__semaforo--${d.estado_validacion}">${d.estado_validacion}</span>` : ''}
            </div>
            <div class="card__actions">
                <button class="card__btn" onclick="window.open('/comprobantes/${d.id}/xml')">XML</button>
                <button class="card__btn" onclick="window.open('/comprobantes/${d.id}/pdf')">PDF</button>
                <button class="card__btn" onclick="compartirCard('comprobante',${JSON.stringify(d).replace(/'/g,'\\\'')})">Compartir</button>
            </div>
        </div>`,

    ALERTA: (d) => {
        const colorMap = { urgente: 'red', importante: 'gold', info: 'blue' };
        const color = colorMap[d.nivel] || 'blue';
        return `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--${color}"><i class="ph-bell-ringing"></i></div>
                <div>
                    <div class="card__title">${d.titulo}</div>
                    <div class="card__subtitle">${d.origen || 'sistema'} · ${d.created_at || ''}</div>
                </div>
            </div>
            <div class="card__body"><div class="card__text">${d.descripcion || d.mensaje || ''}</div></div>
            <div class="card__actions">
                <button class="card__btn" onclick="fetch('/alertas/${d.id}/leer',{method:'PUT'})">Marcar leída</button>
                <button class="card__btn" onclick="compartirCard('alerta',${JSON.stringify(d).replace(/'/g,'\\\'')})">Compartir</button>
            </div>
        </div>`;
    },

    COMPROBANTE_ERROR: (d) => `
        <div class="card card--error">
            <div class="card__header">
                <div class="card__icon card__icon--red"><i class="ph-warning-circle"></i></div>
                <div>
                    <div class="card__title">Comprobante con errores</div>
                    <div class="card__subtitle">${d.serie}-${d.correlativo} · NO usar tributariamente</div>
                </div>
            </div>
            <div class="card__body card__errores">
                ${(d.errores || []).map(e => `
                    <div class="card__error-item">
                        <span class="card__error-campo">${e.campo}</span>
                        <span class="card__error-desc">${e.descripcion}</span>
                    </div>
                `).join('')}
            </div>
            <div class="card__actions">
                <button class="card__btn card__btn--primary" onclick="iniciarCorreccion(${d.comprobante_id})">Solicitar corrección</button>
                <button class="card__btn">Registrar NC</button>
            </div>
        </div>`,

    ASIENTO_CONTABLE: (d) => `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--blue"><i class="ph-book-open"></i></div>
                <div>
                    <div class="card__title">Asiento #${d.numero_asiento || ''}</div>
                    <div class="card__subtitle">${d.fecha || ''} · ${d.glosa || ''}</div>
                </div>
            </div>
            <div class="card__body">
                <table class="card__table">
                    <thead><tr><th>Cuenta</th><th>Denominación</th><th>Debe</th><th>Haber</th></tr></thead>
                    <tbody>
                        ${(d.lineas || []).map(l => `
                            <tr>
                                <td>${l.cuenta_codigo}</td>
                                <td>${l.denominacion}</td>
                                <td class="num">${l.debe > 0 ? fmt(l.debe) : ''}</td>
                                <td class="num">${l.haber > 0 ? fmt(l.haber) : ''}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
                <div class="card__asiento-check ${d.cuadra ? 'ok' : 'error'}">
                    ${d.cuadra ? '✅ Debe = Haber' : '❌ Descuadre detectado'}
                </div>
            </div>
            <div class="card__actions">
                <button class="card__btn" onclick="window.open('/asientos/${d.id}/exportar-ple')">Exportar PLE</button>
            </div>
        </div>`,

    TIPO_CAMBIO: (d) => `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--gold"><i class="ph-currency-dollar"></i></div>
                <div>
                    <div class="card__title">Tipo de cambio ${d.fecha || ''}</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
                <div><div class="card__monto">S/ ${d.compra || '—'}</div><div class="card__subtitle">Compra SBS</div></div>
                <div><div class="card__monto">S/ ${d.venta || '—'}</div><div class="card__subtitle">Venta SBS</div></div>
            </div>
        </div>`,

    DIFERENCIA_SIRE: (d) => `
        <div class="card">
            <div class="card__header">
                <div class="card__icon card__icon--gold"><i class="ph-arrows-left-right"></i></div>
                <div><div class="card__title">Diferencia SIRE ${d.periodo || ''}</div></div>
            </div>
            <table class="card__table">
                <tr><th>Fuente</th><th>Monto</th></tr>
                <tr><td>Sistema</td><td class="num">S/ ${fmt(d.total_sistema)}</td></tr>
                <tr><td>SUNAT SIRE</td><td class="num">S/ ${fmt(d.total_sire)}</td></tr>
                <tr><td><strong>Diferencia</strong></td><td class="num ${d.diferencia > 0 ? 'card__monto--gold' : ''}"><strong>S/ ${fmt(d.diferencia)}</strong></td></tr>
            </table>
        </div>`,
};

function fmt(n) {
    if (n === null || n === undefined) return '0.00';
    return Number(n).toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function renderCard(tipo, datos, contenedor) {
    const fn = CardTypes[tipo];
    if (!fn) return;
    const html = fn(datos);
    if (typeof contenedor === 'string') contenedor = document.getElementById(contenedor);
    if (contenedor) contenedor.insertAdjacentHTML('beforeend', html);
    return html;
}

function compartirCard(tipo, datos) {
    let texto = '';
    if (tipo === 'comprobante') {
        texto = `${(datos.tipo||'').toUpperCase()} ${datos.serie}-${datos.correlativo}\nRUC: ${datos.ruc_emisor}\nTotal: S/ ${fmt(datos.total)}\nFecha: ${datos.fecha_emision}`;
    } else if (tipo === 'alerta') {
        texto = `⚠️ ${datos.titulo}\n${datos.descripcion || datos.mensaje || ''}`;
    } else {
        texto = JSON.stringify(datos, null, 2);
    }

    if (navigator.share) {
        navigator.share({ text: texto }).catch(() => {});
    } else if (navigator.clipboard) {
        navigator.clipboard.writeText(texto);
        if (typeof showToast === 'function') showToast('Copiado al portapapeles');
    }
}

function iniciarCorreccion(comprobanteId) {
    fetch(`/correccion/${comprobanteId}/iniciar`, { method: 'POST' })
        .then(r => r.json())
        .then(d => { if (typeof showToast === 'function') showToast(d.detail || 'Corrección iniciada'); })
        .catch(() => { if (typeof showToast === 'function') showToast('Error'); });
}
