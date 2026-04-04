/**
 * temas.js — Cambio de tema y fuente con persistencia localStorage + sync backend.
 *
 * Decisiones:
 * - Se aplica inmediatamente al cambiar (sin esperar backend).
 * - Se persiste en localStorage para carga instantánea en siguientes visitas.
 * - Se sincroniza con backend (PUT /config/usuario) en segundo plano.
 * - Si el backend falla, el cambio local persiste igualmente.
 */

const Temas = {
    TEMAS: ['dark', 'semi', 'feminine', 'classic'],
    SIZES: ['sm', 'md', 'lg'],

    init() {
        // Restaurar de localStorage (antes de que el DOM renderice)
        const savedTema = localStorage.getItem('alerta_tema');
        const savedSize = localStorage.getItem('alerta_fuente_size');
        if (savedTema) document.documentElement.setAttribute('data-theme', savedTema);
        if (savedSize) document.documentElement.setAttribute('data-size', savedSize);
    },

    getTema() {
        return document.documentElement.getAttribute('data-theme') || 'semi';
    },

    getSize() {
        return document.documentElement.getAttribute('data-size') || 'md';
    },

    setTema(tema) {
        if (!this.TEMAS.includes(tema)) return;
        document.documentElement.setAttribute('data-theme', tema);
        localStorage.setItem('alerta_tema', tema);
        this._syncBackend({ tema });
    },

    setSize(size) {
        if (!this.SIZES.includes(size)) return;
        document.documentElement.setAttribute('data-size', size);
        localStorage.setItem('alerta_fuente_size', size);
        this._syncBackend({ fuente_size: size });
    },

    async _syncBackend(data) {
        try {
            await fetch('/config/usuario', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
        } catch {
            // Fallo silencioso — el cambio local ya se aplicó
        }
    },
};

// Aplicar tema guardado lo antes posible
Temas.init();
