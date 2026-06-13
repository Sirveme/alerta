// Cambio de tema/modo en cliente

function aplicarTema(tema, modo) {
    document.documentElement.setAttribute('data-tema', tema);
    document.documentElement.setAttribute('data-modo', modo);
    localStorage.setItem('ui_tema', tema);
    localStorage.setItem('ui_modo', modo);

    // Enviar al backend para guardar en cookie
    const formData = new FormData();
    formData.append('tema', tema);
    formData.append('modo', modo);
    fetch('/ui/tema', { method: 'POST', body: formData });
}

function toggleModo() {
    const actual = document.documentElement.getAttribute('data-modo') || 'oscuro';
    const nuevo = actual === 'oscuro' ? 'claro' : 'oscuro';
    const tema = document.documentElement.getAttribute('data-tema') || 'vino-ambar';
    aplicarTema(tema, nuevo);
}

function elegirPaleta(tema) {
    const modo = document.documentElement.getAttribute('data-modo') || 'oscuro';
    aplicarTema(tema, modo);
}

// Listeners para botones
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-paleta]').forEach(btn => {
        btn.addEventListener('click', () => elegirPaleta(btn.dataset.paleta));
    });
    document.querySelectorAll('[data-toggle-modo]').forEach(btn => {
        btn.addEventListener('click', toggleModo);
    });
});
