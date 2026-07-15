/* ═══════════════════════════════════════════════════════════════════
   resumen.js — alerta.pe (zAlerta-12 P1)
   Tabla resumen del buzón con caché OFFLINE (IndexedDB):
     - Con red: lee /api/resumen, la muestra y la guarda en IndexedDB.
     - Sin red: lee de IndexedDB (tras la 1ª entrada se ve igual, offline).
   Cada fila trae el botón "Qué hacer" con orientación mínima (solo lo evidente).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const wrap = document.getElementById('rsm-tabla-wrap');
  if (!wrap) return;
  const estadoEl = document.getElementById('rsm-estado');
  const vacioEl = document.getElementById('rsm-vacio');

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Orientación mínima por tipo (solo lo OBVIO y seguro). No es análisis real.
  const ORIENTA = {
    orden_pago: 'Indica un tributo que SUNAT considera pendiente. Revisa el periodo y el monto; si corresponde, paga antes del vencimiento para evitar intereses y cobranza coactiva.',
    multa: 'SUNAT aplicó una sanción. Revisa el motivo y el plazo; evalúa si corresponde pagar (con posible gradualidad) o reclamar.',
    resolucion_determinacion: 'SUNAT determinó una deuda u observación. Revisa el detalle y el plazo; consulta con tu contador si conviene pagar o reclamar.',
    cobranza_coactiva: 'Es una etapa de cobranza. Atiéndela cuanto antes para evitar embargos; revisa el monto y acércate o paga dentro del plazo.',
    fraccionamiento: 'Se refiere a un fraccionamiento de deuda. Revisa las cuotas y sus fechas para no perder el beneficio.',
    esquela: 'SUNAT te comunica una observación o te pide algo. Lee qué solicita y atiéndelo dentro del plazo indicado.',
    aviso: 'Es un aviso informativo. Revísalo cuando puedas; por lo general no requiere acción urgente.',
    pago: 'Es la constancia de un pago realizado ante SUNAT. Confirma que el pago quedó registrado y guarda el comprobante.',
    otro: 'Revisa el documento y, si tiene plazo, atiéndelo a tiempo. Ante dudas, consulta con tu contador.',
  };

  // Label contextual del botón por tipo (zAlerta-32).
  const BTN_LABEL = {
    cobranza_coactiva: 'Ver deuda y plazo',
    orden_pago: 'Revisar y pagar',
    multa: 'Ver multa',
    resolucion_determinacion: 'Ver resolución',
    fraccionamiento: 'Ver cuotas',
    esquela: 'Ver esquela',
    pago: 'Ver constancia',
    otro: 'Ver detalle',
  };

  // Datos ricos de un PAGO confirmado (zAlerta-69) → lista de "Etiqueta: valor".
  function pagoDatos(pg) {
    const f = [];
    if (pg.importe_fmt) f.push('Importe: <b>' + esc(pg.importe_fmt) + '</b>');
    if (pg.tributo) f.push('Tributo: ' + esc(pg.tributo));
    if (pg.periodo) f.push('Periodo: ' + esc(pg.periodo));
    if (pg.valor_pagado) f.push('Valor pagado: ' + esc(pg.valor_pagado));
    if (pg.banco) f.push('Banco: ' + esc(pg.banco));
    if (pg.n_operacion) f.push('N° operación: ' + esc(pg.n_operacion));
    if (pg.n_orden) f.push('N° orden: ' + esc(pg.n_orden));
    return f;
  }
  function pagoModal(pg) {
    const datos = pagoDatos(pg);
    const pdf = pg.gcs_disponible
      ? '<div class="rsm-mod-pdf"><span class="material-symbols-outlined">receipt_long</span>'
        + '<span class="rsm-mod-pdf-nom">Constancia de pago</span>'
        + '<a class="rsm-mod-btn" href="/valorados/' + esc(pg.valorado_id) + '/ver" target="_blank" rel="noopener">Ver PDF</a>'
        + '<a class="rsm-mod-btn rsm-mod-btn--sec" href="/valorados/' + esc(pg.valorado_id) + '/descargar">Descargar</a></div>'
      : '';
    return '<div class="rsm-mod-pdfs"><div class="rsm-mod-lbl">Pago confirmado</div>'
      + (datos.length ? '<div class="rsm-pago-datos">' + datos.map((d) => '<span>' + d + '</span>').join('') + '</div>'
          : '<div class="rsm-pago-datos"><span class="muted">Abre el PDF para ver el detalle del pago.</span></div>')
      + pdf + '</div>';
  }
  const URG_LBL = {
    critica: 'Crítica', urgente: 'Urgente', importante: 'Importante',
    informativa: 'Informativa', sin_clasificar: 'Informativa',
  };

  // Orientación específica por subtipo coactivo (zAlerta-70). "Informa, no asesora":
  // la Conclusión NO afirma extinción de deuda.
  function coactivoOrienta(c) {
    if (c.tercero_retenedor) return 'Eres tercero retenedor: debes retener el importe indicado y comunicarlo a SUNAT dentro del plazo (5 días hábiles). El incumplimiento puede generar responsabilidad solidaria.';
    const T = {
      ejecucion: 'Cobranza coactiva de una deuda exigible. Revisa el monto y el plazo; atiéndela cuanto antes para evitar embargos.',
      retencion: 'Medida de retención sobre fondos o cuentas por una deuda en cobranza. Revisa el detalle en el PDF.',
      levantamiento: 'El embargo fue levantado. Es una buena noticia; guarda la constancia.',
      reduccion: 'Se redujo el monto embargado. Revisa el nuevo importe en el PDF.',
      conclusion: 'El procedimiento coactivo concluyó. Concluir no significa que la deuda esté extinguida: verifica el estado de la deuda.',
      fl: 'Actuación administrativa interna del expediente. Informativa, sin acción inmediata.',
    };
    return T[c.subtipo] || null;
  }
  function coactivoPDF(c) {
    if (!c.valorado_id || !c.gcs_disponible) return '';
    return '<div class="rsm-mod-pdfs"><div class="rsm-mod-lbl">Documento</div>'
      + '<div class="rsm-mod-pdf"><span class="material-symbols-outlined">description</span>'
      + '<span class="rsm-mod-pdf-nom">Resolución coactiva</span>'
      + '<a class="rsm-mod-btn" href="/valorados/' + esc(c.valorado_id) + '/ver" target="_blank" rel="noopener">Ver PDF</a>'
      + '<a class="rsm-mod-btn rsm-mod-btn--sec" href="/valorados/' + esc(c.valorado_id) + '/descargar">Descargar</a></div></div>';
  }

  // ── Modal custom (sin diálogos nativos): detalle + orientación + PDFs ──
  function abrirModal(f) {
    if (!f) return;
    // Marcar como LEÍDA al abrir el modal (zAlerta-47): server-side (compartido
    // entre equipos) y quita el resalte "Nuevo" de esa tarjeta. No al cargar.
    if (!f.leida && f.id) {
      f.leida = true;
      fetch('/api/notificacion/' + f.id + '/leida',
        { method: 'POST', credentials: 'include' }).catch(() => {});
      pintarLista();
    }
    const sem = semColor(f);
    const orienta = (f.coactivo && coactivoOrienta(f.coactivo))
      || ORIENTA[f.tipo] || ORIENTA.otro;
    const meta = [];
    meta.push('<span class="rsm-mod-chip sem-bg--' + sem + '">' + esc(URG_LBL[f.urgencia] || 'Informativa') + '</span>');
    if (f.monto) meta.push('<span class="rsm-mod-chip rsm-mod-chip--deuda">' + esc(f.monto) + '</span>');
    if (f.periodo && f.periodo !== '—') meta.push('<span class="rsm-mod-tag">Periodo: ' + esc(f.periodo) + '</span>');
    if (f.fecha && f.fecha !== '—') meta.push('<span class="rsm-mod-tag">Documento: ' + esc(f.fecha) + '</span>');
    if (f.vence_iso) meta.push('<span class="rsm-mod-tag">Vence: ' + esc(f.vence) + '</span>');
    if (f.ruc) meta.push('<span class="rsm-mod-tag">RUC ' + esc(f.ruc) + '</span>');

    // PDF de DEUDA (primario, desde GCS). La constancia, secundaria.
    let pdfHtml = '';
    if (f.tiene_deuda && f.gcs_disponible && f.valorado_id) {
      pdfHtml += '<div class="rsm-mod-pdfs"><div class="rsm-mod-lbl">Documento de deuda</div>'
        + '<div class="rsm-mod-pdf"><span class="material-symbols-outlined">request_quote</span>'
        + '<span class="rsm-mod-pdf-nom">' + esc(f.num_documento || 'Documento de deuda')
        + (f.monto ? ' · ' + esc(f.monto) : '') + '</span>'
        + '<a class="rsm-mod-btn" href="/valorados/' + esc(f.valorado_id) + '/ver" target="_blank" rel="noopener">Ver PDF</a>'
        + '<a class="rsm-mod-btn rsm-mod-btn--sec" href="/valorados/' + esc(f.valorado_id) + '/descargar">Descargar</a>'
        + '</div></div>';
    }
    const cons = (f.adjuntos || []).map((a) =>
      '<div class="rsm-mod-pdf"><span class="material-symbols-outlined">picture_as_pdf</span>'
      + '<span class="rsm-mod-pdf-nom">' + esc(a.nombre || 'Constancia.pdf') + '</span>'
      + '<a class="rsm-mod-btn" href="/adjuntos/' + esc(a.id) + '/ver" target="_blank" rel="noopener">Ver PDF</a>'
      + '<a class="rsm-mod-btn rsm-mod-btn--sec" href="/adjuntos/' + esc(a.id) + '/descargar">Descargar</a>'
      + '</div>').join('');
    if (cons) {
      pdfHtml += '<div class="rsm-mod-pdfs"><div class="rsm-mod-lbl">'
        + (f.tiene_deuda ? 'Constancia de notificación' : 'Documentos adjuntos')
        + '</div>' + cons + '</div>';
    }
    if (f.es_pago && f.pago) pdfHtml = pagoModal(f.pago) + pdfHtml;
    if (f.es_esquela && f.esquela) pdfHtml = esquelaModal(f.esquela) + pdfHtml;
    if (f.coactivo) pdfHtml = coactivoChip(f) + coactivoPDF(f.coactivo) + pdfHtml;
    if (!pdfHtml) pdfHtml = '<div class="rsm-mod-sinpdf">Esta notificación no tiene PDF.</div>';

    const ov = document.createElement('div');
    ov.className = 'rsm-mod-ov';
    ov.innerHTML =
      '<div class="rsm-mod" role="dialog" aria-modal="true" aria-label="Detalle de la notificación">'
      + '<button class="rsm-mod-x" aria-label="Cerrar"><span class="material-symbols-outlined">close</span></button>'
      + '<div class="rsm-mod-tipo">' + esc(f.documento) + '</div>'
      + '<h3 class="rsm-mod-asunto">' + esc(f.asunto || f.detalle || 'Notificación') + '</h3>'
      + '<div class="rsm-mod-meta">' + meta.join('') + '</div>'
      + '<p class="rsm-mod-orienta">' + esc(orienta) + '</p>'
      + pdfHtml
      + '</div>';

    function cerrar() {
      ov.remove();
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') cerrar(); }
    ov.addEventListener('click', (e) => { if (e.target === ov) cerrar(); });
    ov.querySelector('.rsm-mod-x').addEventListener('click', cerrar);
    document.addEventListener('keydown', onKey);
    document.body.appendChild(ov);
  }

  // ── IndexedDB mínima (sin librerías) ──
  const DB = 'alertape', STORE = 'resumen', KEY = 'mi-resumen';
  function abrir() {
    return new Promise((res, rej) => {
      if (!('indexedDB' in window)) return rej(new Error('no idb'));
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => { r.result.createObjectStore(STORE); };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  async function guardar(data) {
    try {
      const db = await abrir();
      await new Promise((res, rej) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put(data, KEY);
        tx.oncomplete = res; tx.onerror = () => rej(tx.error);
      });
    } catch (_) { /* localStorage de respaldo (poco dato) */
      try { localStorage.setItem('alertape_resumen', JSON.stringify(data)); } catch (e) {}
    }
  }
  async function leerCache() {
    try {
      const db = await abrir();
      return await new Promise((res, rej) => {
        const tx = db.transaction(STORE, 'readonly');
        const g = tx.objectStore(STORE).get(KEY);
        g.onsuccess = () => res(g.result || null);
        g.onerror = () => rej(g.error);
      });
    } catch (_) {
      try { return JSON.parse(localStorage.getItem('alertape_resumen') || 'null'); }
      catch (e) { return null; }
    }
  }

  function pintarEstado(txt, cls) { estadoEl.textContent = txt; estadoEl.className = 'rsm-estado ' + (cls || ''); }

  // ── "Última actualización" + rango "sin novedades" (zAlerta-48 FASE D) ──
  let _ultimaAct = null;                    // ISO de la última revisión de SUNAT
  function fmtHM(iso) {
    const d = iso ? new Date(iso) : new Date();
    if (isNaN(d)) return '';
    return d.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' });
  }
  function actualizarEncabezado(data) {
    _ultimaAct = (data && data.ultima_actualizacion) || _ultimaAct;
    const el = document.getElementById('rsm-actualizado');
    if (!el) return;
    if (_ultimaAct) {
      const d = new Date(_ultimaAct);
      const dia = d.toLocaleDateString('es-PE', { day: '2-digit', month: '2-digit' });
      el.textContent = 'Última actualización: ' + fmtHM(_ultimaAct) + ', ' + dia;
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }

  // ── Semáforo: URGENCIA (de la carpeta SUNAT) + plazo (zAlerta-28) ──
  // rojo:  urgencia alta (Coactiva / Orden de Pago / Multa) AUNQUE no tenga
  //        fecha, o vence en <=7 días (o vencido).
  // ámbar: tiene plazo a >7 días, o "importante" sin fecha.
  // verde: informativo / sin urgencia y sin plazo. NO se infieren plazos.
  const URG_ALTA = { critica: 1, urgente: 1 };
  function semColor(f) {
    const u = (f && f.urgencia) || 'sin_clasificar';
    if (URG_ALTA[u]) return 'rojo';
    const venceIso = f && f.vence_iso;
    if (venceIso) {
      const v = new Date(venceIso);
      if (!isNaN(v)) {
        const dias = Math.ceil((v - new Date()) / 86400000);
        return dias <= 7 ? 'rojo' : 'ambar';
      }
    }
    if (u === 'importante') return 'ambar';
    return 'verde';
  }
  const SEM_TXT = {
    rojo: 'Urgente: revísalo y envíalo a tu contador cuanto antes.',
    ambar: 'Importante: tiene plazo o requiere atención, no lo dejes pasar.',
    verde: 'Informativo, sin apuro — se recomienda leer.',
  };

  // "Recuérdame esto": opciones (value = modo del backend; '' = desactivar).
  const REC_OPTS = [
    ['', 'Sin recordatorio'],
    ['proximos_3', 'Los próximos 3 días'],
    ['ultimos_3', 'Los últimos 3 días antes de vencer'],
    ['hasta_vencer', 'Todos los días hasta el vencimiento'],
  ];

  // ── Diseño C (zAlerta-38): métricas + chips + lista de tarjetas ──
  const DEUDA_TIPOS = ['cobranza_coactiva', 'orden_pago', 'multa',
    'fraccionamiento', 'resolucion_determinacion'];
  const TIPO_LBL = {
    cobranza_coactiva: 'Cobranza Coactiva', orden_pago: 'Orden de Pago',
    multa: 'Multa', fraccionamiento: 'Fraccionamiento',
    resolucion_determinacion: 'Resolución', pago: 'Pagos', informativas: 'Informativas',
  };
  const CHIPS_ORDEN = ['todo', 'cobranza_coactiva', 'orden_pago', 'multa',
    'fraccionamiento', 'resolucion_determinacion', 'pago', 'informativas'];
  function grupoDe(f) {
    if (f.es_pago || f.tipo === 'pago') return 'pago';   // filtro "Pagos" (zAlerta-77)
    return DEUDA_TIPOS.indexOf(f.tipo) >= 0 ? f.tipo : 'informativas';
  }

  // ── Sistema de dos ejes por tarjeta (zAlerta-77): naturaleza (color) + estado
  //    (estilo de línea) + ícono SVG (Material Symbols, sobrio; NO emojis). ──
  function naturalezaDe(f) {
    if (f.es_pago) return 'ok';                          // pago confirmado → verde
    if (f.es_esquela) return 'aviso';                    // omiso → ámbar (atención)
    if (f.coactivo && f.coactivo.grupo) {
      const g = f.coactivo.grupo;
      if (g === 'alivio') return 'ok';                   // levantamiento/reducción → verde
      if (g === 'cierre' || g === 'admin') return 'neutro';
      return 'riesgo';                                   // ejecución/retención → rojo
    }
    const s = semColor(f);
    if (s === 'rojo') return 'riesgo';
    if (s === 'ambar') return 'aviso';
    return f.tiene_deuda ? 'ok' : 'info';                // informativo → azul
  }
  function estadoDe(f) {
    // Confirmado (sólido): hechos concretos (deuda notificada, pago, coactiva).
    if (f.es_pago || f.tiene_deuda || f.coactivo) return 'confirmado';
    return 'proceso';                                    // informativo/aviso → punteado
  }
  function iconoDe(f) {
    if (f.es_pago) return 'check_circle';
    if (f.es_esquela || f.tipo === 'esquela') return 'assignment_late';   // omiso
    if (f.coactivo || f.tipo === 'cobranza_coactiva') return 'gavel';   // mazo (Duilio)
    return ({ orden_pago: 'receipt_long', multa: 'warning',
      fraccionamiento: 'calendar_month', resolucion_determinacion: 'description',
      aviso: 'notifications' })[f.tipo] || 'description';
  }
  // Chip + datos de una Esquela de Omiso en la tarjeta (zAlerta-81).
  function esquelaChip(f) {
    if (!f.es_esquela || !f.esquela) return '';
    const e = f.esquela, bits = [];
    if (e.resumen) bits.push(esc(e.resumen));
    if (e.tributo_sancion) bits.push('Tributo ' + esc(e.tributo_sancion));
    return '<div class="rsm-pago-chip rsm-omiso-chip">'
      + '<span class="material-symbols-outlined">assignment_late</span>'
      + '<span>Omiso' + (bits.length ? ': ' + bits.join(' · ') : ' — declaración no presentada') + '</span></div>';
  }
  function esquelaModal(e) {
    const datos = [];
    if (e.periodos && e.periodos.length) datos.push('Período(s): <b>' + esc(e.periodos.join(', ')) + '</b>');
    if (e.tributo_sancion) datos.push('Tributo sanción: ' + esc(e.tributo_sancion));
    if (e.tributo_asociado) datos.push('Tributo asociado: ' + esc(e.tributo_asociado));
    const pdf = e.gcs_disponible
      ? '<div class="rsm-mod-pdf"><span class="material-symbols-outlined">assignment_late</span>'
        + '<span class="rsm-mod-pdf-nom">Esquela de Omiso</span>'
        + '<a class="rsm-mod-btn" href="/valorados/' + esc(e.valorado_id) + '/ver" target="_blank" rel="noopener">Ver esquela</a>'
        + '<a class="rsm-mod-btn rsm-mod-btn--sec" href="/valorados/' + esc(e.valorado_id) + '/descargar">Descargar</a></div>'
      : '';
    return '<div class="rsm-mod-pdfs"><div class="rsm-mod-lbl">Omiso — declaración no presentada</div>'
      + (datos.length ? '<div class="rsm-pago-datos">' + datos.map((d) => '<span>' + d + '</span>').join('') + '</div>'
          : '<div class="rsm-pago-datos"><span class="muted">Abre la esquela para ver el período y tributo omitidos.</span></div>')
      + pdf + '</div>';
  }
  // ¿la notificación tiene un documento PDF descargable? (zAlerta-79 Problema A)
  function tienePdf(f) {
    return !!((f.tiene_deuda && f.gcs_disponible)
      || (f.pago && f.pago.gcs_disponible)
      || (f.coactivo && f.coactivo.gcs_disponible)
      || (f.esquela && f.esquela.gcs_disponible)
      || (f.adjuntos && f.adjuntos.length));
  }
  function tipoLegible(f) {
    if (f.es_esquela) return 'Esquela de Omiso';         // zAlerta-81
    // Subtipo coactivo (zAlerta-70): la etiqueta específica manda ("Embargo
    // levantado", "Retención a terceros", etc.) sobre el genérico "Cobranza Coactiva".
    if (f.coactivo && f.coactivo.etiqueta) return f.coactivo.etiqueta;
    return f.documento || TIPO_LBL[grupoDe(f)] || 'Notificación';
  }

  // Chip + alerta del subtipo coactivo en la tarjeta (zAlerta-70).
  function coactivoChip(f) {
    const c = f.coactivo;
    if (!c || !c.subtipo) return '';
    let html = '';
    if (c.tercero_retenedor) {
      html += '<div class="rsm-coa-alerta">⚠ Acción requerida: eres tercero retenedor'
        + (c.deudor_ruc ? ' del RUC ' + esc(c.deudor_ruc) : '')
        + '. Debes retener y comunicar a SUNAT dentro del plazo (5 días hábiles).</div>';
    }
    if (c.subtipo === 'conclusion') {
      html += '<div class="rsm-coa-nota rsm-coa--cierre">Procedimiento coactivo concluido — verifica el estado de la deuda.</div>';
    }
    return html;
  }
  function montoSoles(n) { return 'S/ ' + Math.round(n).toLocaleString('es-PE'); }

  let _filtro = 'todo';
  let _filas = [];

  // Estado de lectura de EQUIPO (Capa 2, zAlerta-68): solo buzones de 2+ personas.
  function equipoHTML(f) {
    const eq = f.equipo;
    if (!eq || !eq.miembros || eq.total < 2) return '';
    const chips = eq.miembros.map((m) =>
      '<span class="rsm-eq-m rsm-eq-m--' + (m.leida ? 'si' : 'no') + '">'
      + esc(m.nombre) + ' ' + (m.leida ? '✓' : '✗') + '</span>').join('');
    return '<div class="rsm-equipo">'
      + '<span class="rsm-eq-n">' + eq.leidos + ' de ' + eq.total + ' leyeron</span>'
      + '<span class="rsm-eq-lista">' + chips + '</span></div>';
  }

  // Chip compacto de PAGO confirmado en la tarjeta (zAlerta-69).
  function pagoChip(f) {
    if (!f.es_pago || !f.pago) return '';
    const pg = f.pago, bits = [];
    if (pg.importe_fmt) bits.push('<b>' + esc(pg.importe_fmt) + '</b>');
    if (pg.tributo) bits.push(esc(pg.tributo));
    if (pg.periodo) bits.push('periodo ' + esc(pg.periodo));
    return '<div class="rsm-pago-chip"><span class="material-symbols-outlined">receipt_long</span>'
      + '<span>Pago confirmado' + (bits.length ? ': ' + bits.join(' · ') : '') + '</span></div>';
  }

  function tarjeta(f, i) {
    const sem = semColor(f);
    const conPlazo = !!f.vence_iso;
    const venceTxt = f.es_pago
      ? '<span class="rsm-c-tag rsm-lbl--pago">Pago confirmado</span>'
      : (conPlazo
        ? '<span class="rsm-c-vence">Vence ' + esc(f.vence) + '</span>'
        : (sem === 'rojo' ? '<span class="rsm-c-tag rsm-lbl--rojo">Urgente</span>'
          : sem === 'ambar' ? '<span class="rsm-c-tag rsm-lbl--ambar">Importante</span>'
            : '<span class="rsm-c-tag">Informativo</span>'));
    // Monto: si está, lo mostramos; si es deuda SIN monto parseado, NUNCA "S/ 0"
    // (engañoso) — un rótulo honesto (zAlerta-49). La deuda nunca se oculta.
    const monto = f.monto
      ? '<span class="rsm-c-monto">' + esc(f.monto) + '</span>'
      : (f.tiene_deuda ? '<span class="rsm-c-monto rsm-c-monto--rev">Ver monto en el PDF</span>' : '');
    const btnLabel = (f.coactivo && f.coactivo.accion) || BTN_LABEL[f.tipo] || BTN_LABEL.otro;
    const nueva = !f.leida;
    const badgeNuevo = nueva ? '<span class="rsm-nuevo">Nuevo</span>' : '';
    // Dos ejes (zAlerta-77): naturaleza (color) + estado (línea) + ícono.
    const nat = naturalezaDe(f), est = estadoDe(f);
    const ico = '<span class="rsm-c-ico"><span class="material-symbols-outlined">'
      + iconoDe(f) + '</span></span>';
    return '<div class="rsm-card bloque ' + nat + ' ' + est + (nueva ? ' rsm-card--nueva' : '')
      + '" data-i="' + i + '" title="' + esc(SEM_TXT[sem]) + '">'
      + '<div class="rsm-c-top">' + ico
      + '<b class="rsm-c-tipo">' + esc(tipoLegible(f)) + '</b>'
      + '<span class="rsm-c-top-r">' + badgeNuevo
      + (tienePdf(f) ? '<span class="rsm-pdf-badge" title="Tiene documento PDF">'
          + '<span class="material-symbols-outlined">picture_as_pdf</span></span>' : '')
      + '</span></div>'
      + '<div class="rsm-c-asunto">' + esc(f.detalle) + '</div>'
      + '<div class="rsm-c-meta">'
      + '<span class="rsm-c-fecha"><i class="ti ti-calendar"></i> ' + esc(f.fecha || '—') + '</span>'
      + monto + venceTxt + '</div>'
      + coactivoChip(f)
      + pagoChip(f)
      + esquelaChip(f)
      + equipoHTML(f)
      + '<div class="rsm-c-acc"><button class="rsm-b rsm-b--' + nat + '" data-i="' + i + '">'
      + esc(btnLabel) + '</button></div>'
      + '</div>';
  }

  function pintarLista() {
    const vis = _filtro === 'todo' ? _filas
      : _filas.filter((f) => grupoDe(f) === _filtro);
    const cont = document.getElementById('rsm-lista');
    if (!cont) return;
    cont.innerHTML = vis.length
      ? vis.map((f) => tarjeta(f, _filas.indexOf(f))).join('')
      : '<p class="muted" style="padding:14px 4px">Nada en esta categoría.</p>';
    cont.querySelectorAll('.rsm-card, .rsm-b').forEach((el) =>
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const host = el.closest('.rsm-card') || el;
        abrirModal(_filas[+host.dataset.i]);
      }));
    // chip activo
    document.querySelectorAll('.rsm-chip').forEach((c) =>
      c.classList.toggle('rsm-chip--on', c.dataset.f === _filtro));
  }

  // Contenedores del diseño C (presentes en resumen.html; zAlerta-38b).
  function elMetricas() { return document.getElementById('rsm-metricas'); }
  function elChips() { return document.getElementById('rsm-chips'); }

  function render(data, offline) {
    _filas = (data && data.filas) || [];
    vacioEl.hidden = _filas.length > 0;
    if (!_filas.length) {
      [elMetricas(), elChips(), document.getElementById('rsm-lista')]
        .forEach((el) => { if (el) el.innerHTML = ''; });
      return;
    }

    // ── Cabecera: 3 contadores HONESTOS (zAlerta-76), del servidor (deuda.py) ──
    // Yuxtapone hechos, NO calcula saldo: notificadas y con-pago por separado.
    function kpi(cls, n, sub, label) {
      return '<div class="rsm-kpi bloque ' + cls + ' confirmado">'
        + '<span class="rsm-kpi-n">' + n + '</span>'
        + '<span class="rsm-kpi-l">' + label + '</span>'
        + (sub ? '<span class="rsm-kpi-m">' + esc(sub) + '</span>' : '')
        + '</div>';
    }
    const cab = data && data.cabecera;
    let metr;
    if (cab) {
      metr = kpi('riesgo', cab.notificadas.n, cab.notificadas.monto_fmt || '', 'Deudas notificadas')
        + kpi('ok', cab.con_pago.n, cab.con_pago.monto_fmt || '', 'Con pago registrado')
        + kpi('aviso', cab.con_plazo.n, cab.con_plazo.n ? 'este mes' : '', 'Con plazo próximo');
    } else {
      // Fallback (caché offline vieja, sin cabecera): métrica mínima honesta.
      const nAccion = _filas.filter((f) => semColor(f) === 'rojo').length;
      metr = kpi('riesgo', nAccion, '', 'Necesitan atención')
        + kpi('neutro', _filas.length - nAccion, '', 'Informativas');
    }

    // Chips (solo los presentes; "Todo" siempre)
    const cuenta = {};
    _filas.forEach((f) => { const g = grupoDe(f); cuenta[g] = (cuenta[g] || 0) + 1; });
    const chips = CHIPS_ORDEN
      .filter((k) => k === 'todo' || cuenta[k])
      .map((k) => '<button class="rsm-chip" data-f="' + k + '">'
        + (k === 'todo' ? 'Todo' : esc(TIPO_LBL[k]) + ' (' + cuenta[k] + ')') + '</button>')
      .join('');

    // Rellenar los contenedores nombrados del HTML (contrato explícito).
    const cm = elMetricas(), cc = elChips();
    if (cm) cm.innerHTML = metr;
    if (cc) {
      cc.innerHTML = chips;
      cc.querySelectorAll('.rsm-chip').forEach((c) =>
        c.addEventListener('click', () => { _filtro = c.dataset.f; pintarLista(); }));
    }
    pintarLista();
  }

  async function cargar() {
    let online = null;
    try {
      const r = await fetch('/api/resumen', { credentials: 'include' });
      if (r.ok) online = await r.json();
    } catch (_) { online = null; }

    if (online && online.ok) {
      render(online, false);
      pintarEstado('Al día', 'ok');
      actualizarEncabezado(online);
      mostrarSplashPush(online.no_leidas);      // splash solo si hay novedades
      guardar(online);                          // background: queda offline
    } else {
      const cache = await leerCache();
      if (cache) {
        render(cache, true);
        actualizarEncabezado(cache);
        pintarEstado('Sin conexión · datos guardados', 'off');
      } else {
        pintarEstado('Sin conexión', 'off');
        vacioEl.hidden = false;
        const txt = document.getElementById('rsm-vacio-txt');
        if (txt) txt.textContent = 'Sin conexión y aún no hay datos guardados. '
          + 'Conéctate una vez para guardarlos en tu celular.';
      }
    }
    return online;
  }

  // ── Métrica de lectura (GRACIAS): fuera del poll, se dispara UNA vez ──
  function marcarVista() {
    fetch('/api/alerta/vista', { method: 'POST', credentials: 'include' }).catch(() => {});
  }

  // ── Poller ÚNICO de "lectura activa" (zAlerta-42/43/44) ──────────────
  // Sondea /api/resumen y PARA cuando `hay_lectura_activa === false` (el worker
  // bajó el flag = terminó), o crece el buzón, o vence el tope de seguridad.
  // Registro único: solo un intervalo a la vez → nunca dos pollers en paralelo.
  let _pollTimer = null;
  function pararPoll() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }
  function iniciarPoll(onListo, tope) {
    pararPoll();                                   // cancela cualquier poller previo
    const antes = totalActual();
    let intentos = 0;
    tope = tope || 40;                             // 40 × 6s ≈ 4 min (respaldo)
    _pollTimer = setInterval(async () => {
      intentos += 1;
      const data = await cargar(false);            // sin /vista en cada poll
      const crecio = totalActual() > antes;
      const termino = !!(data && data.hay_lectura_activa === false);
      const vencio = intentos >= tope;
      if (termino || crecio || vencio) {
        pararPoll();                               // limpia el intervalo (no huérfano)
        marcarVista();                             // el usuario ya ve el buzón
        onListo({ crecio: crecio, termino: termino, vencio: vencio });
      }
    }, 6000);
  }

  // Cierra el indicador de onboarding (primera lectura) al terminar.
  function cerrarOnboarding(msg) {
    const ind = document.getElementById('ob-indicador');
    if (window.__obHandle) { try { window.__obHandle.detener(); } catch (_) {} }
    if (ind) ind.remove();
    pintarEstado(msg || 'Buzón actualizado', 'ok');
  }
  function vigilarOnboarding() {
    if (!document.getElementById('ob-indicador')) return;   // no hay primera lectura
    iniciarPoll((r) => cerrarOnboarding(
      (r.crecio || totalActual() > 0) ? 'Buzón actualizado' : 'Todo listo'));
  }

  // ── Mini-leyenda del semáforo (siempre visible, discreta) ──
  function leyendaHTML() {
    return '<div class="rsm-leyenda">'
      + '<span><span class="rsm-punto sem-bg--rojo"></span> Urgente</span>'
      + '<span><span class="rsm-punto sem-bg--ambar"></span> Tiene plazo</span>'
      + '<span><span class="rsm-punto sem-bg--verde"></span> Informativo</span>'
      + '</div>';
  }

  // ── Leyenda persistente + splash del push (zAlerta-17 P3 / zAlerta-48 FASE D) ──
  function mostrarSplash() {
    // Leyenda persistente sobre la lista (siempre).
    const ley = document.createElement('div');
    ley.innerHTML = leyendaHTML();
    wrap.parentNode.insertBefore(ley.firstChild, wrap);
  }
  // El splash amarillo "Esto es lo que dejamos en tu celular" solo cuando vino
  // del push Y HAY algo nuevo (zAlerta-48 FASE D: no aparece con cero novedades).
  function mostrarSplashPush(noLeidas) {
    const desdePush = new URLSearchParams(location.search).get('from') === 'push';
    const cont = document.getElementById('rsm');
    if (!cont || !desdePush || !noLeidas || sessionStorage.getItem('rsm_splash')) return;
    sessionStorage.setItem('rsm_splash', '1');
    const s = document.createElement('div');
    s.className = 'rsm-splash';
    s.innerHTML = '<button class="rsm-splash-x" aria-label="Cerrar">&times;</button>'
      + '<div class="rsm-splash-tit"><i class="ti ti-device-mobile-check"></i> '
      + 'Tienes ' + noLeidas + ' novedad(es) en tu buzón</div>'
      + '<p class="rsm-splash-txt">Revisa con calma; queda guardado aquí, '
      + 'incluso sin internet.</p>' + leyendaHTML();
    cont.insertBefore(s, cont.firstChild);
    s.querySelector('.rsm-splash-x').onclick = () => s.remove();
  }

  // ── "Actualizar ahora" (zAlerta-36): dispara la lectura de día ──
  // Reusa POST /contribuyentes/{id}/actualizar (marca el flag; el worker corre
  // fuera del gate nocturno). Muestra el indicador estilizado y, en cuanto el
  // buzón crece, lo refresca. Sin diálogos nativos.
  function totalActual() {
    return _filas.length;   // Diseño C usa tarjetas (.rsm-card), no filas de tabla.
  }
  const btnAct = document.getElementById('rsm-actualizar');
  if (btnAct) btnAct.addEventListener('click', async () => {
    const live = document.getElementById('rsm-ob-live');
    let handle = null;
    // Feedback INMEDIATO (zAlerta-37 BUG B): monta el indicador antes de nada,
    // así el usuario ve respuesta apenas pulsa (nunca queda "plano").
    btnAct.disabled = true;
    if (live && window.obMontar) {
      live.hidden = false;
      handle = window.obMontar(live, btnAct.dataset.anioActual, btnAct.dataset.anioAnterior);
    }
    function terminar(msg, cls) {
      if (handle) handle.detener();
      if (live) { live.hidden = true; live.innerHTML = ''; }
      btnAct.disabled = false;
      if (msg) pintarEstado(msg, cls || 'ok');
    }

    const ids = (btnAct.dataset.ids || '').split(',').filter(Boolean);
    if (!ids.length) { terminar('No pudimos identificar tu RUC. Recarga la página.', 'off'); return; }

    // POST a la cola manual y VERIFICAR la respuesta (no tragar 4xx en silencio).
    let algunoOk = false;
    await Promise.all(ids.map(async (id) => {
      try {
        const r = await fetch('/contribuyentes/' + id + '/actualizar',
          { method: 'POST', credentials: 'include' });
        if (r.ok) algunoOk = true;
      } catch (_) { /* red caída: lo intentamos igual abajo */ }
    }));
    if (!algunoOk) {
      terminar('No se pudo solicitar la actualización. Inténtalo de nuevo.', 'off');
      return;
    }

    // Rango para el mensaje "sin novedades" (zAlerta-48 FASE D): desde la última
    // revisión conocida hasta ahora. Se captura ANTES de refrescar.
    const desdeIso = _ultimaAct;
    // Poller ÚNICO: para cuando la actualización que pedí TERMINÓ
    // (hay_lectura_activa === false), o crece el buzón, o vence el tope. zAlerta-44.
    iniciarPoll((r) => {
      if (r.crecio) { terminar('Buzón actualizado'); return; }
      // Sin novedades: rango de horas claro y tranquilizador.
      const desdeTxt = desdeIso ? ' desde las ' + fmtHM(desdeIso) : '';
      terminar('No encontramos nada nuevo' + desdeTxt + ' (revisado ' + fmtHM() + ').');
    });
  });

  mostrarSplash();
  cargar().then((data) => {
    // Si NO hay primera lectura en curso, el buzón ya está a la vista: marca la
    // lectura (GRACIAS) una vez. Si hay onboarding, el poller la marcará al cerrar.
    if (data && data.ok && !document.getElementById('ob-indicador')) marcarVista();
    vigilarOnboarding();
  });
})();
