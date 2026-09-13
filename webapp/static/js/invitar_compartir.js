/* invitar_compartir.js — Capa 1. Render del link de invitación generado como algo
   PARA COMPARTIR con el invitado (no para que el creador lo abra). Reusable en las
   3 invitaciones (socio, asistente, cliente).
   Uso: window.pintarInvitacion(outEl, {link, wa_url}, "el socio"); */
(function () {
  window.pintarInvitacion = function (outEl, data, quien) {
    if (!outEl) return;
    var link = (data && data.link) || '';
    quien = quien || 'la persona invitada';
    outEl.hidden = false;
    outEl.className = 'inv-share';
    outEl.innerHTML =
        '<div class="inv-share-tit">✓ Invitación creada</div>'
      + '<p class="inv-share-txt">Comparte este link con <b class="inv-quien"></b> '
      + 'para que active su acceso. <b>No lo abras tú</b> — es para esa persona.</p>'
      + '<div class="inv-share-link" tabindex="0"></div>'
      + '<div class="inv-share-btns">'
      + '  <button type="button" class="btn" data-copiar>Copiar link</button>'
      + (data && data.wa_url ? '  <button type="button" class="btn btn--whatsapp" data-wa>Compartir por WhatsApp</button>' : '')
      + '  <button type="button" class="btn btn--sec" data-share hidden>Compartir…</button>'
      + '</div>'
      + '<div class="inv-share-msg" data-msg aria-live="polite"></div>';
    // TEXTO plano (no <a>): no invita a hacer clic. textContent = anti-inyección.
    outEl.querySelector('.inv-quien').textContent = quien;
    outEl.querySelector('.inv-share-link').textContent = link;
    var msg = outEl.querySelector('[data-msg]');

    outEl.querySelector('[data-copiar]').addEventListener('click', function () {
      var done = function () { msg.textContent = 'Link copiado. Pégalo donde quieras enviarlo.'; };
      var fallback = function () {
        try {
          var r = document.createRange(); r.selectNodeContents(outEl.querySelector('.inv-share-link'));
          var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          msg.textContent = 'Selecciona y copia el link (Ctrl/Cmd+C).';
        } catch (e) { msg.textContent = 'Copia el link de arriba.'; }
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(link).then(done).catch(fallback);
      } else { fallback(); }
    });

    var wa = outEl.querySelector('[data-wa]');
    if (wa) wa.addEventListener('click', function () {
      window.open(data.wa_url, '_blank', 'noopener');
    });

    var share = outEl.querySelector('[data-share]');
    if (share && navigator.share) {
      share.hidden = false;
      share.addEventListener('click', function () {
        navigator.share({ title: 'alerta.pe', text: 'Activa tu acceso a alerta.pe:', url: link })
          .catch(function () {});
      });
    }
  };
})();
