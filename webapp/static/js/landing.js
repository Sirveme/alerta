/* ═══════════════════════════════════════════════════════════════════
   landing.js — alerta.pe (zAlerta-11a)
   - Demo del celular: bucle suave push → tarjeta de detalle (~5s).
   - Modal de autorización de cada testimonio (transparencia: RUC real).
   (Los modales propios viven en app.js, cargado antes en base.html.)
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  // ── Demo del celular ──
  const phone = document.getElementById('phone');
  if (phone) {
    const fases = ['push', 'card'];
    let i = 0;
    function pintar() {
      fases.forEach((f) => {
        const el = phone.querySelector('[data-ph="' + f + '"]');
        if (el) el.classList.toggle('on', fases[i] === f);
      });
    }
    pintar();
    setInterval(() => { i = (i + 1) % fases.length; pintar(); }, 2600);
  }

  // ── Modal de autorización del testimonio ──
  document.querySelectorAll('.lp-testi-i').forEach((b) => {
    b.addEventListener('click', () => {
      const nombre = b.dataset.nombre || 'Este cliente';
      const ruc = b.dataset.ruc || '';
      if (typeof modalHTML === 'function') {
        modalHTML('Testimonio autorizado',
          '<p>' + nombre + ' autorizó este testimonio.</p>'
          + (ruc ? '<p class="muted">RUC ' + ruc + '</p>' : ''),
          () => cerrarModal(), 'Entendido');
      }
    });
  });
})();
