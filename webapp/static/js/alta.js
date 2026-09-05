/* ═══════════════════════════════════════════════════════════════════
   alta.js — alerta.pe (zAlerta-10)
   Alta de clientes en DOS FASES:
     Fase 1: capturar RUCs rápido, con razón social al instante (API RUC).
     Fase 2: completar credenciales + WhatsApp, "Comprobar conexión", guardar.
   Filosofía: cada paso confirma que funciona y da ganas de seguir.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const raiz = document.getElementById('alta');
  if (!raiz) return;

  const GRUPO_PRE = raiz.dataset.grupoPre || '';
  let restantes = parseInt(raiz.dataset.restantes || '0', 10);

  // Estado: una fila por RUC capturado.
  // {ruc, razon_social, estado:'ok'|'warn'|'err', usuario_sol, clave_sol,
  //  emp_nombre, emp_whatsapp, grupos:[], val:{id,estado}}
  const filas = [];

  // ── Helpers DOM ──
  const $ = (s, c) => (c || document).querySelector(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const attr = (s) => String(s == null ? '' : s).replace(/"/g, '&quot;');

  const input = $('#ruc-input');
  const feedback = $('#ruc-feedback');
  const tabla = $('#tabla-rucs');
  const tbody = $('#tbody-rucs');
  const vacio = $('#alta-vacio');
  const btnContinuar = $('#ir-fase2');

  // ════════════════════════ FASE 1 ════════════════════════
  function setFeedback(el, texto, clase) {
    el.className = 'alta-estado ' + (clase || 'muted');
    el.textContent = texto || '';
  }

  function pintarTabla() {
    const hay = filas.length > 0;
    tabla.hidden = !hay;
    vacio.hidden = hay;
    tbody.innerHTML = filas.map((f, i) => {
      const ic = f.estado === 'ok' ? '<span class="alta-pin ok">✓</span>'
        : f.estado === 'warn' ? '<span class="alta-pin warn">⚠</span>'
        : '<span class="alta-pin run"><i class="ti ti-loader-2"></i></span>';
      const rs = f.estado === 'run'
        ? '<span class="muted">consultando…</span>'
        : (f.razon_social
          ? esc(f.razon_social)
          : '<input class="alta-rs-edit" data-i="' + i
            + '" placeholder="Escribe la razón social" value="' + attr(f.razon_social || '') + '">');
      return '<tr>'
        + '<td>' + ic + '</td>'
        + '<td><strong>' + esc(f.ruc) + '</strong></td>'
        + '<td>' + rs + '</td>'
        + '<td><button class="alta-quitar" data-quitar="' + i + '" aria-label="Quitar">'
        + '<i class="ti ti-x"></i></button></td>'
        + '</tr>';
    }).join('');
    $('#conteo-rucs').textContent = filas.length;
    btnContinuar.disabled = filas.length === 0;
  }

  async function agregarRuc(ruc) {
    ruc = (ruc || '').replace(/\D/g, '');
    if (ruc.length !== 11) {
      setFeedback(feedback, 'El RUC debe tener 11 dígitos.', 'err'); return;
    }
    if (filas.some((f) => f.ruc === ruc)) {
      setFeedback(feedback, 'Ese RUC ya está en tu lista.', 'warn'); return;
    }
    // Fila optimista "consultando…"
    const fila = { ruc, razon_social: '', estado: 'run', grupos: [],
      usuario_sol: '', clave_sol: '', emp_nombre: '', emp_whatsapp: '', val: null };
    filas.push(fila);
    pintarTabla();
    input.value = '';
    input.focus();

    try {
      const r = await fetch('/api/ruc/' + ruc);
      const j = await r.json();
      if (!j.ok) { fila.estado = 'err'; setFeedback(feedback, j.error || 'RUC inválido.', 'err'); }
      else if (j.ya_registrado) {
        // Quitar la fila: ya lo tiene registrado en el estudio.
        const idx = filas.indexOf(fila); if (idx >= 0) filas.splice(idx, 1);
        setFeedback(feedback, 'Ese RUC ya lo tienes registrado en tu estudio.', 'warn');
      } else if (j.razon_social) {
        fila.razon_social = j.razon_social; fila.estado = 'ok';
        setFeedback(feedback, '✓ ' + j.razon_social, 'ok');
      } else {
        fila.estado = 'warn';
        setFeedback(feedback, 'RUC válido, pero no trajo razón social — escríbela a mano.', 'warn');
      }
    } catch (_) {
      fila.estado = 'warn';
      setFeedback(feedback, 'No se pudo consultar ahora; puedes seguir y editar a mano.', 'warn');
    }
    pintarTabla();
  }

  $('#ruc-add').addEventListener('click', () => agregarRuc(input.value));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); agregarRuc(input.value); }
  });
  // Autodisparo al llegar a 11 dígitos.
  input.addEventListener('input', () => {
    input.value = input.value.replace(/\D/g, '').slice(0, 11);
    if (input.value.length === 11) agregarRuc(input.value);
  });

  // Edición de razón social manual (⚠ / sin datos) + quitar.
  tbody.addEventListener('input', (e) => {
    const ed = e.target.closest('.alta-rs-edit');
    if (ed) filas[+ed.dataset.i].razon_social = ed.target ? ed.target.value : ed.value;
  });
  tbody.addEventListener('click', (e) => {
    const q = e.target.closest('[data-quitar]');
    if (q) { filas.splice(+q.dataset.quitar, 1); pintarTabla(); }
  });

  // ── Carga por archivo (Parte C) ──
  $('#alta-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fb = $('#file-feedback');
    setFeedback(fb, 'Leyendo «' + file.name + '»…', 'muted');
    const fd = new FormData(); fd.append('archivo', file);
    let j;
    try {
      const r = await fetch('/clientes/importar-archivo', { method: 'POST', body: fd });
      j = await r.json();
    } catch (_) { setFeedback(fb, 'No se pudo subir el archivo.', 'err'); return; }
    e.target.value = '';
    if (!j.ok) { setFeedback(fb, j.error || 'Archivo inválido.', 'err'); return; }

    let nuevas = 0;
    for (const row of j.filas) {
      if (filas.some((f) => f.ruc === row.ruc)) continue;
      const fila = {
        ruc: row.ruc, razon_social: row.razon_social || '',
        estado: row.ruc_valido ? (row.razon_social ? 'ok' : 'run') : 'err',
        grupos: [], usuario_sol: row.usuario_sol || '', clave_sol: row.clave_sol || '',
        emp_nombre: '', emp_whatsapp: '', val: null };
      filas.push(fila); nuevas++;
    }
    pintarTabla();
    setFeedback(fb, j.mensaje, 'ok');
    // Completar razón social faltante vía API (sin bloquear).
    for (const fila of filas) {
      if (fila.estado === 'run' && fila.razon_social === '') {
        try {
          const r = await fetch('/api/ruc/' + fila.ruc);
          const d = await r.json();
          if (d.ok && d.ya_registrado) { fila.estado = 'warn'; fila._dup = true; }
          else if (d.ok && d.razon_social) { fila.razon_social = d.razon_social; fila.estado = 'ok'; }
          else fila.estado = 'warn';
        } catch (_) { fila.estado = 'warn'; }
        pintarTabla();
      }
    }
  });

  // ════════════════════════ FASE 2 ════════════════════════
  const fase1 = $('.alta-fase[data-fase="1"]');
  const fase2 = $('.alta-fase[data-fase="2"]');

  function irAFase(n) {
    fase1.hidden = n !== 1;
    fase2.hidden = n !== 2;
    document.querySelectorAll('.alta-paso').forEach((p) =>
      p.classList.toggle('activo', +p.dataset.paso <= n));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  btnContinuar.addEventListener('click', () => {
    if (!filas.length) return;
    renderFase2();
    irAFase(2);
  });
  $('#volver-fase1').addEventListener('click', () => irAFase(1));

  function renderFase2() {
    const cont = $('#fase2-lista');
    const tplGrupos = $('#tpl-grupos').innerHTML;
    cont.innerHTML = filas.map((f, i) => {
      const nombre = f.razon_social || ('RUC ' + f.ruc);
      return ''
        + '<div class="alta-card" data-i="' + i + '">'
        + '  <div class="alta-card-top">'
        + '    <div><div class="alta-card-rs">' + esc(nombre) + '</div>'
        + '      <div class="muted">RUC ' + esc(f.ruc) + '</div></div>'
        + '    <span class="alta-conexion" data-conx="' + i + '"></span>'
        + '  </div>'
        + '  <div class="alta-grid">'
        + '    <div class="campo"><label>Usuario SOL</label>'
        + '      <input class="f2" data-campo="usuario_sol" data-i="' + i + '"'
        + '        autocomplete="off" value="' + attr(f.usuario_sol) + '"></div>'
        + '    <div class="campo"><label>Clave SOL</label>'
        + '      <input class="f2" type="password" data-campo="clave_sol" data-i="' + i + '"'
        + '        autocomplete="new-password" value="' + attr(f.clave_sol) + '"></div>'
        + '  </div>'
        + '  <div class="campo"><label>WhatsApp del dueño (opcional)</label>'
        + '    <input class="f2" data-campo="emp_whatsapp" data-i="' + i + '"'
        + '      inputmode="numeric" placeholder="51XXXXXXXXX" value="' + attr(f.emp_whatsapp) + '">'
        + '    <small class="muted">Si lo agregas, se crea su acceso gratis y podrás invitarlo.</small></div>'
        + '  <div class="campo alta-emp-nombre" data-i="' + i + '" '
        + (f.emp_whatsapp ? '' : 'hidden') + '><label>Nombre del dueño</label>'
        + '    <input class="f2" data-campo="emp_nombre" data-i="' + i + '"'
        + '      placeholder="Nombre y apellido" value="' + attr(f.emp_nombre) + '"></div>'
        + '  <div class="campo"><label>Grupos</label>'
        + '    <div class="alta-grupos" data-i="' + i + '">' + tplGrupos + '</div></div>'
        + '  <button class="btn btn--sec alta-comprobar" data-i="' + i + '" type="button">'
        + '    <i class="ti ti-plug-connected"></i> Comprobar conexión</button>'
        + '</div>';
    }).join('');

    // Preseleccionar grupo de origen si vinimos desde un grupo.
    if (GRUPO_PRE) {
      cont.querySelectorAll('.alta-grupos input[value="' + GRUPO_PRE + '"]')
        .forEach((c) => { c.checked = true; });
      filas.forEach((f) => { if (!f.grupos.includes(GRUPO_PRE)) f.grupos.push(GRUPO_PRE); });
    }
    $('#conteo-guardar').textContent = '(' + filas.length + ')';
  }

  // Sincronizar inputs de Fase 2 con el estado.
  $('#fase2-lista').addEventListener('input', (e) => {
    const el = e.target.closest('.f2');
    if (el) {
      const f = filas[+el.dataset.i];
      f[el.dataset.campo] = el.value;
      if (el.dataset.campo === 'emp_whatsapp') {
        const nb = $('.alta-emp-nombre[data-i="' + el.dataset.i + '"]');
        if (nb) nb.hidden = !el.value.trim();
      }
    }
    const chk = e.target.closest('.alta-grupos input');
    if (chk) {
      const f = filas[+chk.closest('.alta-grupos').dataset.i];
      f.grupos = Array.from(
        chk.closest('.alta-grupos').querySelectorAll('input:checked')).map((c) => c.value);
    }
  });

  // ── "Comprobar conexión" (login real vía worker, con polling) ──
  $('#fase2-lista').addEventListener('click', (e) => {
    const btn = e.target.closest('.alta-comprobar');
    if (btn) comprobar(+btn.dataset.i, btn);
  });

  function pintarConexion(i, variante, texto) {
    const el = document.querySelector('.alta-conexion[data-conx="' + i + '"]');
    if (el) { el.className = 'alta-conexion ' + variante; el.innerHTML = texto; }
  }

  async function comprobar(i, btn) {
    const f = filas[i];
    if (!f.usuario_sol || !f.clave_sol) {
      pintarConexion(i, 'no', '✗ Ingresa usuario y clave SOL'); return;
    }
    btn.disabled = true;
    pintarConexion(i, 'run', '<i class="ti ti-loader-2"></i> comprobando…');
    let id;
    try {
      const r = await fetch('/contribuyentes/validar-credenciales', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ruc: f.ruc, usuario_sol: f.usuario_sol, clave_sol: f.clave_sol }) });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error);
      id = j.id;
    } catch (_) {
      pintarConexion(i, 'no', '✗ No se pudo iniciar la comprobación');
      btn.disabled = false; return;
    }

    // Polling cada 5s hasta ~90s (el login real toma segundos).
    let intentos = 0;
    const timer = setInterval(async () => {
      intentos++;
      let est;
      try { est = await (await fetch('/contribuyentes/validar-credenciales/' + id)).json(); }
      catch (_) { est = null; }
      if (est && est.ok && est.listo) {
        clearInterval(timer); btn.disabled = false;
        if (est.estado === 'conecta') {
          // Evidencia de LECTURA real: además de "Conecta", el último aviso
          // del buzón (fecha + asunto). Confirma que sí leemos, no solo que
          // el login entra. Si el peek no vino, mostramos solo "Conecta".
          let txt = '✓ Conecta';
          if (est.ultimo_aviso) {
            txt += ' · <span class="conx-aviso">Último aviso: '
                 + esc(est.ultimo_aviso) + '</span>';
          }
          pintarConexion(i, 'ok', txt);
        }
        else if (est.estado === 'no_conecta') pintarConexion(i, 'no', '✗ No conecta — revisa la clave');
        else pintarConexion(i, 'no', '✗ No se pudo comprobar ahora');
      } else if (intentos >= 18) {
        clearInterval(timer); btn.disabled = false;
        pintarConexion(i, 'run', 'Sigue comprobando… revisa en un momento');
      }
    }, 5000);
  }

  // ── Guardar todo (batch) ──
  $('#guardar-todo').addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    // Validación mínima: cada fila necesita usuario + clave.
    const incompletas = filas.filter((f) => !f.usuario_sol || !f.clave_sol);
    if (incompletas.length) {
      confirmarModal('Faltan credenciales',
        incompletas.length + ' cliente(s) sin usuario o clave SOL. Complétalos para guardar.',
        () => {});
      return;
    }
    btn.disabled = true; btn.textContent = 'Guardando…';
    const payload = {
      clientes: filas.map((f) => ({
        ruc: f.ruc, razon_social: f.razon_social, usuario_sol: f.usuario_sol,
        clave_sol: f.clave_sol, empresario_nombre: f.emp_nombre,
        empresario_whatsapp: f.emp_whatsapp, grupos: f.grupos })),
    };
    let j;
    try {
      const r = await fetch('/clientes/alta', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload) });
      j = await r.json();
    } catch (_) {
      btn.disabled = false; btn.textContent = 'Guardar';
      confirmarModal('Error', 'Error de red al guardar.', () => {}); return;
    }
    if (!j.ok) {
      btn.disabled = false; btn.textContent = 'Guardar';
      confirmarModal('No se pudo guardar', j.error || 'Inténtalo de nuevo.', () => {}); return;
    }
    mostrarResumen(j);
  });

  function mostrarResumen(j) {
    const invits = j.resultados.filter((r) => r.ok && r.wa_url);
    const fallos = j.resultados.filter((r) => !r.ok);
    let html = '<p>Se crearon <strong>' + j.creados + '</strong> cliente(s).</p>';
    if (j.mensaje_limite) html += '<p class="alta-estado warn">' + esc(j.mensaje_limite) + '</p>';
    if (fallos.length) {
      html += '<p class="muted">No se crearon ' + fallos.length + ':</p><ul style="margin:0 0 10px;padding-left:18px">'
        + fallos.map((r) => '<li>RUC ' + esc(r.ruc) + ' — ' + esc(r.error || r.estado) + '</li>').join('')
        + '</ul>';
    }
    if (invits.length) {
      html += '<p class="muted">Invita a los dueños por WhatsApp:</p>'
        + invits.map((r) => '<a class="btn btn--bloque btn--whatsapp" style="margin-bottom:8px" '
          + 'href="' + attr(r.wa_url) + '" target="_blank" rel="noopener">Invitar a '
          + esc((r.empresario && r.empresario.nombre) || r.ruc) + '</a>').join('');
    }
    modalHTML('✅ Listo', html, () => {
      cerrarModal();
      location.href = GRUPO_PRE ? ('/grupos/' + GRUPO_PRE) : '/contribuyentes';
    }, 'Ver mis clientes');
  }
})();
