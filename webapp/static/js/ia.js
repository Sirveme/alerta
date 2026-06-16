/* ═══════════════════════════════════════════════════════════════════
   ia.js — alerta.pe (zAlerta-02)
   Barra de consulta IA (texto + voz). FAB abre el bottom sheet; el input
   se envía a POST /voz/consultar y renderiza la TARJETA INTELIGENTE con
   Escuchar (SpeechSynthesis es-PE) / Compartir (wa.me) / Ver PDF / Reaccionar.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const fab = document.getElementById('fab-ia');
  const hoja = document.getElementById('hoja-ia');
  const fondo = document.getElementById('hoja-fondo');
  const input = document.getElementById('ia-input');
  const salida = document.getElementById('ia-resultado');
  if (!fab || !hoja) return;

  // ── Abrir / cerrar bottom sheet ──
  function abrir() {
    hoja.classList.add('abierta'); hoja.setAttribute('aria-hidden', 'false');
    fondo.hidden = false; setTimeout(() => input?.focus(), 120);
  }
  function cerrar() {
    hoja.classList.remove('abierta'); hoja.setAttribute('aria-hidden', 'true');
    fondo.hidden = true; pararVoz();
  }
  fab.addEventListener('click', () => hoja.classList.contains('abierta') ? cerrar() : abrir());
  fondo.addEventListener('click', cerrar);

  // ── Enviar consulta ──
  document.getElementById('ia-enviar')?.addEventListener('click', enviar);
  input?.addEventListener('keydown', (e) => { if (e.key === 'Enter') enviar(); });
  document.querySelectorAll('.ia-sug').forEach((s) =>
    s.addEventListener('click', () => { input.value = s.textContent.trim(); enviar(); }));

  async function enviar() {
    const texto = (input.value || '').trim();
    if (!texto) return;
    salida.innerHTML = '<div class="tarjeta"><span class="spinner"></span> Consultando…</div>';
    try {
      const r = await fetch('/voz/consultar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texto })
      });
      if (r.status === 401) { location.href = '/login'; return; }
      const j = await r.json();
      if (j.ok) renderTarjeta(j.tarjeta);
      else salida.innerHTML = simple(j.mensaje || 'No pude procesar la consulta.');
    } catch (_) { salida.innerHTML = simple('Error de red, reintenta.'); }
  }

  // ── Voz (Web Speech) ──
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = document.getElementById('ia-mic');
  let rec = null, grabando = false;
  if (Rec && mic) {
    rec = new Rec(); rec.lang = 'es-PE'; rec.interimResults = false; rec.maxAlternatives = 1;
    mic.addEventListener('click', () => { if (grabando) rec.stop(); else try { rec.start(); } catch (_) {} });
    rec.onstart = () => { grabando = true; mic.classList.add('grabando'); };
    rec.onend = () => { grabando = false; mic.classList.remove('grabando'); };
    rec.onerror = () => { grabando = false; mic.classList.remove('grabando'); };
    rec.onresult = (ev) => {
      input.value = ev.results[0][0].transcript;
      enviar();  // se envía directo; el usuario igual puede editar y reenviar
    };
  } else if (mic) {
    mic.addEventListener('click', () =>
      confirmarModal('Voz no disponible',
        'Tu navegador no soporta dictado. Escribe la consulta.', () => {}));
  }
  function pararVoz() {
    try { if (grabando) rec.stop(); } catch (_) {}
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  // ── Render de la tarjeta inteligente ──
  function simple(msg) {
    return '<div class="tarjeta acento acento--informativa voz-tarjeta">'
      + '<div class="voz-respuesta">' + esc(msg) + '</div></div>';
  }

  function renderTarjeta(t) {
    const clase = 'acento--' + (t.urgencia || 'informativa');
    const wa = 'https://wa.me/?text=' + encodeURIComponent(textoWhatsapp(t));
    let acciones = '<div class="acciones-soc">'
      + '<button class="acc-soc" data-ia="escuchar" aria-label="Escuchar"><i class="ti ti-volume"></i><span>Escuchar</span></button>'
      + '<a class="acc-soc" target="_blank" rel="noopener" href="' + wa + '" aria-label="Compartir"><i class="ti ti-brand-whatsapp"></i><span>Compartir</span></a>';
    if (t.adjunto_url) acciones += '<a class="acc-soc" target="_blank" href="' + t.adjunto_url + '" aria-label="Ver PDF"><i class="ti ti-file-text"></i><span>Ver PDF</span></a>';
    if (t.contribuyente_id) acciones += '<a class="acc-soc" href="/contribuyentes/' + t.contribuyente_id + '/notificaciones" aria-label="Ver ficha"><i class="ti ti-list-details"></i><span>Ficha</span></a>';
    acciones += '</div>';

    // Varias coincidencias (zAlerta-08): chips para elegir cliente.
    let opciones = '';
    if (t.opciones && t.opciones.length) {
      opciones = '<div class="ia-sugerencias">'
        + t.opciones.map((o) => '<button class="ia-sug" data-opc="' + esc(o.nombre) + '">' + esc(o.nombre) + '</button>').join('')
        + '</div>';
    }

    let meta = '';
    if (t.monto) meta += '<span class="chip-tipo">' + esc(t.monto) + '</span> ';
    if (t.tipo_documento_label || t.tipo_documento) meta += '<span class="chip-tipo">' + esc(t.tipo_documento_label || t.tipo_documento) + '</span> ';
    if (t.plazo) meta += '<span class="chip-tipo">Vence ' + esc(t.plazo) + '</span> ';

    let reacciones = '';
    if (t.notificacion_id) {
      reacciones = '<div class="acciones-soc" data-ia-rx="' + esc(t.notificacion_id) + '">'
        + '<button class="acc-soc rx" data-tipo="util" aria-label="Útil"><i class="ti ti-thumb-up"></i><span>Útil</span></button>'
        + '<button class="acc-soc rx" data-tipo="no_util" aria-label="No útil"><i class="ti ti-thumb-down"></i><span>No útil</span></button>'
        + '<button class="acc-soc rx" data-tipo="destacada" aria-label="Destacar"><i class="ti ti-star"></i><span>Destacar</span></button></div>';
    }

    salida.innerHTML =
      '<div class="tarjeta acento ' + clase + ' voz-tarjeta">'
      + '<div class="fila spread"><strong>' + esc(t.titulo || '') + '</strong>'
      + (t.ruc ? '<span class="muted">RUC ' + esc(t.ruc) + '</span>' : '') + '</div>'
      + '<div class="voz-respuesta">' + esc(t.respuesta || '') + '</div>'
      + (meta ? '<div class="notif-linea">' + meta + '</div>' : '')
      + opciones + acciones + reacciones
      + '<p class="voz-trans">«' + esc(t.transcripcion || '') + '»</p></div>';

    salida.querySelector('[data-ia="escuchar"]')?.addEventListener('click', () => hablar(t.respuesta));
    hablar(t.respuesta);  // lee al aparecer (manos libres)

    // Elegir entre varias coincidencias → reconsulta por ese nombre.
    salida.querySelectorAll('[data-opc]').forEach((b) =>
      b.addEventListener('click', () => { input.value = b.dataset.opc; enviar(); }));

    const rx = salida.querySelector('[data-ia-rx]');
    rx?.querySelectorAll('.rx').forEach((b) => b.addEventListener('click', async () => {
      const res = await fetch(`/notificaciones/${rx.dataset.iaRx}/reaccion`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tipo: b.dataset.tipo })
      });
      const j = await res.json();
      if (j.ok) {
        rx.querySelectorAll('.rx').forEach((x) =>
          x.classList.remove('activa', 'activa--util', 'activa--destacada'));
        if (j.tipo) {
          const el = rx.querySelector(`[data-tipo="${j.tipo}"]`);
          el?.classList.add('activa');
          if (j.tipo === 'util') el?.classList.add('activa--util');
          if (j.tipo === 'destacada') el?.classList.add('activa--destacada');
        }
      }
    }));
  }

  function textoWhatsapp(t) {
    let m = '*alerta.pe* — ' + (t.titulo || '') + '\n' + (t.respuesta || '');
    if (t.plazo) m += '\nVence: ' + t.plazo;
    return m;
  }
  function hablar(texto) {
    if (!texto || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = 'es-PE'; u.rate = 1.0; window.speechSynthesis.speak(u);
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Exponer abrir() para el menú de acciones rápidas o atajos
  window.abrirIA = abrir;
})();
