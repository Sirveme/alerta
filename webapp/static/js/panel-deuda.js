/* ═══════════════════════════════════════════════════════════════════
   panel-deuda.js — alerta.pe (zAlerta-52)
   Panel del contador: bloques de deuda por tipo + drill-down por cliente.
   Los datos vienen embebidos (#pd-data, JSON del motor deuda_estudio).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const dataEl = document.getElementById('pd-data');
  const drill = document.getElementById('pd-drill');
  if (!dataEl || !drill) return;
  let DATA;
  try { DATA = JSON.parse(dataEl.textContent); } catch (_) { return; }

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const buscar = document.getElementById('pd-buscar');
  let _tipoActivo = null;

  function filaDoc(d) {
    const monto = d.monto_fmt
      ? '<span class="pd-monto">' + esc(d.monto_fmt) + '</span>'
      : (d.gcs
          ? '<a class="pd-monto pd-monto--rev" href="/valorados/' + esc(d.valorado_id)
            + '/ver" target="_blank" rel="noopener">Ver en el PDF</a>'
          : '<span class="pd-monto pd-monto--rev">Ver documento</span>');
    const pdf = d.gcs
      ? '<a href="/valorados/' + esc(d.valorado_id) + '/ver" target="_blank" rel="noopener" title="Ver PDF"><span class="material-symbols-outlined">picture_as_pdf</span></a>'
      : '';
    return '<tr>'
      + '<td>' + esc(d.num_documento) + ' ' + pdf + '</td>'
      + '<td>' + esc(d.periodo || '—') + '</td>'
      + '<td>' + esc(d.tributo || '—') + '</td>'
      + '<td class="pd-td-monto">' + monto + '</td>'
      + '<td>' + esc(d.fecha || '—') + '</td>'
      + '</tr>';
  }

  function tarjetaCliente(c) {
    return '<div class="pd-cliente" data-ruc="' + esc(c.ruc) + '" data-razon="'
      + esc((c.razon || '').toLowerCase()) + '">'
      + '<div class="pd-cli-top"><div><b class="pd-cli-razon">' + esc(c.razon) + '</b>'
      + '<span class="pd-cli-ruc">RUC ' + esc(c.ruc) + '</span></div>'
      + '<span class="pd-cli-total">' + esc(c.total_fmt || '—')
      + (c.por_confirmar ? ' <span class="pd-cli-pc">+' + c.por_confirmar + ' por confirmar</span>' : '')
      + '</span></div>'
      + '<div class="pd-tabla-wrap"><table class="pd-tabla"><thead><tr>'
      + '<th>N° Documento</th><th>Periodo</th><th>Tributo</th><th>Monto</th><th>Fecha</th>'
      + '</tr></thead><tbody>' + c.docs.map(filaDoc).join('') + '</tbody></table></div>'
      + '</div>';
  }

  function pintarDrill() {
    if (!_tipoActivo) { drill.hidden = true; drill.innerHTML = ''; return; }
    const b = DATA.por_tipo[_tipoActivo];
    if (!b) { drill.hidden = true; return; }
    const q = (buscar && buscar.value || '').trim().toLowerCase();
    let clientes = b.clientes;
    if (q) clientes = clientes.filter((c) =>
      (c.ruc || '').indexOf(q) >= 0 || (c.razon || '').toLowerCase().indexOf(q) >= 0);
    drill.innerHTML =
      '<div class="pd-drill-head"><b>' + esc(b.label) + '</b>'
      + '<span class="muted"> · ' + b.n_clientes + ' cliente(s) · '
      + esc(b.total_fmt || '—') + (b.por_confirmar ? ' · +' + b.por_confirmar + ' por confirmar' : '')
      + '</span></div>'
      + (clientes.length ? clientes.map(tarjetaCliente).join('')
          : '<p class="muted" style="padding:10px 2px">Ningún cliente coincide.</p>');
    drill.hidden = false;
  }

  document.querySelectorAll('.pd-bloque').forEach((b) =>
    b.addEventListener('click', () => {
      const t = b.dataset.tipo;
      _tipoActivo = (_tipoActivo === t) ? null : t;   // toggle
      document.querySelectorAll('.pd-bloque').forEach((x) =>
        x.classList.toggle('pd-bloque--on', x.dataset.tipo === _tipoActivo));
      pintarDrill();
      if (_tipoActivo) drill.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }));

  if (buscar) buscar.addEventListener('input', () => { if (_tipoActivo) pintarDrill(); });
})();
