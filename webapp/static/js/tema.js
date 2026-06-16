/* ═══════════════════════════════════════════════════════════════════
   tema.js — alerta.pe (zAlerta-02)
   Sistema de temas de 3 ejes (color × borde × franja).
   Aplica al instante togglear data-attributes en <html> y persiste en
   localStorage('alertape_tema'). El anti-flash ya corrió en <head>.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const CLAVE = 'alertape_tema';
  const DEF = { color: 'dark', borde: 'recto', franja: 'si' };
  const root = document.documentElement;

  function leer() {
    var t;
    try { t = Object.assign({}, DEF, JSON.parse(localStorage.getItem(CLAVE) || '{}')); }
    catch (_) { t = Object.assign({}, DEF); }
    if (t.color === 'femenino') t.color = 'violeta';   // tema renombrado
    return t;
  }
  function aplicar(t) {
    root.setAttribute('data-color', t.color);
    root.setAttribute('data-borde', t.borde);
    root.setAttribute('data-franja', t.franja);
    localStorage.setItem(CLAVE, JSON.stringify(t));
    marcarActivos(t);
  }
  function marcarActivos(t) {
    document.querySelectorAll('#modal-tema .swatch').forEach((s) => {
      const eje = s.closest('.swatches').dataset.eje;
      s.classList.toggle('activo', t[eje] === s.dataset.valor);
    });
  }

  const estado = leer();
  aplicar(estado);  // asegura coherencia con localStorage

  // Abrir modal de tema
  document.getElementById('btn-tema')?.addEventListener('click', () => {
    marcarActivos(leer());
    document.getElementById('modal-tema').hidden = false;
  });
  document.getElementById('tema-cerrar')?.addEventListener('click', () => {
    document.getElementById('modal-tema').hidden = true;
  });
  document.getElementById('modal-tema')?.addEventListener('click', (e) => {
    if (e.target.id === 'modal-tema') e.currentTarget.hidden = true;
  });

  // Elegir swatch → aplica al instante
  document.querySelectorAll('#modal-tema .swatch').forEach((s) => {
    s.addEventListener('click', () => {
      const eje = s.closest('.swatches').dataset.eje;
      const t = leer();
      t[eje] = s.dataset.valor;
      aplicar(t);
    });
  });
})();
