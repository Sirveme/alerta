/* cartera.js — alerta.pe (zAlerta-91)
 * Orden (Razón Social / Responsable), búsqueda (RUC/razón/responsable) y
 * BÚSQUEDA POR VOZ (Web Speech API nativa, sin servicios pagos). Todo del
 * lado del cliente sobre las tarjetas ya renderizadas. */
(function () {
  var grid = document.getElementById('cra-grid');
  if (!grid) return;
  var q = document.getElementById('cra-q');
  var orden = document.getElementById('cra-orden');
  var voz = document.getElementById('cra-voz');
  var nores = document.getElementById('cra-nores');
  var cards = Array.prototype.slice.call(grid.querySelectorAll('.cra-card'));

  function norm(s) {
    return (s || '').toLowerCase()
      .normalize('NFD').replace(new RegExp('[\u0300-\u036f]','g'), '');   // sin tildes
  }

  function filtrar() {
    var t = norm(q.value.trim());
    var visibles = 0;
    cards.forEach(function (c) {
      var hay = !t ||
        norm(c.dataset.razon).indexOf(t) >= 0 ||
        (c.dataset.ruc || '').indexOf(t) >= 0 ||
        norm(c.dataset.responsable).indexOf(t) >= 0;
      c.style.display = hay ? '' : 'none';
      if (hay) visibles++;
    });
    if (nores) nores.hidden = visibles !== 0;
  }

  function ordenar() {
    var clave = orden.value === 'responsable' ? 'responsable' : 'razon';
    var arr = cards.slice().sort(function (a, b) {
      return norm(a.dataset[clave]).localeCompare(norm(b.dataset[clave]), 'es');
    });
    arr.forEach(function (c) { grid.appendChild(c); });   // re-inserta en orden
  }

  if (q) q.addEventListener('input', filtrar);
  if (orden) orden.addEventListener('change', ordenar);

  // ── Búsqueda por voz (Web Speech API) ──
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (voz) {
    if (!SR) {
      voz.style.display = 'none';   // navegador sin soporte → sin botón
    } else {
      var rec = new SR();
      rec.lang = 'es-PE';
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      var activo = false;
      voz.addEventListener('click', function () {
        if (activo) { rec.stop(); return; }
        try { rec.start(); } catch (e) { /* ya activo */ }
      });
      rec.onstart = function () { activo = true; voz.classList.add('grabando'); };
      rec.onend = function () { activo = false; voz.classList.remove('grabando'); };
      rec.onerror = function () { activo = false; voz.classList.remove('grabando'); };
      rec.onresult = function (ev) {
        var txt = (ev.results[0][0].transcript || '').replace(/[.,]$/, '').trim();
        if (txt) { q.value = txt; filtrar(); q.focus(); }
      };
    }
  }
})();
