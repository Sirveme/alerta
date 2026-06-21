/* ═══════════════════════════════════════════════════════════════════
   activar.js — alerta.pe (zAlerta-11bb · alta del empresario)
   - RUC con inteligencia (DNI si 10, EIRL) + razón social (API con timeout).
   - WhatsApp temprano → lead recuperable.
   - Clave SOL: validación SILENCIOSA al blur (sin botón). Celebra si conecta.
   - "PRUEBA LAS NOTIFICACIONES": simulación de push que cae desde el top + ding.
   Modales propios desde app.js (cargado antes en base.html).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const raiz = document.getElementById('act');
  if (!raiz) return;
  const WA_SOPORTE = raiz.dataset.waSoporte || '';

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const estado = {
    ruc: '', razon_social: '', tiene_clave: null,
    usuario_sol: '', clave_sol: '',
    conexion_verificada: false,   // ✔ real (login verificado) — BUG 1
    whatsapp: '',                 // número local (sin 51); el server antepone 51
  };

  // ── B.1 · RUC con inteligencia ──
  const inRuc = $('#act-ruc');
  const okBox = $('#act-ruc-ok');
  const rsEl = $('#act-rs');
  const intelEl = $('#act-intel');
  const rsEdit = $('#act-rs-edit');
  const rucMsg = $('#act-ruc-msg');

  function intelRuc(ruc, razon) {
    if (ruc.startsWith('10')) {
      // RUC 10 = 10 + DNI(8) + verificador(1): el DNI son los 8 centrales.
      return 'Persona natural con negocio · DNI ' + ruc.slice(2, 10);
    }
    if (ruc.startsWith('20')) {
      const rs = (razon || '').toUpperCase();
      if (rs.includes('E.I.R.L') || rs.includes('EIRL')) return 'Empresa con titular único (E.I.R.L.)';
      return 'Persona jurídica';
    }
    return '';
  }

  // BUG 2: si la API RUC no responde, NO bloquear; razón social editable a mano.
  function modoManual(ruc, aviso) {
    rsEdit.hidden = false; rsEdit.value = estado.razon_social || '';
    rucMsg.textContent = aviso ||
      'No pudimos traer la razón social automáticamente, escríbela tú.';
    rucMsg.className = 'act-estado warn';
    const intel = intelRuc(ruc, '');   // el DNI (RUC 10) NO depende de la API
    if (intel) { rsEl.textContent = ''; intelEl.textContent = intel; okBox.hidden = false; }
  }

  async function lookupRuc(ruc) {
    estado.ruc = ruc;
    guardarLead();                      // B: lead apenas hay RUC válido
    rucMsg.className = 'act-estado muted'; rucMsg.textContent = 'Consultando…';
    okBox.hidden = true; rsEdit.hidden = true;
    let j = null;
    try {
      const ctrl = new AbortController();
      const corte = setTimeout(() => ctrl.abort(), 5000);  // BUG 2: corte cliente
      const r = await fetch('/api/activar/ruc/' + ruc, { signal: ctrl.signal });
      clearTimeout(corte);
      j = await r.json();
    } catch (_) { j = null; }

    if (!j || !j.ok) { modoManual(ruc, 'No pudimos consultar el RUC ahora; escribe tu razón social.'); estado.razon_social = ''; return; }
    if (j.razon_social) {
      estado.razon_social = j.razon_social;
      rsEl.textContent = j.razon_social;
      intelEl.textContent = intelRuc(ruc, j.razon_social);
      okBox.hidden = false; rucMsg.textContent = '';
      guardarLead();
      dropPush();          // A: push-demo al validar el RUC (valor temprano)
    } else {
      estado.razon_social = '';
      modoManual(ruc, null);
    }
    actualizarMensajeContador();
  }

  inRuc.addEventListener('input', () => {
    inRuc.value = inRuc.value.replace(/\D/g, '').slice(0, 11);
    if (inRuc.value.length === 11) lookupRuc(inRuc.value);
    else { okBox.hidden = true; estado.ruc = inRuc.value; }
  });
  rsEdit.addEventListener('input', () => { estado.razon_social = rsEdit.value; actualizarMensajeContador(); });

  // ── B · WhatsApp temprano + lead recuperable ──
  const inWa = $('#act-wa');
  let leadTimer = null;
  function guardarLead() {
    if (!/^\d{11}$/.test(estado.ruc)) return;
    clearTimeout(leadTimer);
    leadTimer = setTimeout(() => {
      fetch('/api/activar/lead', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ruc: estado.ruc, whatsapp: estado.whatsapp,
          razon_social: estado.razon_social }) }).catch(() => {});
    }, 400);
  }
  inWa.addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 9);
    estado.whatsapp = e.target.value;
  });
  inWa.addEventListener('blur', guardarLead);

  // ── C · Bifurcación clave SOL ──
  document.querySelectorAll('.act-bifurca [data-clave]').forEach((b) => {
    b.addEventListener('click', () => {
      const v = b.dataset.clave;
      estado.tiene_clave = (v === 'si');
      document.querySelectorAll('[data-clave]').forEach((o) => o.classList.toggle('sel', o === b));
      $('#rama-si').hidden = (v !== 'si');
      $('#rama-no').hidden = (v !== 'no');
      if (v === 'no') actualizarMensajeContador();
    });
  });

  const inUser = $('#act-user');
  const inClave = $('#act-clave');
  const celebra = $('#act-celebra');

  // Editar credenciales invalida una verificación previa y oculta la celebración.
  function resetVerif() {
    estado.conexion_verificada = false;
    if (celebra) { celebra.hidden = true; celebra.classList.remove('show'); }
  }
  inUser.addEventListener('input', (e) => { estado.usuario_sol = e.target.value.trim(); resetVerif(); });
  inClave.addEventListener('input', (e) => { estado.clave_sol = e.target.value; resetVerif(); });

  // ── C · Validación SILENCIOSA en background al BLUR (sin botón) ──
  let valToken = 0;
  function lanzarValidacion() {
    if (!/^\d{11}$/.test(estado.ruc) || !estado.usuario_sol || !estado.clave_sol) return;
    const token = ++valToken;          // descarta resultados de intentos viejos
    estado.conexion_verificada = false;
    fetch('/api/comprobar-credenciales', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ruc: estado.ruc, usuario_sol: estado.usuario_sol, clave_sol: estado.clave_sol }) })
      .then((r) => r.json()).then((j) => {
        if (!j.ok || token !== valToken) return;
        let n = 0;
        const t = setInterval(async () => {
          n++;
          if (token !== valToken) { clearInterval(t); return; }   // reemplazada
          let est; try { est = await (await fetch('/api/comprobar-credenciales/' + j.id)).json(); } catch (_) { est = null; }
          if (est && est.ok && est.listo) {
            clearInterval(t);
            if (est.estado === 'conecta' && token === valToken) {
              estado.conexion_verificada = true;
              celebrar();              // SOLO si conecta de verdad (BUG 1)
            }
            // Si NO conecta: silencio (no frustrar). Se puede guardar igual.
          } else if (n >= 6) { clearInterval(t); }  // E: límite ~30s, sin error
        }, 5000);
      }).catch(() => {});              // BUG 3: fallo de red → silencio
  }
  inClave.addEventListener('blur', lanzarValidacion);
  inUser.addEventListener('blur', () => { if (estado.clave_sol) lanzarValidacion(); });

  function celebrar() {
    if (!celebra) return;
    celebra.innerHTML = '<i class="ti ti-confetti"></i>'
      + '<div><strong>¡Felicitaciones!</strong> Desde hoy podrás recibir las '
      + 'notificaciones de tu Buzón SUNAT.</div>';
    celebra.hidden = false;
    requestAnimationFrame(() => celebra.classList.add('show'));
    try { ding(); } catch (_) {}
  }

  // ── Rama "Pido al contador" · mensaje editable ──
  function linkRegistro() {
    const u = new URL('/registro', location.origin);
    u.searchParams.set('tipo', 'estudio');
    if (estado.ruc) u.searchParams.set('ruc', estado.ruc);
    if (estado.razon_social) u.searchParams.set('empresario', estado.razon_social);
    return u.toString();
  }
  function textoMensaje() {
    const link = linkRegistro();
    let m = 'Por S/5 me avisarán todos los días los avisos de SUNAT y SUNAFIL. '
      + 'Pásame URGENTE mis credenciales SOL, o regístrame tú aquí: ' + link + '.';
    if (!$('#act-quitar-gratis').checked) m += ' Tú también tendrás cuenta, GRATIS.';
    return m;
  }
  function actualizarMensajeContador() {
    const cont = $('#act-mensaje');
    if (!cont) return;
    const link = linkRegistro();
    let html = 'Por S/5 me avisarán todos los días los avisos de SUNAT y SUNAFIL. '
      + 'Pásame <b class="hl">URGENTE</b> mis credenciales SOL, o regístrame tú '
      + '<a href="' + esc(link) + '" target="_blank" rel="noopener">aquí</a>.';
    if (!$('#act-quitar-gratis').checked) html += ' Tú también tendrás cuenta, <b class="hl">GRATIS</b>.';
    cont.innerHTML = html;
  }
  $('#act-quitar-gratis').addEventListener('change', actualizarMensajeContador);
  $('#act-enviar-contador').addEventListener('click', () => {
    let num = ($('#act-wa-contador').value || '').replace(/\D/g, '');
    if (num.length < 8) { confirmarModal('WhatsApp del contador', 'Escribe el número de tu contador (sin el código país).', () => {}); return; }
    if (!num.startsWith('51')) num = '51' + num;   // anteponer 51 internamente
    window.open('https://wa.me/' + num + '?text=' + encodeURIComponent(textoMensaje()), '_blank', 'noopener');
  });

  // ── D · "PRUEBA LAS NOTIFICACIONES" — simulación que CAE DESDE EL TOP ──
  let audioCtx = null;
  function ding() {
    // Sonido corto generado (sin assets). Permitido porque viene de un toque.
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

  // Fecha dd/MM/yyyy a N días desde hoy (para que la demo se vea actual).
  function fechaMas(dias) {
    const d = new Date(); d.setDate(d.getDate() + dias);
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear();
  }

  // ── A · Push-demo que CAE DESDE EL TOP (toast en la página, identidad alerta.pe) ──
  function dropPush() {
    const cont = $('#act-pushsim');
    if (!cont) return;
    cont.innerHTML =
      '<div class="psim">'
      + '<div class="psim-top"><span class="psim-app"><span class="psim-dot"></span> alerta.pe · ahora</span>'
      + '<button class="psim-x" aria-label="Cerrar">&times;</button></div>'
      + '<div class="psim-etq">Nueva notificación en tu Buzón SUNAT</div>'
      + '<div class="psim-card estado--urgente">'
      + '  <div class="psim-card-head"><span class="psim-tipo">Orden de Pago</span>'
      + '    <span class="psim-urg">Urgente</span></div>'
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
    const cont = $('#act-pushsim');
    if (!cont) return;
    cont.classList.remove('show');
    clearTimeout(cont._t);
    cont._t = setTimeout(() => { cont.hidden = true; }, 400);
  }

  // ── B · Modal grande interactivo: tabla de notificaciones-ejemplo ──
  // Datos de JUGUETE claramente ilustrativos (identidad alerta.pe, NO SUNAT).
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

  function resumenHablado() {
    return 'Tienes una Orden de Pago que vence pronto, un requerimiento por '
      + 'sustentar, y una resolución de multa. Revisa los plazos para que no se '
      + 'te pasen.';
  }
  function hablar(texto) {
    try {
      if (!('speechSynthesis' in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(texto);
      u.lang = 'es-PE'; u.rate = 1; u.pitch = 1;
      window.speechSynthesis.speak(u);
    } catch (_) {}
  }

  function abrirDemoModal() {
    const wrap = $('#act-demo'); const panel = $('#act-demo-panel');
    if (!wrap || !panel) return;
    const filas = DEMO_FILAS.map((f, i) =>
      '<tr><td><b>' + esc(f.doc) + '</b></td><td>' + esc(f.periodo) + '</td>'
      + '<td>' + esc(f.detalle) + '</td><td>' + esc(f.vence) + '</td></tr>'
      + '<tr class="demo-acc"><td colspan="4">'
      + '<button class="demo-b" data-i="' + i + '">Qué hacer</button>'
      + '<div class="demo-orient" data-orient="' + i + '" hidden></div>'
      + '</td></tr>').join('');
    panel.innerHTML =
      '<div class="demo-head"><span class="logo-selva demo-logo">alerta<i>.pe</i></span>'
      + '<button class="demo-x" aria-label="Cerrar"><i class="ti ti-x"></i></button></div>'
      + '<h3 class="demo-tit">Así se verán tus notificaciones</h3>'
      + '<button class="demo-voz" id="demo-voz"><i class="ti ti-volume"></i> Escuchar resumen</button>'
      + '<div class="demo-tabla-wrap"><table class="demo-tabla"><thead><tr>'
      + '<th>Documento</th><th>Periodo</th><th>Detalle</th><th>Vence</th></tr></thead>'
      + '<tbody>' + filas + '</tbody></table></div>'
      + '<p class="demo-disclaimer"><i class="ti ti-info-circle"></i> Orientación '
      + 'general con fines informativos. No reemplaza la asesoría de tu contador '
      + 'o abogado.</p>';
    wrap.hidden = false;
    requestAnimationFrame(() => wrap.classList.add('show'));
    try { ding(); } catch (_) {}
    hablar(resumenHablado());                         // resumen hablado al abrir

    panel.querySelector('.demo-x').onclick = cerrarDemoModal;
    $('#act-demo-fondo').onclick = cerrarDemoModal;
    $('#demo-voz').onclick = () => hablar(resumenHablado());
    panel.querySelectorAll('.demo-b').forEach((b) => b.addEventListener('click', () => {
      const cont = panel.querySelector('.demo-orient[data-orient="' + b.dataset.i + '"]');
      if (!cont) return;
      cont.textContent = DEMO_FILAS[+b.dataset.i].orient;
      cont.hidden = !cont.hidden;
    }));
  }
  function cerrarDemoModal() {
    const wrap = $('#act-demo');
    if (!wrap) return;
    try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}
    wrap.classList.remove('show');
    setTimeout(() => { wrap.hidden = true; }, 300);
  }
  $('#act-prueba').addEventListener('click', abrirDemoModal);

  // ── Activar ──
  $('#act-activar').addEventListener('click', async (e) => {
    if (!/^\d{11}$/.test(estado.ruc)) { confirmarModal('Falta tu RUC', 'Escribe tu RUC de 11 dígitos.', () => {}); return; }
    // C: el WhatsApp es obligatorio (es el activo para no perder el lead).
    if (!/^\d{8,9}$/.test(estado.whatsapp || '')) {
      confirmarModal('Falta tu WhatsApp', 'Necesitamos tu WhatsApp para avisarte.', () => {});
      try { inWa.focus(); } catch (_) {}
      return;
    }
    if (estado.tiene_clave === null) { confirmarModal('¿Tienes Clave SOL?', 'Elige «Sí, la tengo» o «Pido al contador».', () => {}); return; }
    if (estado.tiene_clave && (!estado.usuario_sol || !estado.clave_sol)) {
      confirmarModal('Faltan credenciales', 'Ingresa tu usuario y clave SOL, o elige «Pido al contador».', () => {}); return;
    }
    // P3: declaración de responsabilidad obligatoria.
    const respChk = $('#act-resp-chk');
    if (!respChk || !respChk.checked) {
      confirmarModal('Falta tu declaración',
        'Marca la casilla de responsabilidad para activar.', () => {});
      const box = $('#act-resp'); if (box) box.classList.add('act-resp--falta');
      return;
    }
    const btn = e.currentTarget; const ctaHTML = btn.innerHTML; btn.disabled = true;
    btn.innerHTML = '<span class="lp-cta-resp">Activando…</span>';
    try {
      const j = await (await fetch('/api/activar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ruc: estado.ruc, razon_social: estado.razon_social,
          tiene_clave: estado.tiene_clave, usuario_sol: estado.usuario_sol,
          clave_sol: estado.clave_sol,
          conexion_verificada: estado.conexion_verificada,
          whatsapp: estado.whatsapp, responsabilidad: true }) })).json();
      if (j.ok) { location.href = j.redirect || '/'; }
      else { btn.disabled = false; btn.innerHTML = ctaHTML; confirmarModal('No se pudo activar', j.error || 'Inténtalo de nuevo.', () => {}); }
    } catch (_) { btn.disabled = false; btn.innerHTML = ctaHTML; confirmarModal('Error', 'Error de red al activar.', () => {}); }
  });

  // ── FOMO repetible (patrón del Colegio): toast flotante, reaparece ──
  (function fomo() {
    const caja = $('#act-fomo');
    if (!caja) return;
    const mensajes = [
      { i: 'ti-bolt', t: 'Un negocio de Iquitos acaba de activar sus alertas.' },
      { i: 'ti-file-alert', t: 'Hoy SUNAT notificó a cientos de contribuyentes.' },
      { i: 'ti-building-store', t: 'Una bodega de Tarapoto activó sus alertas hace minutos.' },
      { i: 'ti-receipt-tax', t: 'SUNAFIL emitió nuevas notificaciones esta semana.' },
      { i: 'ti-clock-exclamation', t: 'Un contribuyente evitó una multa avisado a tiempo.' },
    ];
    let k = 0;
    function mostrar() {
      const m = mensajes[k % mensajes.length]; k++;
      caja.innerHTML = '<i class="ti ' + m.i + '"></i><span>' + esc(m.t) + '</span>'
        + '<button class="act-fomo-x" aria-label="Cerrar">&times;</button>';
      caja.hidden = false;
      requestAnimationFrame(() => caja.classList.add('show'));
      caja.querySelector('.act-fomo-x').onclick = ocultar;
      clearTimeout(caja._t); caja._t = setTimeout(ocultar, 6000);
    }
    function ocultar() {
      caja.classList.remove('show');
      clearTimeout(caja._t);
      caja._t = setTimeout(() => { caja.hidden = true; }, 350);
    }
    setTimeout(function ciclo() { mostrar(); caja._ciclo = setTimeout(ciclo, 28000); }, 9000);
  })();

  // Pre-cargar RUC si vino por query (?ruc=).
  if (raiz.dataset.rucPre && /^\d{11}$/.test(raiz.dataset.rucPre)) {
    inRuc.value = raiz.dataset.rucPre; lookupRuc(raiz.dataset.rucPre);
  }
})();
