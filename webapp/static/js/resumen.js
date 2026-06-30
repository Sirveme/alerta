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
    otro: 'Ver detalle',
  };
  const URG_LBL = {
    critica: 'Crítica', urgente: 'Urgente', importante: 'Importante',
    informativa: 'Informativa', sin_clasificar: 'Informativa',
  };

  // ── Modal custom (sin diálogos nativos): detalle + orientación + PDFs ──
  function abrirModal(f) {
    if (!f) return;
    const sem = semColor(f);
    const orienta = ORIENTA[f.tipo] || ORIENTA.otro;
    const pdfs = (f.adjuntos || []).map((a) =>
      '<div class="rsm-mod-pdf"><span class="material-symbols-outlined">picture_as_pdf</span>'
      + '<span class="rsm-mod-pdf-nom">' + esc(a.nombre || 'Documento.pdf') + '</span>'
      + '<a class="rsm-mod-btn" href="/adjuntos/' + esc(a.id) + '/ver" target="_blank" rel="noopener">Ver PDF</a>'
      + '<a class="rsm-mod-btn rsm-mod-btn--sec" href="/adjuntos/' + esc(a.id) + '/descargar">Descargar</a>'
      + '</div>').join('');
    const meta = [];
    meta.push('<span class="rsm-mod-chip sem-bg--' + sem + '">' + esc(URG_LBL[f.urgencia] || 'Informativa') + '</span>');
    if (f.periodo && f.periodo !== '—') meta.push('<span class="rsm-mod-tag">Periodo: ' + esc(f.periodo) + '</span>');
    if (f.vence_iso) meta.push('<span class="rsm-mod-tag">Vence: ' + esc(f.vence) + '</span>');
    if (f.ruc) meta.push('<span class="rsm-mod-tag">RUC ' + esc(f.ruc) + '</span>');

    const ov = document.createElement('div');
    ov.className = 'rsm-mod-ov';
    ov.innerHTML =
      '<div class="rsm-mod" role="dialog" aria-modal="true" aria-label="Detalle de la notificación">'
      + '<button class="rsm-mod-x" aria-label="Cerrar"><span class="material-symbols-outlined">close</span></button>'
      + '<div class="rsm-mod-tipo">' + esc(f.documento) + '</div>'
      + '<h3 class="rsm-mod-asunto">' + esc(f.asunto || f.detalle || 'Notificación') + '</h3>'
      + '<div class="rsm-mod-meta">' + meta.join('') + '</div>'
      + '<p class="rsm-mod-orienta">' + esc(orienta) + '</p>'
      + (pdfs
          ? '<div class="rsm-mod-pdfs"><div class="rsm-mod-lbl">Documentos adjuntos</div>' + pdfs + '</div>'
          : '<div class="rsm-mod-sinpdf">Esta notificación no tiene PDF adjunto.</div>')
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

  function render(data, offline) {
    const filas = (data && data.filas) || [];
    vacioEl.hidden = filas.length > 0;
    if (!filas.length) { wrap.innerHTML = ''; return; }
    const cuerpo = filas.map((f, i) => {
      const sem = semColor(f);
      const conPlazo = !!f.vence_iso;
      // CTA de recordatorio (solo si hay plazo) + sugerencia en rojo/ámbar.
      const recSel = conPlazo
        ? '<div class="rsm-recordar">'
          + ((sem === 'rojo' || sem === 'ambar')
              ? '<div class="rsm-sugerencia"><i class="ti ti-bell-plus"></i> '
                + 'Te sugerimos programar recordatorios</div>' : '')
          + '<span class="rsm-recordar-lbl"><i class="ti ti-bell"></i> Recuérdame</span>'
          + '<select class="rsm-rec-sel" data-id="' + esc(f.id) + '">'
          + REC_OPTS.map(([v, t]) => '<option value="' + v + '"'
              + (((f.recordatorio || '') === v) ? ' selected' : '') + '>'
              + esc(t) + '</option>').join('')
          + '</select>'
          + '<span class="rsm-rec-ok" hidden>✓ Te recordaremos</span></div>'
        : '';
      const venceCol = conPlazo
        ? 'Vence ' + esc(f.vence)
        : (sem === 'rojo'
            ? '<span class="rsm-info-lbl rsm-lbl--rojo">Urgente</span>'
            : sem === 'ambar'
              ? '<span class="rsm-info-lbl rsm-lbl--ambar">Importante</span>'
              : '<span class="rsm-info-lbl">Informativo</span>');
      const badgePdf = (f.adjuntos && f.adjuntos.length)
        ? ' <span class="rsm-pdf-badge" title="Tiene PDF adjunto">'
          + '<span class="material-symbols-outlined">picture_as_pdf</span></span>'
        : '';
      const btnLabel = BTN_LABEL[f.tipo] || BTN_LABEL.otro;
      return '<tr class="rsm-row sem--' + sem + '" title="' + esc(SEM_TXT[sem]) + '">'
        + '<td><span class="rsm-punto sem-bg--' + sem + '"></span><b>' + esc(f.documento) + '</b>' + badgePdf + '</td>'
        + '<td>' + esc(f.periodo) + '</td>'
        + '<td>' + esc(f.detalle) + '</td>'
        + '<td>' + venceCol + '</td>'
        + '<td><button class="rsm-b" data-i="' + i + '">' + esc(btnLabel) + '</button></td>'
        + '</tr>'
        + (recSel
            ? '<tr class="rsm-orient-row sem--' + sem + '"><td colspan="5">' + recSel + '</td></tr>'
            : '');
    }).join('');
    wrap.innerHTML =
      '<table class="rsm-tabla"><thead><tr>'
      + '<th>Documento</th><th>Periodo</th><th>Detalle</th><th>Vence</th><th></th>'
      + '</tr></thead><tbody>' + cuerpo + '</tbody></table>';
    wrap.querySelectorAll('.rsm-b').forEach((b) => b.addEventListener('click', () => {
      abrirModal(filas[+b.dataset.i]);
    }));
    // Cambio de recordatorio → guardar (no disponible offline).
    wrap.querySelectorAll('.rsm-rec-sel').forEach((s) => s.addEventListener('change', async () => {
      const ok = s.parentElement.querySelector('.rsm-rec-ok');
      try {
        const r = await fetch('/api/recordatorio', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ notificacion_id: s.dataset.id, modo: s.value || null }) });
        const j = await r.json();
        if (j.ok && ok) {
          ok.textContent = s.value ? '✓ Te recordaremos' : 'Recordatorio desactivado';
          ok.hidden = false; setTimeout(() => { ok.hidden = true; }, 2500);
        }
      } catch (_) { /* sin red: el cambio no persiste */ }
    }));
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
      guardar(online);                          // background: queda offline
      // GRACIAS dentro de la app (métrica de lectura).
      fetch('/api/alerta/vista', { method: 'POST', credentials: 'include' }).catch(() => {});
    } else {
      const cache = await leerCache();
      if (cache) {
        render(cache, true);
        pintarEstado('Sin conexión · datos guardados', 'off');
      } else {
        pintarEstado('Sin conexión', 'off');
        vacioEl.hidden = false;
        const txt = document.getElementById('rsm-vacio-txt');
        if (txt) txt.textContent = 'Sin conexión y aún no hay datos guardados. '
          + 'Conéctate una vez para guardarlos en tu celular.';
      }
    }
  }

  // ── Mini-leyenda del semáforo (siempre visible, discreta) ──
  function leyendaHTML() {
    return '<div class="rsm-leyenda">'
      + '<span><span class="rsm-punto sem-bg--rojo"></span> Urgente</span>'
      + '<span><span class="rsm-punto sem-bg--ambar"></span> Tiene plazo</span>'
      + '<span><span class="rsm-punto sem-bg--verde"></span> Informativo</span>'
      + '</div>';
  }

  // ── Splash al entrar DESDE el push (zAlerta-17 P3), una vez por sesión ──
  function mostrarSplash() {
    const desdePush = new URLSearchParams(location.search).get('from') === 'push';
    const cont = document.getElementById('rsm');
    if (!cont) return;
    // La mini-leyenda va siempre; el splash solo si vino del push.
    if (desdePush && !sessionStorage.getItem('rsm_splash')) {
      sessionStorage.setItem('rsm_splash', '1');
      const s = document.createElement('div');
      s.className = 'rsm-splash';
      s.innerHTML = '<button class="rsm-splash-x" aria-label="Cerrar">&times;</button>'
        + '<div class="rsm-splash-tit"><i class="ti ti-device-mobile-check"></i> '
        + 'Esto es lo que dejamos en tu celular</div>'
        + '<p class="rsm-splash-txt">Revisa con calma; queda guardado aquí, '
        + 'incluso sin internet.</p>' + leyendaHTML();
      cont.insertBefore(s, cont.firstChild);
      s.querySelector('.rsm-splash-x').onclick = () => s.remove();
    }
    // Leyenda persistente sobre la tabla.
    const ley = document.createElement('div');
    ley.innerHTML = leyendaHTML();
    wrap.parentNode.insertBefore(ley.firstChild, wrap);
  }

  // ── "Actualizar ahora" (zAlerta-36): dispara la lectura de día ──
  // Reusa POST /contribuyentes/{id}/actualizar (marca el flag; el worker corre
  // fuera del gate nocturno). Muestra el indicador estilizado y, en cuanto el
  // buzón crece, lo refresca. Sin diálogos nativos.
  function totalActual() {
    return wrap.querySelectorAll('.rsm-row').length;
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

    // Poll hasta que el buzón crezca (o tope ~3 min). El worker corre por ciclos.
    const antes = totalActual();
    let intentos = 0;
    const tope = 30;
    const timer = setInterval(async () => {
      intentos += 1;
      await cargar();
      if (totalActual() > antes || intentos >= tope) {
        clearInterval(timer);
        terminar(totalActual() > antes ? 'Buzón actualizado' : 'Listo, sin novedades por ahora');
      }
    }, 6000);
  });

  mostrarSplash();
  cargar();
})();
