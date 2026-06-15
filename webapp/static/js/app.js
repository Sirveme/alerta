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

// ── "Actualizar ahora" (scraping bajo demanda) ──
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.btn-actualizar');
  if (!btn) return;
  const id = btn.dataset.id;
  const out = document.querySelector(`.resultado-actualizar[data-for="${id}"]`);
  const original = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Solicitando…';
  if (out) out.textContent = 'Solicitando actualización…';
  try {
    const r = await fetch(`/contribuyentes/${id}/actualizar`, { method: 'POST' });
    const j = await r.json();
    if (out) out.textContent = j.mensaje || (j.exito ? 'Listo.' : 'No se pudo, reintentar.');
    // El worker procesa el scraping en segundo plano: recargamos en ~60s
    // para mostrar los datos frescos cuando termine.
    if (j.exito && j.solicitado) setTimeout(() => location.reload(), 60000);
  } catch (_) {
    if (out) out.textContent = 'No se pudo solicitar la actualización, reintentar.';
  } finally { btn.disabled = false; btn.innerHTML = original; }
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
    <div class="campo"><label>Grupos</label><div id="nc-grupos">${chks}</div></div>`,
    async (fondo) => {
      const ruc = document.getElementById('nc-ruc').value.trim();
      const usuario = document.getElementById('nc-usuario').value.trim();
      const clave = document.getElementById('nc-clave').value;
      if (!/^\d{11}$/.test(ruc) || !usuario || !clave) {
        return confirmarModal('Datos incompletos', 'Revisá RUC (11 dígitos), usuario y clave SOL.', () => {});
      }
      const grupos_ids = Array.from(document.querySelectorAll('#nc-grupos input:checked')).map((c) => c.value);
      try {
        const r = await fetch('/contribuyentes', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ruc, razon_social: document.getElementById('nc-rs').value.trim(),
            usuario_sol: usuario, clave_sol: clave, grupos: grupos_ids })
        });
        const j = await r.json();
        if (j.ok) { cerrarModal(); location.href = '/contribuyentes'; }
        else confirmarModal('No se pudo crear', j.error || 'Error al crear el cliente.', () => {});
      } catch (_) { confirmarModal('Error', 'Error de red al crear el cliente.', () => {}); }
    }, 'Crear cliente');
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
