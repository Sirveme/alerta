/* ═══════════════════════════════════════════════════════════════════
   mi-cuenta.js — alerta.pe (zAlerta-22)
   Tablero "Mi Cuenta". REUSA los endpoints existentes (no crea lógica nueva):
     - conectar/actualizar buzón: /contribuyentes/{id}/cred/validar + /cred/guardar
       + polling /contribuyentes/validar-credenciales/{id}.
     - "que las ponga mi contador": wa.me con link /registro?ruc=…
     - desconectar: /contribuyentes/{id}/desconectar.
   Modales propios (app.js); sin diálogos nativos.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const mc = document.getElementById('mc');
  if (!mc) return;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // ── Acción principal: scroll suave al bloque conectar ──
  document.querySelectorAll('[data-scroll="conectar"]').forEach((a) => {
    a.addEventListener('click', (e) => {
      const t = $('#conectar');
      if (t) { e.preventDefault(); t.scrollIntoView({ behavior: 'smooth', block: 'start' });
        const u = $('#mc-user'); if (u) setTimeout(() => u.focus(), 350); }
    });
  });

  const bloque = $('#conectar');
  if (!bloque) return;
  const cid = bloque.dataset.cid, ruc = bloque.dataset.ruc, rs = bloque.dataset.rs || '';

  // ── Tabs (mis credenciales / mi contador) ──
  document.querySelectorAll('.mc-tab').forEach((t) => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.mc-tab').forEach((o) => o.classList.toggle('sel', o === t));
      const v = t.dataset.tab;
      $('#mc-rama-mias').hidden = (v !== 'mias');
      $('#mc-rama-contador').hidden = (v !== 'contador');
      if (v === 'contador') pintarMensaje();
    });
  });

  // ── a) Conectar con mis credenciales (validar al blur → guardar al conectar) ──
  const user = $('#mc-user'), clave = $('#mc-clave'), conx = $('#mc-conx');
  function pinta(cls, txt) { conx.className = 'mc-conx ' + (cls || ''); conx.innerHTML = txt || ''; }
  let token = 0;
  async function conectar() {
    const u = (user.value || '').trim(), c = clave.value;
    if (!u || !c) return;
    const t = ++token;
    pinta('run', '<span class="material-symbols-outlined">sync</span> Comprobando tu conexión…');
    let sid;
    try {
      const j = await (await fetch('/contribuyentes/' + cid + '/cred/validar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ usuario_sol: u, clave_sol: c }) })).json();
      if (!j.ok) throw new Error(); sid = j.id;
    } catch (_) { pinta('no', 'No pudimos comprobar ahora; reintenta.'); return; }
    let n = 0;
    const iv = setInterval(async () => {
      n++;
      if (t !== token) { clearInterval(iv); return; }
      let est; try { est = await (await fetch('/contribuyentes/validar-credenciales/' + sid)).json(); } catch (_) { est = null; }
      if (est && est.ok && est.listo) {
        clearInterval(iv);
        if (est.estado === 'conecta') {
          try {
            await fetch('/contribuyentes/' + cid + '/cred/guardar', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ usuario_sol: u, clave_sol: c }) });
            pinta('ok', '<span class="material-symbols-outlined">check_circle</span> ¡Conectado! Tu buzón ya está vigilado.');
            setTimeout(() => location.reload(), 1500);   // el estado de arriba pasa a VIGILADO
          } catch (_) { pinta('no', 'Conectó pero no se pudo guardar; reintenta.'); }
        } else {
          pinta('no', '<span class="material-symbols-outlined">error</span> No conecta. Revisa la clave y reintenta (no borramos la anterior).');
        }
      } else if (n >= 6) { clearInterval(iv); pinta('run', 'La red está lenta. Reintenta en un momento.'); }
    }, 5000);
  }
  if (clave) clave.addEventListener('blur', conectar);
  if (user) user.addEventListener('blur', () => { if (clave.value) conectar(); });

  // ── b) Que las ponga mi contador (wa.me con link /registro?ruc=) ──
  function linkRegistro() {
    const u = new URL('/registro', location.origin);
    u.searchParams.set('tipo', 'estudio');
    if (ruc) u.searchParams.set('ruc', ruc);
    if (rs) u.searchParams.set('empresario', rs);
    return u.toString();
  }
  function mensajeTexto() {
    let m = 'Por S/5 me avisarán todos los días los avisos de SUNAT y SUNAFIL. '
      + 'Pásame URGENTE mis credenciales SOL, o regístrame tú aquí: ' + linkRegistro() + '.';
    if (!$('#mc-quitar-gratis').checked) m += ' Tú también tendrás cuenta, GRATIS.';
    return m;
  }
  function pintarMensaje() {
    const cont = $('#mc-mensaje'); if (!cont) return;
    let html = 'Por S/5 me avisarán todos los días los avisos de SUNAT y SUNAFIL. '
      + 'Pásame <b class="hl">URGENTE</b> mis credenciales SOL, o regístrame tú '
      + '<a href="' + esc(linkRegistro()) + '" target="_blank" rel="noopener">aquí</a>.';
    if (!$('#mc-quitar-gratis').checked) html += ' Tú también tendrás cuenta, <b class="hl">GRATIS</b>.';
    cont.innerHTML = html;
  }
  const chk = $('#mc-quitar-gratis'); if (chk) chk.addEventListener('change', pintarMensaje);
  const env = $('#mc-enviar-cont');
  if (env) env.addEventListener('click', () => {
    let num = ($('#mc-wa-cont').value || '').replace(/\D/g, '');
    if (num.length < 8) { confirmarModal('WhatsApp del contador', 'Escribe el número de tu contador (sin código país).', () => {}); return; }
    if (!num.startsWith('51')) num = '51' + num;
    window.open('https://wa.me/' + num + '?text=' + encodeURIComponent(mensajeTexto()), '_blank', 'noopener');
  });

  // ── Desconectar (modal propio) ──
  const desc = $('#mc-desconectar');
  if (desc) desc.addEventListener('click', () => {
    confirmarModal('Desconectar mi buzón',
      '¿Seguro? Dejaremos de revisar el buzón de «' + desc.dataset.rs + '» y borraremos '
      + 'tu Clave SOL guardada. Podrás volver a conectarlo después.',
      async () => {
        try {
          const r = await fetch('/contribuyentes/' + desc.dataset.cid + '/desconectar', { method: 'POST' });
          if (r.ok) location.reload(); else confirmarModal('No se pudo', 'Inténtalo de nuevo.', () => {});
        } catch (_) { confirmarModal('Error', 'Error de red.', () => {}); }
      });
  });
})();
