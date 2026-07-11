/* ═══════════════════════════════════════════════════════════════════
   pwa.js — alerta.pe (zAlerta-64)
   Instalación como PWA: botón "Instalar app" en Android/Chrome
   (beforeinstallprompt) e instrucción en iOS/Safari (no soporta el prompt).
   Se oculta si la app ya está instalada (display-mode: standalone).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  var standalone = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  if (standalone) return;   // ya instalada → nada que hacer

  var ua = navigator.userAgent || '';
  var isIOS = /iphone|ipad|ipod/i.test(ua) && !window.MSStream;
  var deferred = null;

  function boton(texto, onClick) {
    var b = document.getElementById('pwa-instalar');
    if (b) return b;
    b = document.createElement('button');
    b.id = 'pwa-instalar';
    b.className = 'pwa-cta';
    b.type = 'button';
    b.innerHTML = '<span class="pwa-cta-ico" aria-hidden="true">＋</span> Instalar alerta.pe';
    b.addEventListener('click', onClick);
    document.body.appendChild(b);
    return b;
  }

  function cerrarHoja() {
    var h = document.getElementById('pwa-ios');
    if (h) h.remove();
  }

  function instruccionIOS() {
    if (document.getElementById('pwa-ios')) return;
    var h = document.createElement('div');
    h.id = 'pwa-ios';
    h.className = 'pwa-ios-fondo';
    h.innerHTML =
      '<div class="pwa-ios-hoja" role="dialog" aria-label="Cómo instalar">' +
      '<button class="pwa-ios-x" aria-label="Cerrar">✕</button>' +
      '<div class="pwa-ios-titulo"><span class="marca-punto"></span> Instala alerta.pe</div>' +
      '<p>En tu iPhone: toca <b>Compartir</b> <span class="pwa-ios-ico">⬆︎</span> ' +
      'y luego <b>Añadir a inicio</b>.</p>' +
      '<p class="pwa-ios-nota">Así queda como una app y los avisos llegan mejor.</p>' +
      '</div>';
    h.addEventListener('click', function (ev) {
      if (ev.target === h || ev.target.classList.contains('pwa-ios-x')) cerrarHoja();
    });
    document.body.appendChild(h);
  }

  if (isIOS) {
    // iOS/Safari no dispara beforeinstallprompt → botón con instrucción manual.
    boton('Instalar alerta.pe', instruccionIOS);
  } else {
    window.addEventListener('beforeinstallprompt', function (e) {
      e.preventDefault();
      deferred = e;
      var b = boton('Instalar alerta.pe', async function () {
        if (!deferred) return;
        b.disabled = true;
        deferred.prompt();
        try { await deferred.userChoice; } catch (_) {}
        deferred = null;
        b.remove();
      });
      b.hidden = false;
    });
  }

  window.addEventListener('appinstalled', function () {
    var b = document.getElementById('pwa-instalar');
    if (b) b.remove();
    cerrarHoja();
  });
})();
