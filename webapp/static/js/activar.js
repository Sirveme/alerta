/* ═══════════════════════════════════════════════════════════════════
   activar.js — alerta.pe (zAlerta-11a · alta del empresario)
   Inteligencia del RUC + bifurcación clave SOL + canal privado + activar.
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
    usuario_sol: '', clave_sol: '', cargo: '',
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

  async function lookupRuc(ruc) {
    estado.ruc = ruc;
    rucMsg.className = 'act-estado muted'; rucMsg.textContent = 'Consultando…';
    okBox.hidden = true; rsEdit.hidden = true;
    try {
      const j = await (await fetch('/api/ruc/' + ruc)).json();
      if (!j.ok) { rucMsg.textContent = j.error || 'RUC inválido.'; rucMsg.className = 'act-estado err'; return; }
      estado.razon_social = j.razon_social || '';
      if (j.razon_social) {
        rsEl.textContent = j.razon_social;
        intelEl.textContent = intelRuc(ruc, j.razon_social);
        okBox.hidden = false; rucMsg.textContent = '';
      } else {
        // Sin datos de la API: dejar editar la razón social a mano (no bloquea).
        rsEdit.hidden = false; rsEdit.value = '';
        intelEl.textContent = '';
        rucMsg.textContent = 'No trajo la razón social — escríbela a mano.';
        rucMsg.className = 'act-estado warn';
        const intel = intelRuc(ruc, '');
        if (intel) { rsEl.textContent = ''; intelEl.textContent = intel; okBox.hidden = false; }
      }
      actualizarMensajeContador();
    } catch (_) {
      rucMsg.textContent = 'No se pudo consultar ahora; puedes seguir.';
      rucMsg.className = 'act-estado warn';
      rsEdit.hidden = false;
    }
  }

  inRuc.addEventListener('input', () => {
    inRuc.value = inRuc.value.replace(/\D/g, '').slice(0, 11);
    if (inRuc.value.length === 11) lookupRuc(inRuc.value);
    else { okBox.hidden = true; }
  });
  rsEdit.addEventListener('input', () => { estado.razon_social = rsEdit.value; actualizarMensajeContador(); });

  // ── B.2 · Bifurcación clave SOL ──
  document.querySelectorAll('.act-opt').forEach((b) => {
    b.addEventListener('click', () => {
      const v = b.dataset.clave;
      estado.tiene_clave = (v === 'si');
      document.querySelectorAll('.act-opt').forEach((o) => o.classList.toggle('sel', o === b));
      $('#rama-si').hidden = (v !== 'si');
      $('#rama-no').hidden = (v !== 'no');
      if (v === 'no') actualizarMensajeContador();
    });
  });

  $('#act-user').addEventListener('input', (e) => { estado.usuario_sol = e.target.value; });
  $('#act-clave').addEventListener('input', (e) => { estado.clave_sol = e.target.value; });
  $('#act-cargo').addEventListener('change', (e) => { estado.cargo = e.target.value; });

  // ── Comprobar conexión (público, polling) ──
  const conx = $('#act-conexion');
  function pintarConx(cls, txt) { conx.className = 'act-conexion ' + cls; conx.innerHTML = txt; }
  $('#act-comprobar').addEventListener('click', async (e) => {
    if (!estado.usuario_sol || !estado.clave_sol) { pintarConx('no', '✗ Ingresa usuario y clave'); return; }
    if (!/^\d{11}$/.test(estado.ruc)) { pintarConx('no', '✗ Ingresa primero tu RUC'); return; }
    const btn = e.currentTarget; btn.disabled = true;
    pintarConx('run', '<i class="ti ti-loader-2"></i> comprobando…');
    let id;
    try {
      const j = await (await fetch('/api/comprobar-credenciales', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ruc: estado.ruc, usuario_sol: estado.usuario_sol, clave_sol: estado.clave_sol }) })).json();
      if (!j.ok) throw new Error();
      id = j.id;
    } catch (_) { pintarConx('no', '✗ No se pudo iniciar'); btn.disabled = false; return; }
    let n = 0;
    const t = setInterval(async () => {
      n++;
      let est; try { est = await (await fetch('/api/comprobar-credenciales/' + id)).json(); } catch (_) { est = null; }
      if (est && est.ok && est.listo) {
        clearInterval(t); btn.disabled = false;
        if (est.estado === 'conecta') pintarConx('ok', '✓ Conecta');
        else if (est.estado === 'no_conecta') pintarConx('no', '✗ No conecta, revisa la clave');
        else pintarConx('no', '✗ No se pudo comprobar ahora');
      } else if (n >= 18) { clearInterval(t); btn.disabled = false; pintarConx('run', 'Sigue comprobando…'); }
    }, 5000);
  });

  // ── Rama NO · mensaje al contador editable ──
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
    const num = $('#act-wa-contador').value.replace(/\D/g, '');
    if (num.length < 9) { confirmarModal('WhatsApp del contador', 'Ingresa el número con código país (ej. 51XXXXXXXXX).', () => {}); return; }
    window.open('https://wa.me/' + num + '?text=' + encodeURIComponent(textoMensaje()), '_blank', 'noopener');
  });

  // ── B.3 · Canal privado: "Compruébalo" (demo de notificación local) ──
  $('#act-compruebalo').addEventListener('click', async () => {
    const msg = $('#act-push-msg');
    if (!('Notification' in window)) { msg.textContent = 'Tu navegador no soporta avisos.'; return; }
    try {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') { msg.textContent = 'Activa los avisos para sentir cómo te llegará.'; msg.className = 'act-estado warn'; return; }
      const opts = { body: 'Orden de Pago · Periodo 2026-04 · IGV', icon: '/static/img/favicon.svg', tag: 'alertape-demo' };
      if ('serviceWorker' in navigator) {
        const reg = await navigator.serviceWorker.ready;
        reg.showNotification('Nueva notificación de SUNAT', opts);
      } else { new Notification('Nueva notificación de SUNAT', opts); }
      msg.textContent = '✓ Así te llegará cada aviso, al instante.'; msg.className = 'act-estado ok';
    } catch (_) { msg.textContent = 'No se pudo mostrar el aviso de prueba.'; }
  });

  // ── B.6 · Activar ──
  $('#act-activar').addEventListener('click', async (e) => {
    if (!/^\d{11}$/.test(estado.ruc)) { confirmarModal('Falta tu RUC', 'Escribe tu RUC de 11 dígitos.', () => {}); return; }
    if (estado.tiene_clave === null) { confirmarModal('¿Tienes Clave SOL?', 'Elige «Sí, la tengo» o «No la tengo».', () => {}); return; }
    if (estado.tiene_clave && (!estado.usuario_sol || !estado.clave_sol)) {
      confirmarModal('Faltan credenciales', 'Ingresa tu usuario y clave SOL, o elige «No la tengo».', () => {}); return;
    }
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = 'Activando…';
    try {
      const j = await (await fetch('/api/activar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ruc: estado.ruc, razon_social: estado.razon_social,
          tiene_clave: estado.tiene_clave, usuario_sol: estado.usuario_sol,
          clave_sol: estado.clave_sol, cargo: estado.cargo }) })).json();
      if (j.ok) { location.href = j.redirect || '/'; }
      else { btn.disabled = false; btn.textContent = 'Activar mis alertas'; confirmarModal('No se pudo activar', j.error || 'Inténtalo de nuevo.', () => {}); }
    } catch (_) { btn.disabled = false; btn.textContent = 'Activar mis alertas'; confirmarModal('Error', 'Error de red al activar.', () => {}); }
  });

  // Pre-cargar RUC si vino por query (?ruc=).
  if (raiz.dataset.rucPre && /^\d{11}$/.test(raiz.dataset.rucPre)) {
    inRuc.value = raiz.dataset.rucPre; lookupRuc(raiz.dataset.rucPre);
  }
})();
