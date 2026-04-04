/**
 * voz.js — Módulo completo de voz para alerta.pe.
 *
 * Flujo:
 * 1. Usuario toca botón 🎤 → Web Speech API (es-PE, gratis, local)
 * 2. Transcripción en tiempo real visible
 * 3. Silencio 2s → envía automáticamente POST /api/voz/consulta
 * 4. Muestra respuesta en panel + cards/tabla según datos
 * 5. Web Speech Synthesis lee la respuesta en voz alta
 * 6. Si acción contiene "cambiar_empresa:ID" → actualizar cabecera
 *
 * Comandos especiales (sin servidor):
 * "para"|"detente" → detener TTS
 * "repite" → releer última respuesta
 */

class VozManager {
    constructor() {
        this.recognition = null;
        this.synthesis = window.speechSynthesis;
        this.ultimaRespuesta = '';
        this.escuchando = false;
        this.silenceTimer = null;
        this.panel = null;
        this.btn = null;
        // Whisper dual flow: graba audio en paralelo con Web Speech
        this.mediaRecorder = null;
        this.audioChunks = [];
        this._init();
    }

    _init() {
        this.btn = document.getElementById('btn-voz');
        this._crearPanel();

        // Verificar soporte
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            if (this.btn) this.btn.title = 'Tu navegador no soporta voz';
            return;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.lang = 'es-PE';
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.maxAlternatives = 1;

        this.recognition.onresult = (e) => this._onResult(e);
        this.recognition.onend = () => this._onEnd();
        this.recognition.onerror = (e) => this._onError(e);

        // Cargar velocidad de voz del perfil
        const user = JSON.parse(localStorage.getItem('alerta_user') || '{}');
        this.velocidad = user.velocidad_voz === 'lenta' ? 0.8 :
                         user.velocidad_voz === 'rapida' ? 1.3 : 1.0;
    }

    _crearPanel() {
        if (document.getElementById('voz-panel')) return;

        const panel = document.createElement('div');
        panel.className = 'voz-panel';
        panel.id = 'voz-panel';
        panel.innerHTML = `
            <div class="voz-panel__handle"></div>
            <div class="voz-panel__transcripcion" id="voz-transcripcion"></div>
            <div class="voz-panel__respuesta" id="voz-respuesta"></div>
            <div class="voz-cards" id="voz-cards"></div>
            <div class="voz-panel__actions">
                <button class="voz-panel__btn" onclick="voz.hablar(voz.ultimaRespuesta)">
                    <i class="ph-speaker-high"></i> Escuchar
                </button>
                <button class="voz-panel__btn voz-panel__btn--primary" onclick="voz.activar()">
                    <i class="ph-microphone"></i> Nueva consulta
                </button>
                <button class="voz-panel__btn" onclick="voz.cerrarPanel()">
                    Cerrar
                </button>
            </div>
            <div class="voz-historial" id="voz-historial"></div>
        `;
        document.body.appendChild(panel);
        this.panel = panel;

        // Cerrar al tocar fuera
        panel.querySelector('.voz-panel__handle').addEventListener('click', () => this.cerrarPanel());
    }

    activar() {
        if (!this.recognition) {
            if (typeof showToast === 'function') showToast('Tu navegador no soporta voz');
            return;
        }

        if (this.escuchando) {
            this.detener();
            return;
        }

        // Detener TTS si estaba hablando
        this.synthesis.cancel();

        this.escuchando = true;
        this._setEstado('escuchando');

        // Mostrar panel con transcripción vacía
        document.getElementById('voz-transcripcion').textContent = 'Escuchando...';
        document.getElementById('voz-respuesta').textContent = '';
        document.getElementById('voz-cards').innerHTML = '';
        this.panel.classList.add('visible');

        try {
            this.recognition.start();
        } catch {
            // Ya estaba escuchando
        }

        // Iniciar grabación de audio para Whisper (flujo dual)
        this._iniciarGrabacion();
    }

    detener() {
        this.escuchando = false;
        this._setEstado('idle');
        clearTimeout(this.silenceTimer);
        try { this.recognition.stop(); } catch {}
    }

    async enviarConsulta(texto) {
        if (!texto.trim()) return;

        // Comandos especiales locales
        const cmd = texto.toLowerCase().trim();
        if (cmd === 'para' || cmd === 'detente' || cmd === 'stop') {
            this.synthesis.cancel();
            return;
        }
        if (cmd === 'repite' || cmd === 'repite eso') {
            this.hablar(this.ultimaRespuesta);
            return;
        }

        this._setEstado('procesando');
        document.getElementById('voz-transcripcion').textContent = `"${texto}"`;
        document.getElementById('voz-respuesta').textContent = 'Procesando...';

        try {
            const res = await fetch('/api/voz/consulta', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ texto }),
            });

            if (!res.ok) {
                document.getElementById('voz-respuesta').textContent = 'Error al procesar la consulta';
                this._setEstado('idle');
                return;
            }

            const data = await res.json();
            this.mostrarRespuesta(data);

            // Cambio de empresa
            if (data.empresa_cambiada && data.nueva_empresa_id) {
                this._cambiarEmpresa(data.nueva_empresa_id);
            }

            // Acción de navegación
            if (data.accion && data.accion.startsWith('ir_a:')) {
                const url = data.accion.replace('ir_a:', '');
                setTimeout(() => window.location.href = url, 2000);
            }

        } catch (err) {
            document.getElementById('voz-respuesta').textContent = 'Error de conexión';
            this._setEstado('idle');
        }
    }

    mostrarRespuesta(data) {
        this._setEstado('respondiendo');

        const respuesta = data.respuesta_texto || data.respuesta_display || '';
        this.ultimaRespuesta = respuesta;

        document.getElementById('voz-respuesta').textContent = respuesta;

        // Renderizar cards si hay datos
        const cardsEl = document.getElementById('voz-cards');
        cardsEl.innerHTML = '';

        if (data.datos) {
            const d = data.datos;

            // KPIs numéricos
            const kpis = ['total', 'cobrado', 'pendiente', 'total_comprado', 'total_pendiente'];
            for (const key of kpis) {
                if (d[key] !== undefined) {
                    const label = key.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
                    const colorClass = key.includes('cobrado') || key === 'total' ? '--green' :
                                       key.includes('pendiente') ? '--gold' : '';
                    cardsEl.innerHTML += `
                        <div class="voz-card">
                            <div class="voz-card__label">${label}</div>
                            <div class="voz-card__valor voz-card__valor${colorClass}">S/ ${Number(d[key]).toLocaleString('es-PE', {minimumFractionDigits: 2})}</div>
                        </div>
                    `;
                }
            }

            // Cantidad
            if (d.cantidad !== undefined) {
                cardsEl.innerHTML += `
                    <div class="voz-card">
                        <div class="voz-card__label">Cantidad</div>
                        <div class="voz-card__valor">${d.cantidad}</div>
                    </div>
                `;
            }

            if (d.alertas !== undefined) {
                cardsEl.innerHTML += `
                    <div class="voz-card">
                        <div class="voz-card__label">Alertas activas</div>
                        <div class="voz-card__valor voz-card__valor--red">${d.alertas}</div>
                    </div>
                `;
            }
        }

        this.panel.classList.add('visible');

        // TTS automático
        setTimeout(() => {
            this.hablar(respuesta);
            this._setEstado('idle');
        }, 300);
    }

    hablar(texto, velocidad) {
        if (!texto || !this.synthesis) return;

        this.synthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(texto);
        utterance.lang = 'es-PE';
        utterance.rate = velocidad || this.velocidad;
        utterance.volume = 0.9;

        // Buscar voz en español
        const voces = this.synthesis.getVoices();
        const vozEs = voces.find(v => v.lang.startsWith('es'));
        if (vozEs) utterance.voice = vozEs;

        this.synthesis.speak(utterance);
    }

    cerrarPanel() {
        if (this.panel) this.panel.classList.remove('visible');
        this.synthesis.cancel();
        this.detener();
    }

    // ── Internos ────────────────────────────────────────────────

    _onResult(event) {
        let transcript = '';
        let isFinal = false;

        for (let i = event.resultIndex; i < event.results.length; i++) {
            transcript += event.results[i][0].transcript;
            if (event.results[i].isFinal) isFinal = true;
        }

        document.getElementById('voz-transcripcion').textContent = transcript;

        // Reset silence timer
        clearTimeout(this.silenceTimer);

        if (isFinal) {
            // Silencio de 2s después del resultado final →
            // Detener grabación Whisper + enviar texto final
            this.silenceTimer = setTimeout(async () => {
                this.detener();
                // Flujo dual: intentar Whisper primero, fallback a Web Speech
                const textoFinal = await this._transcribirConWhisper(transcript);
                this.enviarConsulta(textoFinal);
            }, 2000);
        }
    }

    _onEnd() {
        if (this.escuchando) {
            // Reiniciar si se detuvo inesperadamente
            try { this.recognition.start(); } catch {}
        }
    }

    _onError(event) {
        if (event.error === 'no-speech') return; // Normal
        if (event.error === 'aborted') return;   // Usuario detuvo
        console.warn('Speech error:', event.error);
        this.detener();
    }

    _setEstado(estado) {
        if (!this.btn) return;
        this.btn.classList.remove('escuchando', 'procesando', 'respondiendo');
        if (estado !== 'idle') this.btn.classList.add(estado);
    }

    async _cambiarEmpresa(empresaId) {
        try {
            await fetch('/auth/cambiar-empresa', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ empresa_id: empresaId }),
            });
            // Actualizar estado local
            const user = JSON.parse(localStorage.getItem('alerta_user') || '{}');
            user.empresa_activa_id = empresaId;
            localStorage.setItem('alerta_user', JSON.stringify(user));
        } catch {}
    }

    // ── Whisper (flujo dual) ────────────────────────────────────

    async _iniciarGrabacion() {
        /* Graba audio con MediaRecorder en paralelo con Web Speech API.
           El audio se envía a Whisper al finalizar para transcripción precisa. */
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.audioChunks = [];
            this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) this.audioChunks.push(e.data);
            };
            this.mediaRecorder.start();
        } catch {
            // Sin permiso de micrófono o sin soporte → solo Web Speech
            this.mediaRecorder = null;
        }
    }

    _detenerGrabacion() {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
            // Detener tracks del stream
            this.mediaRecorder.stream.getTracks().forEach(t => t.stop());
        }
    }

    async _transcribirConWhisper(textoWebSpeech) {
        /* Envía audio grabado a Whisper. Si falla, usa textoWebSpeech como fallback. */
        this._detenerGrabacion();

        if (!this.audioChunks.length) return textoWebSpeech;

        try {
            // Esperar un momento para que el último chunk se agregue
            await new Promise(r => setTimeout(r, 200));

            const blob = new Blob(this.audioChunks, { type: 'audio/webm' });

            // Solo enviar a Whisper si el audio es >0.5s (evitar requests vacíos)
            if (blob.size < 5000) return textoWebSpeech;

            const form = new FormData();
            form.append('audio', blob, 'consulta.webm');

            const res = await fetch('/api/voz/transcribir', { method: 'POST', body: form });
            if (!res.ok) return textoWebSpeech;

            const data = await res.json();
            const textoWhisper = data.texto || '';

            // Si Whisper retornó algo sustancialmente diferente, usar Whisper
            if (textoWhisper && textoWhisper.length > 3) {
                // Actualizar transcripción visual si difiere
                const el = document.getElementById('voz-transcripcion');
                if (el && textoWhisper !== textoWebSpeech) {
                    el.textContent = `"${textoWhisper}"`;
                }
                return textoWhisper;
            }
        } catch {
            // Whisper falló → fallback a Web Speech
        }

        return textoWebSpeech;
    }
}

// Instancia global
window.voz = new VozManager();
