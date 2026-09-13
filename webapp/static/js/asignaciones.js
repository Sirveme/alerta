/* asignaciones.js — Capa 1 Fase C3. Pantalla del contador dueño: asignar clientes
   a asistentes por GRUPO (en vivo) o por RUC. Solo CREA asignaciones (C3); no
   cambia lo que ve el asistente (C4). */
(function () {
  var raiz = document.getElementById('asg');
  var dataEl = document.getElementById('asg-data');
  if (!raiz || !dataEl) return;
  var DATA = JSON.parse(dataEl.textContent);
  var esc = function (s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); };

  var gById = {}; DATA.grupos.forEach(function (g) { gById[g.id] = g; });
  var cById = {}; DATA.contribs.forEach(function (c) { cById[c.id] = c; });
  var asig = DATA.asignaciones || {};            // { asistente_id: {grupos:[], rucs:[]} }
  var sel = DATA.asistentes.length ? DATA.asistentes[0].id : null;

  var $ = function (id) { return document.getElementById(id); };
  var toastT = null;
  function toast(msg) {
    var t = $('asg-toast'); if (!t) return;
    t.textContent = msg; t.classList.add('show');
    clearTimeout(toastT); toastT = setTimeout(function () { t.classList.remove('show'); }, 2600);
  }
  function nombreDe(aid) { var a = DATA.asistentes.find(function (x) { return x.id === aid; }); return a ? a.nombre : '—'; }
  function inicial(nom) { return (nom || '?').trim().charAt(0).toUpperCase(); }
  // SOLO demo: mapea por nombre las 3 fotos de asistentes. Sin foto → avatar de inicial.
  var FOTO_DEMO = { ana: 'asistente-ana.jpg', beto: 'asistente-beto.jpg', carla: 'asistente-carla.jpg' };
  function fotoDe(nom) {
    var f = FOTO_DEMO[(nom || '').trim().split(' ')[0].toLowerCase()];
    return f ? '/static/img/' + f : null;
  }
  function cuenta(aid) {
    var a = asig[aid] || { grupos: [], rucs: [] };
    var n = a.rucs.length;
    a.grupos.forEach(function (gid) { if (gById[gid]) n += gById[gid].n; });
    return n;
  }

  function renderEquipo() {
    var cont = $('asg-equipo'); cont.innerHTML = '';
    DATA.asistentes.forEach(function (a) {
      var d = document.createElement('div');
      d.className = 'asg-asis' + (a.id === sel ? ' sel' : '');
      d.dataset.aid = a.id;
      var foto = fotoDe(a.nombre), ini = esc(inicial(a.nombre));
      var av = foto
        ? '<div class="asg-av asg-av--foto"><img src="' + foto + '" alt="" data-ini="' + ini + '" '
            + 'onerror="this.parentNode.classList.remove(\'asg-av--foto\');this.parentNode.textContent=this.dataset.ini;"></div>'
        : '<div class="asg-av">' + ini + '</div>';
      d.innerHTML = av
        + '<div class="asg-nom">' + esc(a.nombre.split(' ')[0]) + '</div>'
        + '<div class="asg-cnt">' + cuenta(a.id) + ' RUCs</div>';
      d.addEventListener('click', function () { sel = a.id; renderTodo(); });
      cont.appendChild(d);
    });
  }

  // Quitar en 2 toques (evita liberaciones accidentales): 1er toque arma "¿Quitar?",
  // 2º confirma; se desarma solo a los 3.5s o si armas otro. `etiqueta` va al aria-label.
  function armarQuitar(btn, etiqueta, ejecutar) {
    var timer = null;
    function reset() {
      btn.classList.remove('confirm'); btn.textContent = '×';
      btn.setAttribute('aria-label', etiqueta); btn._armado = false; clearTimeout(timer);
    }
    reset();
    btn.setAttribute('type', 'button'); btn.setAttribute('title', etiqueta);
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (btn._armado) { reset(); ejecutar(); return; }
      // Desarma cualquier otro ✕ pendiente en el panel.
      $('asg-panel').querySelectorAll('.asg-x.confirm').forEach(function (o) {
        o.classList.remove('confirm'); o.textContent = '×'; o._armado = false;
      });
      btn._armado = true; btn.classList.add('confirm'); btn.textContent = '¿Quitar?';
      btn.setAttribute('aria-label', 'Confirmar: quitar ' + etiqueta);
      timer = setTimeout(reset, 3500);
    });
  }

  function renderPanel() {
    var a = asig[sel] || { grupos: [], rucs: [] };
    var nom1 = (nombreDe(sel) || '').split(' ')[0];
    $('asg-nom-sel').textContent = nombreDe(sel);
    $('asg-nom-sel2').textContent = nom1;
    if ($('asg-nom-sel3')) $('asg-nom-sel3').textContent = nom1;
    var p = $('asg-panel'); var h = '';
    h += '<div class="asg-subcap">GRUPOS ASIGNADOS</div>';
    if (a.grupos.length) {
      h += '<div class="asg-gchips">';
      a.grupos.forEach(function (gid) {
        var g = gById[gid]; if (!g) return;
        h += '<span class="asg-gchip"><span class="dot"></span>' + esc(g.nombre) + ' · ' + g.n
          + ' <span class="asg-vivo">EN VIVO</span>'
          + '<button class="asg-x" data-quitar-grupo="' + gid + '">×</button></span>';
      });
      h += '</div>';
    } else { h += '<div class="asg-vacio">Sin grupos asignados.</div>'; }
    h += '<div class="asg-subcap">RUCs SUELTOS</div>';
    if (a.rucs.length) {
      a.rucs.forEach(function (cid) {
        var c = cById[cid]; if (!c) return;
        h += '<div class="asg-ruc"><div><div class="asg-rz">' + esc(c.razon) + '</div>'
          + '<div class="asg-rn">RUC ' + esc(c.ruc) + '</div></div>'
          + '<button class="asg-x" data-quitar-ruc="' + cid + '">×</button></div>';
      });
    } else { h += '<div class="asg-vacio">Sin RUCs sueltos.</div>'; }
    p.innerHTML = h;
    p.querySelectorAll('[data-quitar-grupo]').forEach(function (x) {
      var g = gById[x.dataset.quitarGrupo];
      armarQuitar(x, (g ? g.nombre : 'grupo') + ' de ' + nom1,
        function () { accionGrupo(x.dataset.quitarGrupo, 'quitar'); });
    });
    p.querySelectorAll('[data-quitar-ruc]').forEach(function (x) {
      var c = cById[x.dataset.quitarRuc];
      armarQuitar(x, (c ? c.razon : 'RUC') + ' de ' + nom1,
        function () { accionRuc(x.dataset.quitarRuc, 'quitar'); });
    });
  }

  function renderGrupos() {
    var a = asig[sel] || { grupos: [], rucs: [] };
    var cont = $('asg-grupos'); cont.innerHTML = '';
    DATA.grupos.filter(function (g) { return a.grupos.indexOf(g.id) === -1; }).forEach(function (g) {
      var d = document.createElement('div');
      d.className = 'asg-gcard'; d.dataset.gid = g.id;
      d.innerHTML = '<svg class="asg-grip" width="18" height="18" viewBox="0 0 24 24" fill="none">'
        + '<circle cx="9" cy="6" r="1.4" fill="currentColor"/><circle cx="15" cy="6" r="1.4" fill="currentColor"/>'
        + '<circle cx="9" cy="12" r="1.4" fill="currentColor"/><circle cx="15" cy="12" r="1.4" fill="currentColor"/>'
        + '<circle cx="9" cy="18" r="1.4" fill="currentColor"/><circle cx="15" cy="18" r="1.4" fill="currentColor"/></svg>'
        + '<div class="asg-gmid"><div class="asg-gnom">' + esc(g.nombre) + '</div>'
        + '<div class="asg-gmeta">' + g.n + ' RUCs · sin asignar</div></div>'
        + '<button class="asg-btn" type="button">Asignar</button>';
      d.querySelector('.asg-btn').addEventListener('click', function () { accionGrupo(g.id, 'asignar'); });
      arrastrable(d, g.id);
      cont.appendChild(d);
    });
    if (!cont.children.length) cont.innerHTML = '<div class="asg-vacio">Todos los grupos ya están asignados a ' + esc((nombreDe(sel) || '').split(' ')[0]) + '.</div>';
  }

  function renderPicker() {
    var q = ($('asg-buscar').value || '').toLowerCase().trim();
    var res = $('asg-res'); res.innerHTML = '';
    if (q.length < 2) return;
    var a = asig[sel] || { grupos: [], rucs: [] };
    DATA.contribs.filter(function (c) {
      return a.rucs.indexOf(c.id) === -1 &&
        ((c.razon || '').toLowerCase().indexOf(q) !== -1 || (c.ruc || '').indexOf(q) !== -1);
    }).slice(0, 8).forEach(function (c) {
      var d = document.createElement('div'); d.className = 'asg-ruc';
      d.innerHTML = '<div><div class="asg-rz">' + esc(c.razon) + '</div><div class="asg-rn">RUC ' + esc(c.ruc) + '</div></div>'
        + '<button class="asg-btn" type="button">Asignar</button>';
      d.querySelector('.asg-btn').addEventListener('click', function () { accionRuc(c.id, 'asignar'); $('asg-buscar').value = ''; renderPicker(); });
      res.appendChild(d);
    });
  }

  function renderTodo() { renderEquipo(); renderPanel(); renderGrupos(); renderPicker(); }

  async function post(url, body) {
    try {
      var r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      return await r.json();
    } catch (_) { return { ok: false, error: 'Error de red.' }; }
  }
  async function accionGrupo(gid, accion, aid) {
    aid = aid || sel;
    var j = await post('/api/asignar/grupo', { asistente_id: aid, grupo_id: gid, accion: accion });
    if (!j.ok) { toast(j.error || 'No se pudo.'); return; }
    asig[aid] = { grupos: j.grupos, rucs: j.rucs };
    var g = gById[gid];
    toast(accion === 'quitar' ? (g ? g.nombre + ' quitado de ' : 'Quitado de ') + nombreDe(aid).split(' ')[0]
                              : (g ? g.nombre + ' → ' : '→ ') + nombreDe(aid).split(' ')[0] + ' (en vivo)');
    renderTodo();
  }
  async function accionRuc(cid, accion) {
    var j = await post('/api/asignar/ruc', { asistente_id: sel, contribuyente_id: cid, accion: accion });
    if (!j.ok) { toast(j.error || 'No se pudo.'); return; }
    asig[sel] = { grupos: j.grupos, rucs: j.rucs };
    toast(accion === 'quitar' ? 'RUC quitado' : 'RUC asignado a ' + nombreDe(sel).split(' ')[0]);
    renderTodo();
  }

  // ── Arrastrar una tarjeta de grupo sobre un asistente (demo). Pointer events. ──
  function arrastrable(card, gid) {
    var grip = card.querySelector('.asg-grip');
    var ghost = null, activo = false;
    function inicio(e) {
      e.preventDefault(); activo = true; card.classList.add('arr');
      ghost = card.cloneNode(true); ghost.style.position = 'fixed'; ghost.style.zIndex = 60;
      ghost.style.width = card.offsetWidth + 'px'; ghost.style.pointerEvents = 'none'; ghost.style.opacity = '.9';
      document.body.appendChild(ghost); mover(e);
      document.addEventListener('pointermove', mover); document.addEventListener('pointerup', fin);
    }
    function mover(e) {
      if (ghost) { ghost.style.left = (e.clientX - 30) + 'px'; ghost.style.top = (e.clientY - 20) + 'px'; }
      document.querySelectorAll('.asg-asis').forEach(function (a) {
        var r = a.getBoundingClientRect();
        a.classList.toggle('drop', e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom);
      });
    }
    function fin(e) {
      document.removeEventListener('pointermove', mover); document.removeEventListener('pointerup', fin);
      card.classList.remove('arr'); if (ghost) { ghost.remove(); ghost = null; }
      var destino = null;
      document.querySelectorAll('.asg-asis').forEach(function (a) {
        var r = a.getBoundingClientRect();
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) destino = a.dataset.aid;
        a.classList.remove('drop');
      });
      activo = false;
      if (destino) accionGrupo(gid, 'asignar', destino);
    }
    grip.style.touchAction = 'none';
    grip.addEventListener('pointerdown', inicio);
  }

  var buscar = $('asg-buscar'); if (buscar) buscar.addEventListener('input', renderPicker);
  if (sel) renderTodo(); else { $('asg-panel').innerHTML = '<div class="asg-vacio">Aún no tienes asistentes. Invítalos desde tu cartera.</div>'; }
})();
