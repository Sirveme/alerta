/* ═══════════════════════════════════════════════════════════════════
   onboarding.js — alerta.pe (zAlerta-34 Paso 4)
   Indicador de espera ESTILIZADO durante la primera descarga del buzón.
   Es una animación TRANQUILIZADORA (las frases rotan por TIEMPO, no porque
   el backend reporte progreso real). En conexiones lentas (Iquitos) el
   movimiento evita que el usuario crea que se colgó y cierre la app.
   Animación CSS pura + este JS mínimo (sin librerías).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  // Monta el indicador estilizado en `cont` y rota las frases por TIEMPO.
  // Devuelve un handle con .detener() para limpiar el intervalo (zAlerta-36).
  function obMontar(cont, aA, aP) {
    if (!cont) return { detener: function () {} };
    aA = aA || (new Date().getFullYear() + '');
    aP = aP || (new Date().getFullYear() - 1 + '');

    // Frase + clase de color (las clases mapean a variables de marca en el CSS).
    const FRASES = [
      ['Tu buzón tiene tus notificaciones de SUNAT', 'verde'],
      ['Hoy traemos ' + aP + ' y ' + aA + ' completos', 'cian'],
      ['Podrás pedir años anteriores cuando quieras', 'ambar'],
      ['Terminamos ' + aP, 'verde'],
      ['Terminamos ' + aA, 'verde'],
      ['Organizando y clasificando tus notificaciones', 'cian'],
      ['Guardando tus documentos en tu dispositivo', 'ambar'],
      ['Casi listo', 'rosa'],
      ['Lo URGENTE estará primero', 'rosa'],
    ];

    cont.innerHTML =
      '<div class="ob-inner">'
      + '<div class="ob-head">'
      + '<span class="ob-spin material-symbols-outlined">refresh</span>'
      + '<span class="ob-titulo">Conectando con tu buzón SUNAT</span>'
      + '</div>'
      + '<div class="ob-barra"><span class="ob-barra-fill"></span></div>'
      + '<div class="ob-frase ob-c-verde" id="ob-frase">' + FRASES[0][0] + '</div>'
      + '<div class="ob-nota">Esto puede tardar un momento si tu conexión es lenta. '
      + 'No cierres la app.</div>'
      + '</div>';

    const fraseEl = cont.querySelector('#ob-frase');
    let i = 0;
    const t = setInterval(function () {
      i = (i + 1) % FRASES.length;
      fraseEl.style.opacity = '0';
      setTimeout(function () {
        fraseEl.textContent = FRASES[i][0];
        fraseEl.className = 'ob-frase ob-c-' + FRASES[i][1];
        fraseEl.style.opacity = '1';
      }, 380);
    }, 2200);
    return { detener: function () { clearInterval(t); } };
  }

  window.obMontar = obMontar;

  // Auto-arranque: si la página ya trae el indicador (primera lectura en curso).
  const cont = document.getElementById('ob-indicador');
  if (cont) obMontar(cont, cont.dataset.anioActual, cont.dataset.anioAnterior);
})();
