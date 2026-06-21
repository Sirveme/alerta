/* ═══════════════════════════════════════════════════════════════════
   pagar.js — alerta.pe (zAlerta-14)
   "Ya pagué" → busca pagos de S/5 recientes (auto-identificación) → el
   usuario elige el suyo → reclama y activa la suscripción. La API Key de
   PagoOK NUNCA toca el navegador: todo pasa por el backend de alerta.pe.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const raiz = document.getElementById('pay');
  if (!raiz) return;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const monto = raiz.dataset.monto || '5.00';

  const listaWrap = $('#pay-lista-wrap');
  const lista = $('#pay-lista');
  const estado = $('#pay-estado');
  const btn = $('#pay-yapague');

  function pintaEstado(cls, txt) { estado.className = 'pay-estado ' + (cls || ''); estado.innerHTML = txt || ''; }

  async function buscar() {
    btn.disabled = true;
    pintaEstado('run', '<i class="ti ti-loader-2"></i> Buscando tu pago…');
    let j;
    try { j = await (await fetch('/api/pago/buscar', { method: 'POST', headers: { 'Accept': 'application/json' } })).json(); }
    catch (_) { j = null; }
    btn.disabled = false;
    if (!j || !j.ok) { pintaEstado('no', 'No pudimos consultar ahora. Reintenta en un momento.'); return; }
    if (!j.pagos.length) {
      listaWrap.hidden = true;
      pintaEstado('warn', 'Aún no vemos tu pago. A veces demora unos segundos. '
        + '<button class="pay-reintentar" id="pay-reintentar">Reintentar</button>');
      const rb = $('#pay-reintentar'); if (rb) rb.onclick = buscar;
      return;
    }
    pintaEstado('', '');
    lista.innerHTML = j.pagos.map((p) => {
      const met = p.metodo === 'plin' ? 'Plin' : (p.metodo === 'yape' ? 'Yape' : (p.metodo || 'Pago'));
      return '<button class="pay-opcion" data-id="' + esc(p.id) + '">'
        + '<span class="pay-met pay-met--' + esc(p.metodo) + '">' + esc(met) + '</span>'
        + '<span class="pay-titular">' + esc(p.titular) + '</span>'
        + '<span class="pay-hora">' + esc(p.hora) + '</span>'
        + '<span class="pay-mt">S/ ' + esc(p.monto) + '</span></button>';
    }).join('');
    listaWrap.hidden = false;
    lista.querySelectorAll('.pay-opcion').forEach((b) =>
      b.addEventListener('click', () => reclamar(b.dataset.id, b)));
  }

  async function reclamar(pagoId, opcion) {
    lista.querySelectorAll('.pay-opcion').forEach((b) => { b.disabled = true; });
    pintaEstado('run', '<i class="ti ti-loader-2"></i> Validando tu pago…');
    let j;
    try {
      j = await (await fetch('/api/pago/reclamar', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ pago_id: pagoId }) })).json();
    } catch (_) { j = null; }
    if (j && j.ok) { celebrar(j.vence); return; }
    lista.querySelectorAll('.pay-opcion').forEach((b) => { b.disabled = false; });
    if (j && j.ya_reclamado) {
      pintaEstado('no', j.error || 'Ese pago ya fue registrado. Si crees que es un error, escríbenos.');
    } else {
      pintaEstado('no', (j && j.error) || 'No pudimos validar ahora, reintenta en un momento.');
    }
  }

  function celebrar(vence) {
    const c = document.getElementById('pay-celebra');
    listaWrap.hidden = true; pintaEstado('', '');
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

  btn.addEventListener('click', buscar);
})();
