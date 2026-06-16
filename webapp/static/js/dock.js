/* ═══════════════════════════════════════════════════════════════════
   dock.js — alerta.pe (zAlerta-09 Pieza 5)
   Dock lateral derecho: abrir/cerrar, selector de TAMAÑO DE TEXTO
   (persistido en localStorage como data-fuente en <html>), acceso al
   selector de TEMA existente e "Ir arriba".
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const dock = document.getElementById('dock');
  if (!dock) return;
  const root = document.documentElement;
  const CLAVE_FUENTE = 'alertape_fuente';
  const panel = document.getElementById('dock-panel');
  const toggle = document.getElementById('dock-toggle');

  // ── Abrir / cerrar el panel ──
  function abrir(v) {
    dock.classList.toggle('abierto', v);
    panel.hidden = !v;
    toggle.setAttribute('aria-expanded', v ? 'true' : 'false');
  }
  toggle?.addEventListener('click', () => abrir(panel.hidden));
  document.addEventListener('click', (e) => {
    if (!dock.contains(e.target) && !panel.hidden) abrir(false);
  });

  // ── Tamaño de texto (data-fuente en <html>, persistido) ──
  function fuenteActual() {
    return localStorage.getItem(CLAVE_FUENTE) || root.getAttribute('data-fuente') || 'mediana';
  }
  function marcarFuente(f) {
    document.querySelectorAll('.dock-A').forEach((b) =>
      b.classList.toggle('activo', b.dataset.fuente === f));
  }
  function aplicarFuente(f) {
    root.setAttribute('data-fuente', f);
    try { localStorage.setItem(CLAVE_FUENTE, f); } catch (_) {}
    marcarFuente(f);
  }
  document.querySelectorAll('.dock-A').forEach((b) =>
    b.addEventListener('click', () => aplicarFuente(b.dataset.fuente)));
  marcarFuente(fuenteActual());

  // ── Acceso al selector de TEMA existente (reusa el modal de tema.js) ──
  document.getElementById('dock-tema')?.addEventListener('click', () => {
    abrir(false);
    document.getElementById('btn-tema')?.click();
  });

  // ── Ir arriba ──
  document.getElementById('dock-arriba')?.addEventListener('click', () => {
    abrir(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // CAPA 2 (pendiente): aquí se cablearían los accesos al ecosistema
  // (pagoOK, QueVendi, Facturalo) y "contactar cliente" cuando existan.
})();
