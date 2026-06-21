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

  function render(data, offline) {
    const filas = (data && data.filas) || [];
    vacioEl.hidden = filas.length > 0;
    if (!filas.length) { wrap.innerHTML = ''; return; }
    const cuerpo = filas.map((f, i) =>
      '<tr class="rsm-row estado--' + esc(f.urgencia) + '">'
      + '<td><b>' + esc(f.documento) + '</b></td>'
      + '<td>' + esc(f.periodo) + '</td>'
      + '<td>' + esc(f.detalle) + '</td>'
      + '<td>' + esc(f.vence) + '</td>'
      + '<td><button class="rsm-b" data-i="' + i + '">Qué hacer</button></td>'
      + '</tr>'
      + '<tr class="rsm-orient-row"><td colspan="5">'
      + '<div class="rsm-orient" data-orient="' + i + '" hidden></div></td></tr>'
    ).join('');
    wrap.innerHTML =
      '<table class="rsm-tabla"><thead><tr>'
      + '<th>Documento</th><th>Periodo</th><th>Detalle</th><th>Vence</th><th></th>'
      + '</tr></thead><tbody>' + cuerpo + '</tbody></table>';
    wrap.querySelectorAll('.rsm-b').forEach((b) => b.addEventListener('click', () => {
      const cont = wrap.querySelector('.rsm-orient[data-orient="' + b.dataset.i + '"]');
      if (!cont) return;
      cont.textContent = ORIENTA[filas[+b.dataset.i].tipo] || ORIENTA.otro;
      cont.hidden = !cont.hidden;
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
        vacioEl.textContent = 'Sin conexión y aún no hay datos guardados. '
          + 'Conéctate una vez para guardarlos en tu celular.';
      }
    }
  }

  cargar();
})();
