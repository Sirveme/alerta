/* ═══════════════════════════════════════════════════════════════════
   landing.js — alerta.pe (zAlerta-19 · landing de venta)
   - Demo interactiva: RUC → razón social (API) → push-demo cae del top con
     ding → modal "GUARDADO EN TU CELULAR" (tabla ejemplo + "Qué hacer").
   - Historias RTF: tocar una tarjeta la trae AL FRENTE (overlay z-index).
   (El FAQ usa <details> nativo; los modales propios viven en app.js.)
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const lp = document.querySelector('.lp-venta');
  if (!lp) return;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  let razonActual = '';

  // Fecha dd/MM/yyyy a N días desde hoy.
  function fechaMas(dias) {
    const d = new Date(); d.setDate(d.getDate() + dias);
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear();
  }

  // ── Sonido corto "ding" (WebAudio; permitido tras un toque del usuario) ──
  let audioCtx = null;
  function ding() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    audioCtx = audioCtx || new AC();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = 'sine'; o.frequency.setValueAtTime(880, audioCtx.currentTime);
    o.frequency.setValueAtTime(1320, audioCtx.currentTime + 0.09);
    g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.4);
    o.connect(g); g.connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + 0.42);
  }

  // ── Push-demo que CAE DESDE EL TOP ──
  function dropPush() {
    const cont = $('#act-pushsim');
    if (!cont) return;
    const rs = razonActual ? esc(razonActual) : 'Tu negocio';
    cont.innerHTML =
      '<div class="psim">'
      + '<div class="psim-top"><span class="psim-app"><span class="psim-dot"></span> alerta.pe · ahora</span>'
      + '<button class="psim-x" aria-label="Cerrar">&times;</button></div>'
      + '<div class="psim-etq">Nueva notificación en tu Buzón SUNAT</div>'
      + '<div class="psim-card estado--urgente">'
      + '  <div class="psim-card-head"><span class="psim-tipo">Orden de Pago</span>'
      + '    <span class="psim-urg">Urgente</span></div>'
      + '  <div class="psim-rs">' + rs + '</div>'
      + '  <ul class="psim-det"><li><span>Vence el</span><b>' + fechaMas(1) + '</b></li></ul>'
      + '</div>'
      + '<div class="psim-guardado"><i class="ti ti-device-mobile-down"></i> '
      + '<b>Guardado en tu celular.</b> Lo revisas cuando quieras, <b>incluso sin internet</b>.</div>'
      + '</div>';
    cont.hidden = false;
    requestAnimationFrame(() => cont.classList.add('show'));
    try { ding(); } catch (_) {}
    cont.querySelector('.psim-x').onclick = cerrarSim;
    clearTimeout(cont._t); cont._t = setTimeout(cerrarSim, 7000);
  }
  function cerrarSim() {
    const cont = $('#act-pushsim'); if (!cont) return;
    cont.classList.remove('show');
    clearTimeout(cont._t); cont._t = setTimeout(() => { cont.hidden = true; }, 400);
  }

  // ── RUC → razón social + push-demo ──
  const inRuc = $('#demo-ruc'), msg = $('#demo-msg');
  async function lookup(ruc) {
    msg.className = 'lp-demo-msg muted'; msg.textContent = 'Consultando…';
    try {
      const ctrl = new AbortController();
      const corte = setTimeout(() => ctrl.abort(), 5000);
      const j = await (await fetch('/api/activar/ruc/' + ruc, { signal: ctrl.signal })).json();
      clearTimeout(corte);
      if (j.ok && j.razon_social) {
        razonActual = j.razon_social;
        $('#ph-rs').textContent = j.razon_social;
        $('#ph-push-cuerpo').textContent = 'Orden de Pago · ' + j.razon_social;
        $('#ph-vence').textContent = fechaMas(1);
        msg.className = 'lp-demo-msg ok'; msg.textContent = '✓ ' + j.razon_social;
      } else {
        razonActual = '';
        msg.className = 'lp-demo-msg muted';
        msg.textContent = 'Ese RUC no trajo razón social, pero así te llegaría la alerta.';
      }
    } catch (_) { msg.className = 'lp-demo-msg muted'; msg.textContent = 'Mira cómo te llegaría la alerta.'; }
    dropPush();
  }
  if (inRuc) {
    inRuc.addEventListener('input', () => {
      inRuc.value = inRuc.value.replace(/\D/g, '').slice(0, 11);
      if (inRuc.value.length === 11) lookup(inRuc.value);
    });
  }

  // ── Modal "GUARDADO EN TU CELULAR" (tabla ejemplo, solo "Qué hacer") ──
  const DEMO_FILAS = [
    { doc: 'Orden de Pago', periodo: 'Abr-2026', detalle: 'IGV no pagado', vence: fechaMas(2),
      orient: 'Indica un tributo que SUNAT considera pendiente. Revisa el periodo y el monto; si corresponde, paga antes del vencimiento para evitar intereses y cobranza coactiva.' },
    { doc: 'Requerimiento', periodo: '—', detalle: 'Sustentar gastos', vence: fechaMas(3),
      orient: 'SUNAT te pide sustentar o presentar algo. Atiende dentro del plazo indicado; reúne la documentación solicitada.' },
    { doc: 'Resolución de Multa', periodo: 'Mar-2026', detalle: 'Declarar fuera de plazo', vence: fechaMas(5),
      orient: 'SUNAT aplicó una sanción. Revisa el motivo y el plazo; evalúa si corresponde pagar (con posible gradualidad) o reclamar.' },
    { doc: 'Aviso informativo', periodo: '—', detalle: 'Buzón actualizado', vence: '—',
      orient: 'Es un aviso general, sin acción urgente. Revísalo cuando puedas.' },
  ];
  function abrirDemoModal() {
    const wrap = $('#act-demo'), panel = $('#act-demo-panel');
    if (!wrap || !panel) return;
    const filas = DEMO_FILAS.map((f, i) =>
      '<tr><td><b>' + esc(f.doc) + '</b></td><td>' + esc(f.periodo) + '</td>'
      + '<td>' + esc(f.detalle) + '</td><td>' + esc(f.vence) + '</td></tr>'
      + '<tr class="demo-acc"><td colspan="4">'
      + '<button class="demo-b" data-i="' + i + '">Qué hacer</button>'
      + '<div class="demo-orient" data-orient="' + i + '" hidden></div></td></tr>').join('');
    panel.innerHTML =
      '<div class="demo-head"><span class="logo-selva demo-logo">alerta<i>.pe</i></span>'
      + '<button class="demo-x" aria-label="Cerrar"><i class="ti ti-x"></i></button></div>'
      + '<h3 class="demo-tit">Así se verán tus notificaciones</h3>'
      + '<div class="demo-tabla-wrap"><table class="demo-tabla"><thead><tr>'
      + '<th>Documento</th><th>Periodo</th><th>Detalle</th><th>Vence</th></tr></thead>'
      + '<tbody>' + filas + '</tbody></table></div>'
      + '<p class="demo-disclaimer"><i class="ti ti-info-circle"></i> Orientación '
      + 'general con fines informativos. No reemplaza la asesoría de tu contador o abogado.</p>';
    wrap.hidden = false;
    requestAnimationFrame(() => wrap.classList.add('show'));
    try { ding(); } catch (_) {}
    panel.querySelector('.demo-x').onclick = cerrarDemoModal;
    $('#act-demo-fondo').onclick = cerrarDemoModal;
    panel.querySelectorAll('.demo-b').forEach((b) => b.addEventListener('click', () => {
      const c = panel.querySelector('.demo-orient[data-orient="' + b.dataset.i + '"]');
      if (!c) return; c.textContent = DEMO_FILAS[+b.dataset.i].orient; c.hidden = !c.hidden;
    }));
  }
  function cerrarDemoModal() {
    const wrap = $('#act-demo'); if (!wrap) return;
    wrap.classList.remove('show'); setTimeout(() => { wrap.hidden = true; }, 300);
  }
  const probar = $('#demo-probar');
  if (probar) probar.addEventListener('click', abrirDemoModal);

  // ── Historias RTF: tocar una tarjeta la trae AL FRENTE (overlay + video) ──
  const ov = $('#lp-rtf-overlay'), ovPanel = $('#lp-rtf-panel');
  document.querySelectorAll('.lp-historia').forEach((h) => {
    h.addEventListener('click', () => {
      if (!ov || !ovPanel) return;
      const slug = h.dataset.slug || '';
      // <video> con source + poster; si el archivo no existe, onerror degrada a
      // "próximamente" (sin recuadro roto).
      ovPanel.innerHTML =
        '<button class="lp-rtf-x" aria-label="Cerrar"><span class="material-symbols-outlined">close</span></button>'
        + '<div class="phone phone--video borde-chicha-1" style="width:230px;height:410px">'
        + '<div class="phone-notch"></div><div class="phone-pantalla phone-pantalla--video">'
        + '<video class="rtf-video" controls playsinline autoplay '
        + 'poster="/static/img/' + esc(slug) + '.jpg">'
        + '<source src="/static/video/' + esc(slug) + '.mp4" type="video/mp4">'
        + '<source src="/static/video/' + esc(slug) + '.webm" type="video/webm"></video>'
        + '<div class="lp-video-ph rtf-proximo" hidden><span class="material-symbols-outlined">movie</span>'
        + '<span>Próximamente</span></div>'
        + '</div></div>'
        + '<div class="lp-rtf-tit">' + esc(h.dataset.titulo || '') + '</div>';
      ov.hidden = false;
      requestAnimationFrame(() => ov.classList.add('show'));
      // Si el video no carga (sin archivo aún), mostrar "Próximamente".
      const vid = ovPanel.querySelector('.rtf-video');
      const prox = ovPanel.querySelector('.rtf-proximo');
      if (vid) vid.addEventListener('error', () => { vid.style.display = 'none'; if (prox) prox.hidden = false; }, true);
      ovPanel.querySelector('.lp-rtf-x').onclick = cerrarRtf;
      $('#lp-rtf-fondo').onclick = cerrarRtf;
    });
  });
  function cerrarRtf() {
    if (!ov) return;
    const v = ovPanel.querySelector('video'); if (v) { try { v.pause(); } catch (_) {} }
    ov.classList.remove('show'); setTimeout(() => { ov.hidden = true; }, 300);
  }
})();
