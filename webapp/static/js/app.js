/* ═══════════════════════════════════════════════════════════════════
   app.js — alerta.pe
   Helpers globales: modales PROPIOS (no nativos), "Actualizar ahora",
   menú de acciones rápidas ("+"), registro del service worker.
   (El tema vive en tema.js; la barra IA en ia.js.)
   ═══════════════════════════════════════════════════════════════════ */

// ── Modal propio: confirmar ──
function confirmarModal(titulo, texto, onSi) {
  const fondo = document.getElementById('modal-fondo');
  document.getElementById('modal-titulo').textContent = titulo;
  document.getElementById('modal-texto').textContent = texto;
  const acciones = document.getElementById('modal-acciones');
  acciones.innerHTML = '';
  acciones.append(
    _btn('Cancelar', 'btn btn--sec', cerrarModal),
    _btn('Aceptar', 'btn btn--peligro', () => { cerrarModal(); onSi(); }));
  fondo.hidden = false;
}

// ── Modal con HTML arbitrario + botón Guardar (callback) ──
function modalHTML(titulo, html, onGuardar, txtGuardar) {
  const fondo = document.getElementById('modal-fondo');
  document.getElementById('modal-titulo').textContent = titulo;
  document.getElementById('modal-texto').innerHTML = html;
  const acciones = document.getElementById('modal-acciones');
  acciones.innerHTML = '';
  acciones.append(
    _btn('Cancelar', 'btn btn--sec', cerrarModal),
    _btn(txtGuardar || 'Guardar', 'btn', () => onGuardar(fondo)));
  fondo.hidden = false;
}

// ── Modal que adopta un <form> oculto del DOM ──
function abrirModalForm(titulo, form) {
  const texto = document.getElementById('modal-texto');
  document.getElementById('modal-titulo').textContent = titulo;
  texto.innerHTML = ''; form.hidden = false; texto.appendChild(form);
  const acciones = document.getElementById('modal-acciones');
  acciones.innerHTML = '';
  acciones.append(
    _btn('Cancelar', 'btn btn--sec', () => { form.hidden = true; cerrarModal(); }),
    _btn('Guardar', 'btn', () => { if (form.reportValidity()) form.submit(); }));
  document.getElementById('modal-fondo').hidden = false;
}

function cerrarModal() { document.getElementById('modal-fondo').hidden = true; }
function _btn(txt, cls, fn) {
  const b = document.createElement('button');
  b.textContent = txt; b.className = cls; b.type = 'button';
  b.addEventListener('click', fn); return b;
}
document.getElementById('modal-fondo')?.addEventListener('click', (e) => {
  if (e.target.id === 'modal-fondo') cerrarModal();
});

function postRedirect(action) {
  const f = document.createElement('form');
  f.method = 'POST'; f.action = action;
  document.body.appendChild(f); f.submit();
}

// ── "Actualizar ahora" — indicador CON SUSTANCIA (zAlerta-07 D) ──
function _horaCorta(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' }); }
  catch (_) { return '—'; }
}
function _resumenConteos(conteos) {
  if (!conteos || !conteos.length) return 'Aún no hay notificaciones guardadas.';
  return 'Tienes: ' + conteos.map((c) => `${c.n} ${c.etiqueta}`).join(' · ');
}
async function _estado(id) {
  const r = await fetch(`/contribuyentes/${id}/estado-scrapeo`);
  return r.json();
}

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.btn-actualizar');
  if (!btn) return;
  const id = btn.dataset.id;
  const out = document.querySelector(`.resultado-actualizar[data-for="${id}"]`);
  const original = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Actualizando…';

  // 1) Mostrar lo que YA tiene (instantáneo desde BD), no un spinner vacío.
  let prev = { ultimo_scrapeo_at: null, total: 0 };
  try {
    const est = await _estado(id);
    if (est.ok) {
      prev = { ultimo_scrapeo_at: est.ultimo_scrapeo_at, total: est.total };
      if (out) out.textContent = _resumenConteos(est.conteos) + ' · Buscando novedades…';
    }
  } catch (_) {}

  // 2) Marcar el flag (el worker scrapea aparte).
  try {
    await fetch(`/contribuyentes/${id}/actualizar`, { method: 'POST' });
  } catch (_) {
    if (out) out.textContent = 'No se pudo solicitar la actualización, reintentar.';
    btn.disabled = false; btn.innerHTML = original; return;
  }
  btn.disabled = false; btn.innerHTML = original;

  // 3) Polling cada 10s hasta ~2 min: detectar cuando ultimo_scrapeo_at cambia.
  let intentos = 0;
  const maxIntentos = 12;
  const timer = setInterval(async () => {
    intentos++;
    let est;
    try { est = await _estado(id); } catch (_) { est = null; }
    if (est && est.ok && est.ultimo_scrapeo_at !== prev.ultimo_scrapeo_at) {
      clearInterval(timer);
      const nuevas = (est.total || 0) - (prev.total || 0);
      if (nuevas > 0) {
        if (out) out.textContent = `¡${nuevas} ${nuevas === 1 ? 'nueva notificación' : 'nuevas notificaciones'}!`;
        setTimeout(() => location.reload(), 1200);
      } else if (out) {
        out.textContent = `Listo. No hay notificaciones nuevas entre `
          + `${_horaCorta(prev.ultimo_scrapeo_at)} y ${_horaCorta(est.ultimo_scrapeo_at)}.`;
      }
    } else if (intentos >= maxIntentos) {
      clearInterval(timer);
      if (out) out.textContent = 'El monitoreo sigue en proceso, te avisaremos apenas haya novedades.';
    }
  }, 10000);
});

// ── Menú de acciones rápidas "+" ──
(function () {
  const btn = document.getElementById('btn-mas');
  const menu = document.getElementById('menu-mas');
  if (!btn || !menu) return;
  btn.addEventListener('click', (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; });
  document.addEventListener('click', (e) => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) menu.hidden = true;
  });
  menu.querySelectorAll('[data-accion]').forEach((b) =>
    b.addEventListener('click', () => { menu.hidden = true; abrirAccion(b.dataset.accion); }));
})();

function abrirAccion(accion) {
  if (accion === 'nuevo-grupo') return formNuevoGrupo();
  if (accion === 'nuevo-cliente') return formNuevoCliente();
  if (accion === 'importar') return formImportar();
}

function formNuevoGrupo() {
  modalHTML('Nuevo grupo', `
    <div class="campo"><label>Nombre</label>
      <input id="ng-nombre" required placeholder="ej. Tarapoto, Pagan tarde"></div>
    <div class="campo"><label>Color</label>
      <input id="ng-color" type="color" value="#5B8DEF"></div>`,
    () => {
      const nombre = document.getElementById('ng-nombre').value.trim();
      if (!nombre) return;
      const f = document.createElement('form'); f.method = 'POST'; f.action = '/grupos';
      f.innerHTML = `<input name="nombre" value="${_attr(nombre)}">
        <input name="color" value="${document.getElementById('ng-color').value}">
        <input name="icono" value="ti-folder">`;
      document.body.appendChild(f); f.submit();
    });
}

async function formNuevoCliente() {
  let grupos = [];
  try { grupos = await (await fetch('/api/grupos')).json(); } catch (_) {}
  const chks = grupos.map((g) =>
    `<label style="display:flex;align-items:center;gap:8px;font-weight:400;margin-bottom:6px;">
       <input type="checkbox" style="width:auto;min-height:auto" name="grupo" value="${g.id}">
       <span class="grupo-punto" style="--acento:${_attr(g.color)};width:12px;height:12px"></span>${_attr(g.nombre)}
     </label>`).join('') || '<span class="muted">No hay grupos todavía.</span>';

  modalHTML('Nuevo cliente (RUC)', `
    <div class="campo"><label>RUC</label>
      <input id="nc-ruc" inputmode="numeric" pattern="[0-9]{11}" maxlength="11"
             required placeholder="11 dígitos"></div>
    <div class="campo"><label>Razón social (opcional)</label>
      <input id="nc-rs" placeholder="Se autocompleta si está disponible"></div>
    <div class="campo"><label>Usuario SOL</label>
      <input id="nc-usuario" required autocomplete="off"></div>
    <div class="campo"><label>Clave SOL</label>
      <input id="nc-clave" type="password" required autocomplete="new-password">
      <small class="muted">Se cifra con Fernet. Nunca se muestra.</small></div>
    <hr style="border:none;border-top:1px solid var(--borde);margin:14px 0">
    <p class="muted" style="margin:0 0 10px">Datos del dueño del RUC (se crea su acceso gratis):</p>
    <div class="campo"><label>Nombre del empresario</label>
      <input id="nc-emp-nombre" required placeholder="Nombre y apellido del dueño"></div>
    <div class="campo"><label>WhatsApp del empresario</label>
      <input id="nc-emp-wa" inputmode="numeric" required placeholder="51XXXXXXXXX (con código país)">
      <small class="muted">Para invitarlo a ver su propio RUC.</small></div>
    <div class="campo"><label>Grupos</label><div id="nc-grupos">${chks}</div></div>`,
    async (fondo) => {
      const ruc = document.getElementById('nc-ruc').value.trim();
      const usuario = document.getElementById('nc-usuario').value.trim();
      const clave = document.getElementById('nc-clave').value;
      const empNombre = document.getElementById('nc-emp-nombre').value.trim();
      const empWa = document.getElementById('nc-emp-wa').value.replace(/\D/g, '');
      if (!/^\d{11}$/.test(ruc) || !usuario || !clave) {
        return confirmarModal('Datos incompletos', 'Revisa el RUC (11 dígitos), usuario y clave SOL.', () => {});
      }
      if (!empNombre || empWa.length < 9) {
        return confirmarModal('Datos del empresario', 'Ingresa el nombre y el WhatsApp del dueño del RUC (con código país).', () => {});
      }
      const grupos_ids = Array.from(document.querySelectorAll('#nc-grupos input:checked')).map((c) => c.value);
      try {
        const r = await fetch('/contribuyentes', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ruc, razon_social: document.getElementById('nc-rs').value.trim(),
            usuario_sol: usuario, clave_sol: clave, grupos: grupos_ids,
            empresario_nombre: empNombre, empresario_whatsapp: empWa })
        });
        const j = await r.json();
        if (j.ok) { invitarEmpresario(j); }
        else confirmarModal(j.limite ? 'Límite de plan' : 'No se pudo crear',
                            j.error || 'Error al crear el cliente.', () => {});
      } catch (_) { confirmarModal('Error', 'Error de red al crear el cliente.', () => {}); }
    }, 'Crear cliente');
}

// Tras crear el cliente: mostrar el botón notorio "Invitar por WhatsApp".
function invitarEmpresario(j) {
  const nombre = (j.empresario && j.empresario.nombre) || 'tu cliente';
  modalHTML('✅ Cliente creado',
    `<p>Se creó el RUC y la cuenta gratuita de <strong>${_attr(nombre)}</strong>.</p>
     <p class="muted">Invítalo por WhatsApp: recibirá el enlace para pedir su clave a Soporte.</p>
     <a class="btn btn--bloque btn--whatsapp" href="${_attr(j.wa_url)}" target="_blank" rel="noopener">
       Invitar al cliente por WhatsApp</a>`,
    () => { cerrarModal(); location.href = '/contribuyentes'; }, 'Listo, ver clientes');
}

function formImportar() {
  modalHTML('Importar Excel de RUCs', `
    <p class="muted">Subí un .xlsx con columnas: <strong>RUC, usuario, clave, grupo</strong>.</p>
    <div class="campo"><input id="imp-file" type="file" accept=".xlsx,.xls"></div>`,
    async () => {
      const f = document.getElementById('imp-file').files[0];
      if (!f) return;
      const fd = new FormData(); fd.append('archivo', f);
      try {
        const r = await fetch('/contribuyentes/importar', { method: 'POST', body: fd });
        const j = await r.json();
        cerrarModal();
        confirmarModal('Importación', j.mensaje || 'Recibido.', () => {});
      } catch (_) { confirmarModal('Error', 'No se pudo subir el archivo.', () => {}); }
    }, 'Importar');
}

function _attr(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;'); }

// ── Service worker (PWA) ──
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
