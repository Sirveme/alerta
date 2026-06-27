/* ═══════════════════════════════════════════════════════════════════
   landing.js — alerta.pe (zAlerta-20 fix-2 · landing de venta)
   - Demo FUNCIONAL (como /activar): RUC → razón social → push-demo cae del
     top con ding (2 líneas: Multa + Orden de Pago) → resumen en TABLA con
     divisores + "Escuchar resumen" + "Qué hacer". Repetible.
   - Historias RTF: tarjetas separadas/tocables → overlay z-index al frente + video.
   (FAQ usa <details> nativo; modales propios en app.js.)
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const lp = document.querySelector('.lp-venta');
  if (!lp) return;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  let razonActual = '';

  function fechaMas(dias) {
    const d = new Date(); d.setDate(d.getDate() + dias);
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear();
  }

  // ── Sonido "ding" (WebAudio; tras un toque del usuario) ──
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

  // ── Web Speech: "Escuchar resumen" ──
  function hablar(t) {
    try {
      if (!('speechSynthesis' in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(t);
      u.lang = 'es-PE'; u.rate = 1; window.speechSynthesis.speak(u);
    } catch (_) {}
  }
  function resumenHablado() {
    return 'Tienes una Resolución de Multa que vence pronto y una Orden de Pago. '
      + 'Revisa los plazos para que no se te pasen.';
  }

  // ── Push-demo (cae del top, DOS líneas: Multa + Orden de Pago) ──
  function dropPush() {
    const cont = $('#act-pushsim'); if (!cont) return;
    const rs = razonActual ? '<div class="psim-rs">' + esc(razonActual) + '</div>' : '';
    cont.innerHTML =
      '<div class="psim">'
      + '<div class="psim-top"><span class="psim-app"><span class="psim-dot"></span> alerta.pe · ahora</span>'
      + '<button class="psim-x" aria-label="Cerrar">&times;</button></div>'
      + '<div class="psim-etq">Nuevas notificaciones en tu Buzón SUNAT</div>' + rs
      + '<div class="psim-card estado--urgente">'
      + '  <div class="psim-card-head"><span class="psim-tipo">Resolución de Multa</span>'
      + '    <span class="psim-urg">Vence pronto</span></div>'
      + '  <ul class="psim-det"><li><span>Vence el</span><b>' + fechaMas(2) + '</b></li></ul></div>'
      + '<div class="psim-card estado--importante" style="margin-top:8px">'
      + '  <div class="psim-card-head"><span class="psim-tipo">Orden de Pago</span>'
      + '    <span class="psim-urg psim-urg--amber">Importante</span></div>'
      + '  <ul class="psim-det"><li><span>Vence el</span><b>' + fechaMas(6) + '</b></li></ul></div>'
      + '<div class="psim-guardado"><span class="material-symbols-outlined">mobile_friendly</span> '
      + '<b>Guardado en tu celular.</b> Lo revisas cuando quieras, <b>incluso sin internet</b>.</div>'
      + '</div>';
    cont.hidden = false;
    requestAnimationFrame(() => cont.classList.add('show'));
    try { ding(); } catch (_) {}
    cont.querySelector('.psim-x').onclick = cerrarSim;
    clearTimeout(cont._t); cont._t = setTimeout(cerrarSim, 8000);
  }
  function cerrarSim() {
    const cont = $('#act-pushsim'); if (!cont) return;
    cont.classList.remove('show');
    clearTimeout(cont._t); cont._t = setTimeout(() => { cont.hidden = true; }, 400);
  }

  // ── RUC → razón social + actualiza el celular + push-demo ──
  const inRuc = $('#demo-ruc'), msg = $('#demo-msg');
  const phdRs = $('#phd-rs');
  function pintarFechas() {
    const v1 = $('#phd-v1'), v2 = $('#phd-v2');
    if (v1) v1.textContent = fechaMas(2);
    if (v2) v2.textContent = fechaMas(6);
  }
  async function lookup(ruc) {
    msg.className = 'lp-demo-msg'; msg.textContent = 'Consultando…';
    try {
      const ctrl = new AbortController();
      const corte = setTimeout(() => ctrl.abort(), 5000);
      const j = await (await fetch('/api/activar/ruc/' + ruc, { signal: ctrl.signal })).json();
      clearTimeout(corte);
      if (j.ok && j.razon_social) {
        razonActual = j.razon_social;
        if (phdRs) { phdRs.textContent = j.razon_social; phdRs.hidden = false; }
        msg.className = 'lp-demo-msg ok'; msg.textContent = '✓ ' + j.razon_social;
      } else {
        razonActual = '';
        if (phdRs) phdRs.hidden = true;
        msg.className = 'lp-demo-msg'; msg.textContent = 'Ese RUC no trajo razón social, pero así te llegaría la alerta.';
      }
    } catch (_) { msg.className = 'lp-demo-msg'; msg.textContent = 'Mira cómo te llegaría la alerta.'; }
    pintarFechas();
    dropPush();
  }
  if (inRuc) {
    inRuc.addEventListener('input', () => {
      inRuc.value = inRuc.value.replace(/\D/g, '').slice(0, 11);
      if (inRuc.value.length === 11) lookup(inRuc.value);
    });
  }
  pintarFechas();   // fechas de ejemplo desde el inicio

  // ── Modal "Así se verán tus notificaciones" (TABLA con divisores) ──
  const DEMO_FILAS = [
    { doc: 'Resolución de Multa', periodo: 'Mar-2026', detalle: 'Declarar fuera de plazo', vence: fechaMas(2),
      orient: 'SUNAT aplicó una sanción. Revisa el motivo y el plazo; evalúa si corresponde pagar (con posible gradualidad) o reclamar.' },
    { doc: 'Orden de Pago', periodo: 'Abr-2026', detalle: 'IGV no pagado', vence: fechaMas(6),
      orient: 'Indica un tributo que SUNAT considera pendiente. Revisa el periodo y el monto; si corresponde, paga antes del vencimiento para evitar intereses y cobranza coactiva.' },
    { doc: 'Requerimiento', periodo: '—', detalle: 'Sustentar gastos', vence: fechaMas(9),
      orient: 'SUNAT te pide sustentar o presentar algo. Atiende dentro del plazo indicado; reúne la documentación solicitada.' },
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
    const rsHead = razonActual
      ? '<div class="demo-rs">' + esc(razonActual) + '</div>' : '';
    panel.innerHTML =
      '<div class="demo-head"><span class="logo-selva demo-logo">alerta<i>.pe</i></span>'
      + '<button class="demo-x" aria-label="Cerrar"><span class="material-symbols-outlined">close</span></button></div>'
      + '<h3 class="demo-tit">Así se verán tus notificaciones</h3>' + rsHead
      + '<button class="demo-voz" id="demo-voz"><span class="material-symbols-outlined">volume_up</span> Escuchar resumen</button>'
      + '<div class="demo-tabla-wrap"><table class="demo-tabla"><thead><tr>'
      + '<th>Documento</th><th>Periodo</th><th>Detalle</th><th>Vence</th></tr></thead>'
      + '<tbody>' + filas + '</tbody></table></div>'
      + '<p class="demo-disclaimer"><span class="material-symbols-outlined">info</span> Orientación '
      + 'general con fines informativos. No reemplaza la asesoría de tu contador o abogado.</p>';
    wrap.hidden = false;
    requestAnimationFrame(() => wrap.classList.add('show'));
    try { ding(); } catch (_) {}
    hablar(resumenHablado());
    panel.querySelector('.demo-x').onclick = cerrarDemoModal;
    $('#act-demo-fondo').onclick = cerrarDemoModal;
    $('#demo-voz').onclick = () => hablar(resumenHablado());
    panel.querySelectorAll('.demo-b').forEach((b) => b.addEventListener('click', () => {
      const c = panel.querySelector('.demo-orient[data-orient="' + b.dataset.i + '"]');
      if (!c) return; c.textContent = DEMO_FILAS[+b.dataset.i].orient; c.hidden = !c.hidden;
    }));
  }
  function cerrarDemoModal() {
    const wrap = $('#act-demo'); if (!wrap) return;
    try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}
    wrap.classList.remove('show'); setTimeout(() => { wrap.hidden = true; }, 300);
  }
  const verRes = $('#demo-resumen');
  if (verRes) verRes.addEventListener('click', abrirDemoModal);

  // ── Historias RTF: tarjetas separadas/tocables → overlay al frente + video ──
  const ov = $('#lp-rtf-overlay'), ovPanel = $('#lp-rtf-panel');
  document.querySelectorAll('.lp-historia').forEach((h) => {
    h.addEventListener('click', () => {
      if (!ov || !ovPanel) return;
      const slug = h.dataset.slug || '';
      ovPanel.innerHTML =
        '<button class="lp-rtf-x" aria-label="Cerrar"><span class="material-symbols-outlined">close</span></button>'
        + '<div class="phone phone--video borde-chicha-1" style="width:230px;height:410px">'
        + '<div class="phone-notch"></div><div class="phone-pantalla phone-pantalla--video">'
        + '<video class="rtf-video" controls playsinline autoplay '
        + 'poster="/static/img/' + esc(slug) + '.jpg">'
        + '<source src="/static/vid/' + esc(slug) + '.mp4" type="video/mp4">'
        + '<source src="/static/vid/' + esc(slug) + '.webm" type="video/webm"></video>'
        + '<div class="lp-video-ph rtf-proximo" hidden><span class="material-symbols-outlined">movie</span>'
        + '<span>Próximamente</span></div></div></div>'
        + '<div class="lp-rtf-tit">' + esc(h.dataset.titulo || '') + '</div>';
      ov.hidden = false;
      requestAnimationFrame(() => ov.classList.add('show'));
      const vid = ovPanel.querySelector('.rtf-video'), prox = ovPanel.querySelector('.rtf-proximo');
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

  // ── Videos de precio: degradar a "próximamente" si el archivo no existe ──
  document.querySelectorAll('.lp-vprecio-player video').forEach((v) => {
    const prox = v.parentElement.querySelector('.lp-vprecio-prox');
    v.addEventListener('error', () => { v.style.display = 'none'; if (prox) prox.hidden = false; }, true);
    // <video> con solo <source> inexistente no siempre dispara 'error' en el
    // elemento; comprobamos tras intentar cargar.
    v.addEventListener('loadeddata', () => { if (prox) prox.hidden = true; });
  });

  // ── Mini-captura bajo el Video 2 (reusa /api/activar/ruc + /api/activar/lead) ──
  const capRuc = $('#cap-ruc'), capWa = $('#cap-wa'), capRs = $('#cap-rs'),
        capMsg = $('#cap-msg'), capBtn = $('#cap-enviar');
  if (capRuc && capBtn) {
    let capRazon = '';
    function mostrarRs(rs) {
      capRazon = rs || '';
      if (rs) { capRs.textContent = '✓ ' + rs; capRs.hidden = false; }
      else { capRs.hidden = true; }
    }
    async function capLookup(ruc) {
      capMsg.className = 'lp-captura-msg'; capMsg.textContent = '';
      try {
        const j = await (await fetch('/api/activar/ruc/' + ruc)).json();
        if (j.ok && j.razon_social) mostrarRs(j.razon_social); else mostrarRs('');
      } catch (_) { mostrarRs(''); }
    }
    // Pre-rellenar si el usuario ya validó un RUC arriba (demo/héroe).
    if (inRuc && /^\d{11}$/.test(inRuc.value || '')) {
      capRuc.value = inRuc.value;
      if (razonActual) mostrarRs(razonActual); else capLookup(inRuc.value);
    }
    capRuc.addEventListener('input', () => {
      capRuc.value = capRuc.value.replace(/\D/g, '').slice(0, 11);
      if (capRuc.value.length === 11) capLookup(capRuc.value); else mostrarRs('');
    });
    capWa.addEventListener('input', () => { capWa.value = capWa.value.replace(/\D/g, '').slice(0, 9); });
    capBtn.addEventListener('click', async () => {
      const ruc = (capRuc.value || '').trim(), wa = (capWa.value || '').trim();
      if (!/^\d{11}$/.test(ruc)) { capMsg.className = 'lp-captura-msg no'; capMsg.textContent = 'Escribe tu RUC de 11 dígitos.'; return; }
      if (!/^\d{8,9}$/.test(wa)) { capMsg.className = 'lp-captura-msg no'; capMsg.textContent = 'Escribe tu WhatsApp (sin código país).'; return; }
      capBtn.disabled = true;
      try {
        const j = await (await fetch('/api/activar/lead', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ruc: ruc, whatsapp: wa, razon_social: capRazon }) })).json();
        if (j.ok) {
          capMsg.className = 'lp-captura-msg ok';
          capMsg.innerHTML = '✓ ¡Precio asegurado! Continúa para conectar tu buzón. '
            + '<a href="/activar?ruc=' + encodeURIComponent(ruc) + '">Continuar ›</a>';
        } else { capBtn.disabled = false; capMsg.className = 'lp-captura-msg no'; capMsg.textContent = 'No pudimos guardar; reintenta.'; }
      } catch (_) { capBtn.disabled = false; capMsg.className = 'lp-captura-msg no'; capMsg.textContent = 'Error de red; reintenta.'; }
    });
  }
})();
