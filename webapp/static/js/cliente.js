/* cliente.js — alerta.pe (zAlerta-97)
 * Vista del cliente para el contador: expandir filas período+tributo → documentos,
 * y capa de gestión Capa 1 (crear instrucción general/por-documento + marcar
 * terminado). Reusa el modal/estilos existentes; sin diálogos nativos. */
(function () {
  var cli = document.getElementById('cli');
  if (!cli) return;
  var cid = cli.dataset.cid;
  var tpl = document.getElementById('cli-form-tpl');

  function esc(s) {
    return (s || '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ── Expandir/colapsar una fila período+tributo ──
  cli.querySelectorAll('.cli-fila').forEach(function (fila) {
    fila.addEventListener('click', function (e) {
      if (e.target.closest('a')) return;
      var i = fila.dataset.i;
      var det = cli.querySelector('.cli-detalle[data-i="' + i + '"]');
      if (!det) return;
      var abierto = !det.hidden;
      det.hidden = abierto;
      fila.classList.toggle('cli-fila--abierta', !abierto);
    });
  });

  // ── Instrucción: html de una fila recién creada (coincide con _instruccion.html) ──
  function instrHTML(d) {
    var meta = '';
    if (d.fecha_limite) meta += '<span class="cli-instr-tag"><span class="material-symbols-outlined">event</span>' + esc(d.fecha_limite) + '</span>';
    meta += '<span class="cli-instr-estado cli-instr-estado--pend">Pendiente</span>'
      + '<button class="cli-instr-terminar" data-id="' + d.id + '">Marcar terminado</button>';
    return '<div class="cli-instr cli-instr--pendiente" data-id="' + d.id + '">'
      + '<div class="cli-instr-texto">' + esc(d.texto) + '</div>'
      + '<div class="cli-instr-meta">' + meta + '</div></div>';
  }

  // ── Abrir el formulario de nueva instrucción (clona la plantilla) ──
  cli.addEventListener('click', function (e) {
    var add = e.target.closest('.cli-instr-add');
    if (add) {
      if (add.nextElementSibling && add.nextElementSibling.classList.contains('cli-instr-form')) return;
      var frag = tpl.content.cloneNode(true);
      var form = frag.querySelector('form');
      form.dataset.notif = add.dataset.notif || '';
      add.after(form);
      form.querySelector('.cli-instr-txt').focus();
      form.querySelector('.cli-instr-cancel').addEventListener('click', function () { form.remove(); });
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        enviarInstruccion(form, add);
      });
      return;
    }
    var term = e.target.closest('.cli-instr-terminar');
    if (term) { terminar(term); return; }
  });

  function enviarInstruccion(form, add) {
    var txt = form.querySelector('.cli-instr-txt').value.trim();
    if (!txt) return;
    var fd = new FormData();
    fd.append('contribuyente_id', cid);
    fd.append('texto', txt);
    fd.append('notificacion_id', form.dataset.notif || '');
    fd.append('destinatario_persona_id', form.querySelector('.cli-instr-dest').value || '');
    fd.append('fecha_limite', form.querySelector('.cli-instr-fecha').value || '');
    var btn = form.querySelector('.cli-instr-ok'); btn.disabled = true;
    fetch('/api/instruccion', { method: 'POST', body: fd, credentials: 'include' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok) {
          var lista = add.previousElementSibling && add.previousElementSibling.classList.contains('cli-instr-lista')
            ? add.previousElementSibling
            : add.parentElement.querySelector('.cli-instr-lista');
          if (lista) lista.insertAdjacentHTML('beforeend', instrHTML(d));
          form.remove();
        } else { btn.disabled = false; }
      })
      .catch(function () { btn.disabled = false; });
  }

  function terminar(btn) {
    var id = btn.dataset.id;
    btn.disabled = true;
    fetch('/api/instruccion/' + id + '/terminar', { method: 'POST', credentials: 'include' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok) {
          var box = btn.closest('.cli-instr');
          box.classList.remove('cli-instr--pendiente');
          box.classList.add('cli-instr--terminado');
          var meta = box.querySelector('.cli-instr-meta');
          meta.querySelector('.cli-instr-estado').outerHTML =
            '<span class="cli-instr-estado cli-instr-estado--term">✓ Terminado' + (d.terminado_at ? ' · ' + esc(d.terminado_at) : '') + '</span>';
          btn.remove();
        } else { btn.disabled = false; }
      })
      .catch(function () { btn.disabled = false; });
  }
})();
