/* ═══════════════════════════════════════════════════════════════════
   pagar.js — alerta.pe (zAlerta-14 + fix zAlerta-15)
   Dos flujos:
     A "Voy a pagar ahora" → abre sesión (marca de tiempo) → "Ya transferí" →
        busca en ventana CORTA (normalmente 1 pago: el suyo).
     B "Ya pagué antes"    → busca en ventana AMPLIA (48h) → autoidentificación.
   La API Key de PagoOK NUNCA toca el navegador: todo pasa por el backend.
   Tiempos: el backend filtra en UTC; las horas mostradas llegan ya en hora Lima.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const raiz = document.getElementById('pay');
  if (!raiz) return;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const caminos = $('#pay-caminos');
  const transferir = $('#pay-transferir');
  const listaWrap = $('#pay-lista-wrap');
  const lista = $('#pay-lista');
  const listaTit = $('#pay-lista-tit');
  const estado = $('#pay-estado');

  let sesionAbierta = false;   // flujo A iniciado y no completado
  let activado = false;

  function pintaEstado(cls, txt) { estado.className = 'pay-estado ' + (cls || ''); estado.innerHTML = txt || ''; }

  // ── Flujo A: abrir sesión de pago ──
  $('#pay-ahora').addEventListener('click', async () => {
    try { await fetch('/api/pago/iniciar', { method: 'POST' }); sesionAbierta = true; } catch (_) {}
    caminos.hidden = true;
    transferir.hidden = false;
    transferir.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  $('#pay-transferi').addEventListener('click', () => buscar('ahora'));
  $('#pay-antes').addEventListener('click', () => {
    // Cambiar a flujo B: descartar cualquier sesión corta previa.
    if (sesionAbierta) { fetch('/api/pago/cancelar', { method: 'POST' }).catch(() => {}); sesionAbierta = false; }
    buscar('antes');
  });

  async function buscar(flujo) {
    pintaEstado('run', '<i class="ti ti-loader-2"></i> Buscando tu pago…');
    let j;
    try {
      j = await (await fetch('/api/pago/buscar', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ flujo: flujo }) })).json();
    } catch (_) { j = null; }
    if (!j || !j.ok) { pintaEstado('no', 'No pudimos consultar ahora. Reintenta en un momento.'); return; }
    if (!j.pagos.length) {
      listaWrap.hidden = true;
      pintaEstado('warn', 'Aún no vemos tu pago. A veces demora unos segundos. '
        + '<button class="pay-reintentar" data-flujo="' + flujo + '">Reintentar</button>');
      const rb = estado.querySelector('.pay-reintentar');
      if (rb) rb.onclick = () => buscar(rb.dataset.flujo);
      return;
    }
    pintaEstado('', '');
    // Flujo A con 1 solo pago → confirmación directa "¿Este es tu pago?".
    if (flujo === 'ahora' && j.pagos.length === 1) {
      listaTit.textContent = '¿Este es tu pago?';
    } else {
      listaTit.textContent = '¿Cuál es tu pago?';
    }
    const unico = (flujo === 'ahora' && j.pagos.length === 1);
    const etiqueta = (mt) => mt === 'plin' ? 'PLIN' : mt === 'yape' ? 'YAPE'
      : (mt === 'transferencia' || mt === 'transf') ? 'TRANSF.' : (mt ? mt.toUpperCase() : 'PAGO');
    lista.innerHTML = j.pagos.map((p) => {
      const cuando = ((p.fecha ? p.fecha + ' ' : '') + (p.hora || '')).trim() || '—';
      const cta = unico ? '<span class="pay-simio">Sí, es mío →</span>' : '';
      return '<button class="pay-opcion" data-id="' + esc(p.id) + '">'
        + '<span class="pay-met pay-met--' + esc(p.metodo) + '">' + esc(etiqueta(p.metodo)) + '</span>'
        + '<span class="pay-info"><span class="pay-titular">' + esc(p.nombre) + '</span>'
        + '<span class="pay-cuando">' + esc(cuando) + ' · S/ ' + esc(p.monto) + '</span></span>'
        + cta + '</button>';
    }).join('');
    listaWrap.hidden = false;
    listaWrap.scrollIntoView({ behavior: 'smooth', block: 'center' });
    lista.querySelectorAll('.pay-opcion').forEach((b) =>
      b.addEventListener('click', () => reclamar(b.dataset.id)));
  }

  async function reclamar(pagoId) {
    lista.querySelectorAll('.pay-opcion').forEach((b) => { b.disabled = true; });
    pintaEstado('run', '<i class="ti ti-loader-2"></i> Validando tu pago…');
    let j;
    try {
      j = await (await fetch('/api/pago/reclamar', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ pago_id: pagoId }) })).json();
    } catch (_) { j = null; }
    if (j && j.ok) { activado = true; sesionAbierta = false; celebrar(j.vence); return; }
    lista.querySelectorAll('.pay-opcion').forEach((b) => { b.disabled = false; });
    if (j && j.ya_reclamado) {
      pintaEstado('no', j.error || 'Ese pago ya fue registrado. Si crees que es un error, escríbenos.');
    } else {
      pintaEstado('no', (j && j.error) || 'No pudimos validar ahora, reintenta en un momento.');
    }
  }

  function celebrar(vence) {
    const c = document.getElementById('pay-celebra');
    listaWrap.hidden = true; transferir.hidden = true; pintaEstado('', '');
    c.innerHTML =
      '<div class="pay-celebra-card borde-chicha-1">'
      + '<i class="ti ti-confetti pay-celebra-icono"></i>'
      + '<div class="pay-celebra-tit">¡Listo! Tu suscripción está activa.</div>'
      + (vence ? '<div class="pay-celebra-vence">Tu alerta.pe está activa hasta el <b>' + esc(vence) + '</b>.</div>' : '')
      + '<div class="pay-celebra-gracias">Gracias por confiar en nosotros.</div>'
      + '<div class="pay-pagook">¿Sabes cómo supimos en segundos que tu pago es válido? '
      + 'Usamos <b>PagoOK</b>.</div>'
      + '<a href="/" class="lp-cta act-cta-full" style="margin-top:14px">'
      + '<span class="lp-cta-resp">Ir a mi panel →</span></a>'
      + '</div>';
    c.hidden = false;
    c.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // FIX 3 · si el usuario se va con una sesión A abierta sin completar, limpiarla.
  window.addEventListener('pagehide', () => {
    if (sesionAbierta && !activado && navigator.sendBeacon) {
      navigator.sendBeacon('/api/pago/cancelar');
    }
  });
})();
