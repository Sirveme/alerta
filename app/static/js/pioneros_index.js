/* ══════════════════════════════════════════════════════════════
   alerta.pe — Pioneros JS
══════════════════════════════════════════════════════════════ */

/* ── BRANDS ────────────────────────────────────────────────── */
const BRANDS = {
  alertape: {
    name: 'alerta.pe',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#0c1428"/>
      <rect x=".5" y=".5" width="39" height="39" rx="9.5" fill="none" stroke="rgba(212,160,18,.6)" stroke-width="1"/>
      <text x="20" y="27" text-anchor="middle" font-size="20">🔔</text>
    </svg>`
  },
  yape: {
    name: 'Yape',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#7B3FF2"/>
      <text x="20" y="26" text-anchor="middle" fill="white" font-size="12" font-weight="bold" font-family="Arial,sans-serif">yape</text>
    </svg>`
  },
  plin: {
    name: 'Plin',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="pg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#00c6fb"/>
        <stop offset="100%" stop-color="#0072ff"/>
      </linearGradient></defs>
      <rect width="40" height="40" rx="10" fill="url(#pg)"/>
      <text x="20" y="27" text-anchor="middle" fill="white" font-size="13" font-weight="bold" font-family="Arial,sans-serif">Plin</text>
    </svg>`
  },
  bcp: {
    name: 'BCP',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#E30613"/>
      <text x="20" y="27" text-anchor="middle" fill="white" font-size="13" font-weight="bold" font-family="Arial,sans-serif">BCP</text>
    </svg>`
  },
  bbva: {
    name: 'BBVA',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#004481"/>
      <text x="20" y="27" text-anchor="middle" fill="white" font-size="11" font-weight="bold" font-family="Arial,sans-serif">BBVA</text>
    </svg>`
  },
  interbank: {
    name: 'IBK',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#00963f"/>
      <text x="20" y="27" text-anchor="middle" fill="white" font-size="13" font-weight="bold" font-family="Arial,sans-serif">IBK</text>
    </svg>`
  },
  bnacion: {
    name: 'B.Nación',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#003DA5"/>
      <text x="20" y="22" text-anchor="middle" fill="white" font-size="10" font-weight="bold" font-family="Arial,sans-serif">B.</text>
      <text x="20" y="33" text-anchor="middle" fill="white" font-size="10" font-weight="bold" font-family="Arial,sans-serif">NAC.</text>
    </svg>`
  },
  scotiabank: {
    name: 'Scotia',
    svg: `<svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="10" fill="#EC111A"/>
      <text x="20" y="27" text-anchor="middle" fill="white" font-size="10.5" font-weight="bold" font-family="Arial,sans-serif">Scotia</text>
    </svg>`
  },
};

/* ── NOTIFICACIONES PARA EL TELÉFONO ───────────────────────── */
const NOTIFS = [
  {
    brand: 'yape',
    tipo: 'important',
    app: 'alerta.pe · Yape',
    title: '✓ Pago confirmado — S/3,200',
    msg: 'Minimarket Flores · cruzado con F001-1847',
    ts: 'ahora',
    sound: 'ding-dong',
  },
  {
    brand: 'alertape',
    tipo: 'urgent',
    app: 'alerta.pe · SUNAT',
    title: '🔴 Resolución de Cobranza Coactiva',
    msg: 'Comercializadora Lima SAC · Renta 3a Jul-2025',
    ts: '1 min',
    sound: 'alert',
  },
  {
    brand: 'bcp',
    tipo: 'important',
    app: 'alerta.pe · BCP',
    title: 'Transferencia recibida S/8,500',
    msg: 'Empresa Ríos e Hijos → Cta. 1234-5678',
    ts: '4 min',
    sound: 'new-notification-sound',
  },
  {
    brand: 'plin',
    tipo: 'info',
    app: 'alerta.pe · Plin',
    title: '🔵 Pago recibido S/1,150',
    msg: 'Bodega El Carmen · ciclo completo',
    ts: '12 min',
    sound: 'pop-sound',
  },
  {
    brand: 'bbva',
    tipo: 'important',
    app: 'alerta.pe · Análisis',
    title: '🟡 Anomalía detectada',
    msg: 'Cliente XYZ vendió 40% más, 20% menos facturas',
    ts: 'hoy',
    sound: 'confirm',
  },
  {
    brand: 'interbank',
    tipo: 'info',
    app: 'alerta.pe · IBK',
    title: 'Transferencia S/5,200',
    msg: 'Importaciones Norte SAC · ref. 00234',
    ts: 'hoy',
    sound: 'swoosh-sound',
  },
];

/* ── SONIDOS ────────────────────────────────────────────────── */
const SND = {};
const SND_FILES = {
  'alert':                  'alert.mp3',
  'cancel':                 'cancel.mp3',
  'confirm':                'confirm.mp3',
  'ding-dong':              'ding-dong.mp3',
  'error':                  'error.mp3',
  'new-notification-sound': 'new-notification-sound.mp3',
  'pop-sound':              'pop-sound.mp3',
  'swoosh-sound':           'swoosh-sound.mp3',
  'success':                'successfinish-ui-sound.mp3',
  'click':                  'mouse-click-sound.mp3',
};
Object.entries(SND_FILES).forEach(([k, f]) => {
  try { SND[k] = new Audio(`/static/sounds/${f}`); SND[k].volume = 0.4; } catch(e) {}
});

function play(key) {
  try {
    const s = SND[key];
    if (s) { s.currentTime = 0; s.play().catch(() => {}); }
  } catch(e) {}
}

/* ── TOAST ─────────────────────────────────────────────────── */
function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = 'toast'; }, 3500);
}

/* ── RELOJ DEL TELÉFONO ─────────────────────────────────────── */
function updatePhoneClock() {
  const el = document.getElementById('phone-time');
  if (!el) return;
  const now = new Date();
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  el.textContent = `${h}:${m}`;

  const lock = document.getElementById('phone-lock-clock');
  if (lock) lock.textContent = `${h}:${m}`;

  const days = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
  const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
  const date = document.getElementById('phone-lock-date');
  if (date) date.textContent = `${days[now.getDay()]}, ${now.getDate()} ${months[now.getMonth()]}`;
}

/* ── ESTRELLAS DEL TELÉFONO ─────────────────────────────────── */
function addStars() {
  const container = document.querySelector('.phone-stars');
  if (!container) return;
  for (let i = 0; i < 30; i++) {
    const star = document.createElement('div');
    const size = Math.random() * 1.5 + .5;
    star.className = 'star';
    star.style.cssText = `
      width: ${size}px; height: ${size}px;
      left: ${Math.random() * 100}%; top: ${Math.random() * 100}%;
      --d: ${(Math.random() * 3 + 2).toFixed(1)}s;
      --delay: ${(Math.random() * 4).toFixed(1)}s;
      opacity: ${(Math.random() * .6 + .2).toFixed(2)};
    `;
    container.appendChild(star);
  }
}

/* ── ANIMACIÓN DE NOTIFICACIONES ────────────────────────────── */
let notifIndex = 0;
let soundsPlayed = new Set();
let notifRunning = false;

function buildNotifEl(notif) {
  const brand = BRANDS[notif.brand] || BRANDS.alertape;
  const el = document.createElement('div');
  el.className = `pn ${notif.tipo}`;
  el.innerHTML = `
    <div class="pn-icon">${brand.svg}</div>
    <div class="pn-body">
      <div class="pn-top">
        <span class="pn-app">${notif.app}</span>
        <span class="pn-ts">${notif.ts}</span>
      </div>
      <div class="pn-title">${notif.title}</div>
      <div class="pn-msg">${notif.msg}</div>
    </div>`;
  return el;
}

async function runNotifCycle() {
  if (notifRunning) return;
  notifRunning = true;

  const container = document.querySelector('.phone-notifs');
  if (!container) return;

  while (true) {
    const notif = NOTIFS[notifIndex % NOTIFS.length];
    notifIndex++;

    const el = buildNotifEl(notif);
    container.prepend(el);

    // Limitar a 3 notificaciones visibles
    const all = container.querySelectorAll('.pn');
    if (all.length > 3) {
      const last = all[all.length - 1];
      last.classList.add('out');
      setTimeout(() => last.remove(), 500);
    }

    // Animar entrada
    await sleep(30);
    el.classList.add('in');

    // Sonido: solo la primera vez
    if (!soundsPlayed.has(notifIndex - 1)) {
      soundsPlayed.add(notifIndex - 1);
      play(notif.sound);
    }

    await sleep(5500);

    // Al llegar a la última vuelta completa, seguir silenciosamente (sin sonidos)
    if (notifIndex >= NOTIFS.length) {
      soundsPlayed = new Set(Array.from({ length: NOTIFS.length * 999 }, (_, i) => i));
    }

    await sleep(600);
  }
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/* ── AUDIO PLAYER ───────────────────────────────────────────── */
let audioEl = null;
let isPlaying = false;

function initAudio() {
  audioEl = new Audio('/static/audio/intro.mp3');
  audioEl.addEventListener('ended', () => stopAudio());
  audioEl.addEventListener('error', () => {
    toast('Audio no disponible aún — próximamente', 'inf');
    stopAudio();
  });

  const btn = document.getElementById('btn-play');
  const eq  = document.querySelector('.eq');
  if (!btn) return;

  btn.addEventListener('click', () => {
    if (!isPlaying) {
      audioEl.play().then(() => {
        isPlaying = true;
        btn.innerHTML = `<span class="eq playing"><span></span><span></span><span></span><span></span></span> PAUSAR`;
        play('click');
      }).catch(() => {
        toast('Graba el audio intro y súbelo a static/audio/intro.mp3', 'inf');
      });
    } else {
      stopAudio();
    }
  });
}

function stopAudio() {
  isPlaying = false;
  if (audioEl) audioEl.pause();
  const btn = document.getElementById('btn-play');
  if (btn) btn.innerHTML = `▶ REPRODUCIR`;
}

/* ── TEMA Y FUENTE ──────────────────────────────────────────── */
function initControls() {
  const root = document.documentElement;

  // Recuperar preferencias guardadas
  const savedTheme = localStorage.getItem('alerta-theme') || 'semi';
  const savedSize  = localStorage.getItem('alerta-size')  || 'md';
  root.setAttribute('data-theme', savedTheme === 'dark' ? 'dark' : '');
  root.setAttribute('data-size', savedSize);
  updateCtrlBtns();

  document.getElementById('ctrl-dark')?.addEventListener('click', () => {
    const now = root.getAttribute('data-theme');
    const next = now === 'dark' ? '' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('alerta-theme', next === 'dark' ? 'dark' : 'semi');
    updateCtrlBtns();
    play('click');
  });

  document.getElementById('ctrl-sm')?.addEventListener('click', () => {
    root.setAttribute('data-size', 'sm');
    localStorage.setItem('alerta-size', 'sm');
    updateCtrlBtns();
    play('click');
  });
  document.getElementById('ctrl-lg')?.addEventListener('click', () => {
    root.setAttribute('data-size', 'lg');
    localStorage.setItem('alerta-size', 'lg');
    updateCtrlBtns();
    play('click');
  });
}

function updateCtrlBtns() {
  const root   = document.documentElement;
  const theme  = root.getAttribute('data-theme');
  const size   = root.getAttribute('data-size') || 'md';
  document.getElementById('ctrl-dark')?.classList.toggle('active', theme === 'dark');
  document.getElementById('ctrl-sm')?.classList.toggle('active', size === 'sm');
  document.getElementById('ctrl-lg')?.classList.toggle('active', size === 'lg');
}

/* ── DESCUENTO POR MES ─────────────────────────────────────── */
function initDiscount() {
  const mes = new Date().getMonth() + 1;
  const map = {
    4: { pct: '75%', desc: 'Regístrate en abril — el mayor descuento.' },
    5: { pct: '50%', desc: 'Regístrate en mayo.' },
    6: { pct: '25%', desc: 'Últimos Pioneros con descuento.' },
  };
  const info = map[mes] || { pct: '—', desc: 'Período de Pioneros cerrado.' };
  const pctEl = document.getElementById('disc-pct');
  const descEl = document.getElementById('disc-desc');
  if (pctEl) pctEl.textContent = info.pct;
  if (descEl) descEl.textContent = info.desc;
  ['abr','may','jun'].forEach((m, i) => {
    const el = document.getElementById(`dm-${m}`);
    if (el && mes === i + 4) el.classList.add('active');
  });
}

/* ── FOTO UPLOAD ────────────────────────────────────────────── */
let fotoFile = null;

window.handlePhoto = function(input) {
  const file = input.files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    toast('La foto no debe superar 5 MB', 'err');
    play('error');
    return;
  }
  fotoFile = file;
  const reader = new FileReader();
  reader.onload = e => {
    const prev = document.getElementById('photo-preview');
    const wrap = document.getElementById('photo-preview-wrap');
    const autWrap = document.getElementById('wrap-autoriza-foto');
    if (prev) prev.src = e.target.result;
    if (wrap) wrap.style.display = 'block';
    if (autWrap) autWrap.style.display = 'flex';
    play('click');
  };
  reader.readAsDataURL(file);
};

window.removePhoto = function() {
  fotoFile = null;
  const input = document.getElementById('foto-input');
  if (input) input.value = '';
  const wrap = document.getElementById('photo-preview-wrap');
  const autWrap = document.getElementById('wrap-autoriza-foto');
  if (wrap) wrap.style.display = 'none';
  if (autWrap) autWrap.style.display = 'none';
  play('click');
};

/* ── MICRÓFONO (Web Speech API) ─────────────────────────────── */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null, activeCampo = null, grabando = false;

if (SR) {
  recog = new SR();
  recog.lang = 'es-PE';
  recog.continuous = true;
  recog.interimResults = true;

  recog.onresult = e => {
    if (!activeCampo) return;
    const ta = document.getElementById(activeCampo);
    if (!ta) return;
    let final = ta._base || '';
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      e.results[i].isFinal
        ? (final += e.results[i][0].transcript + ' ')
        : (interim += e.results[i][0].transcript);
    }
    ta.value = final + interim;
  };

  recog.onend = () => {
    if (activeCampo) {
      const ta = document.getElementById(activeCampo);
      if (ta) ta._base = ta.value;
      if (activeCampo === 'dolor' && ta && ta.value.trim().length > 20) debounceIA();
    }
    detenerMic();
  };
  recog.onerror = () => detenerMic();
}

window.toggleMic = function(campo) {
  if (grabando && activeCampo === campo) detenerMic();
  else iniciarMic(campo);
};

function iniciarMic(campo) {
  if (!recog) { toast('Tu navegador no soporta entrada por voz — escribe directamente', 'inf'); return; }
  if (grabando) detenerMic();
  activeCampo = campo;
  const ta = document.getElementById(campo);
  if (ta) ta._base = ta.value;
  grabando = true;
  document.getElementById(`mic-${campo}`)?.classList.add('recording');
  recog.start();
  play('click');
}

function detenerMic() {
  grabando = false;
  if (recog) try { recog.stop(); } catch(e) {}
  document.querySelectorAll('.mic-btn').forEach(b => b.classList.remove('recording'));
  activeCampo = null;
}

/* ── ANÁLISIS IA ─────────────────────────────────────────────── */
let timerIA = null;

document.addEventListener('DOMContentLoaded', () => {
  const dolorEl = document.getElementById('dolor');
  if (dolorEl) {
    dolorEl.addEventListener('input', () => {
      if (dolorEl.value.trim().length > 25) debounceIA();
    });
  }
});

function debounceIA() {
  clearTimeout(timerIA);
  timerIA = setTimeout(analizarDolor, 2000);
}

async function analizarDolor() {
  const texto = document.getElementById('dolor')?.value.trim();
  if (!texto || texto.length < 20) return;

  const box = document.getElementById('ai-box-dolor');
  if (!box) return;
  box.style.display = 'block';
  box.innerHTML = `<div class="ai-loading"><span class="spinner"></span> Analizando tu respuesta...</div>`;
  play('new-notification-sound');

  try {
    const res = await fetch('/api/contadores/analizar-dolor', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto }),
    });
    if (!res.ok) throw new Error();
    const data = await res.json();
    play('ding-dong');

    box.innerHTML = `
      <div class="ai-result">
        <div style="font-size:1.3rem;flex-shrink:0;">🤖</div>
        <div style="flex:1;">
          <div class="ai-label">Lo que entendemos</div>
          <div class="ai-sum">${data.resumen}</div>
          <div class="ai-q">¿Lo describimos bien?</div>
          <div class="ai-btns">
            <button class="ai-yes" onclick="confirmarIA(true)">✓ Sí, exacto</button>
            <button class="ai-no"  onclick="confirmarIA(false)">✗ No del todo</button>
          </div>
        </div>
      </div>`;
  } catch(e) {
    box.style.display = 'none';
  }
}

window.confirmarIA = function(ok) {
  const box = document.getElementById('ai-box-dolor');
  play('click');
  if (ok) {
    box.innerHTML = `<div class="ai-confirmed">✓ Perfecto, anotado. ¡Gracias!</div>`;
    setTimeout(() => { box.style.display = 'none'; }, 2000);
  } else {
    box.innerHTML = `<div class="ai-adjust">Ajusta tu respuesta arriba — queremos escucharte bien.</div>`;
    document.getElementById('dolor')?.focus();
    setTimeout(() => { box.style.display = 'none'; }, 2500);
  }
};

/* ── GALERÍA ─────────────────────────────────────────────────── */
async function cargarGaleria() {
  try {
    const res = await fetch('/api/contadores/respuestas');
    if (!res.ok) return;
    const data = await res.json();
    const lista = data.respuestas || [];
    const total = data.total || lista.length;

    const badge = document.getElementById('gallery-badge');
    if (badge) badge.textContent = `${total} Pionero${total !== 1 ? 's' : ''}`;

    const cont = document.getElementById('gallery-list');
    if (!cont || lista.length === 0) return;
    cont.innerHTML = '';

    lista.forEach(r => {
      const iniciales = (r.nombre || 'NN')
        .split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
      const nombrePub = r.anonimo
        ? (() => { const p = (r.nombre || '').split(' '); return p.length >= 2 ? `${p[0]} ${p[1][0]}.` : p[0] || 'Anónimo'; })()
        : (r.nombre || 'Anónimo');

      const avatarHtml = r.foto_url
        ? `<div class="pioneer-av"><img src="${r.foto_url}" alt="${nombrePub}" onerror="this.parentElement.textContent='${iniciales}'"></div>`
        : `<div class="pioneer-av">${iniciales}</div>`;

      const card = document.createElement('div');
      card.className = 'pioneer-card';
      card.innerHTML = `
        <div class="pioneer-head">
          ${avatarHtml}
          <div>
            <div class="pioneer-name">CPC ${nombrePub}</div>
            <div class="pioneer-region">📍 ${r.region || ''}</div>
          </div>
          <span class="pioneer-badge">Pionero</span>
        </div>
        <div class="pioneer-dlabel">Mayor dolor</div>
        <div class="pioneer-dolor">"${r.dolor || ''}"</div>
        ${r.sugerencia ? `<div class="pioneer-dlabel" style="margin-top:.7rem;color:var(--blue);">Su sugerencia</div><div class="pioneer-dolor">${r.sugerencia}</div>` : ''}
      `;
      cont.appendChild(card);
    });
  } catch(e) { /* offline */ }
}

/* ── FORMULARIO ─────────────────────────────────────────────── */
function initForm() {
  const form = document.getElementById('form-pionero');
  if (!form) return;

  form.addEventListener('submit', async e => {
    e.preventDefault();

    const nombre   = document.getElementById('nombre')?.value.trim();
    const whatsapp = document.getElementById('whatsapp')?.value.trim();
    const region   = document.getElementById('region')?.value;
    const dolor    = document.getElementById('dolor')?.value.trim();
    const proceso  = document.getElementById('proceso')?.value.trim();
    const sugere   = document.getElementById('sugerencia')?.value.trim();
    const anonimo  = document.getElementById('cb-anonimo')?.checked;
    const autFoto  = document.getElementById('cb-autoriza-foto')?.checked;

    if (!nombre || !whatsapp || !region || !dolor) {
      toast('Completa los campos obligatorios (*)', 'err');
      play('error');
      return;
    }

    const btn = document.getElementById('btn-enviar');
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Enviando...`;

    try {
      const fd = new FormData();
      fd.append('nombre',        nombre);
      fd.append('whatsapp',      whatsapp);
      fd.append('region',        region);
      fd.append('dolor',         dolor);
      fd.append('proceso_actual', proceso || '');
      fd.append('sugerencia',    sugere || '');
      fd.append('anonimo',       anonimo ? '1' : '0');
      fd.append('autoriza_foto', autFoto ? '1' : '0');
      if (fotoFile && autFoto) fd.append('foto', fotoFile);

      const res  = await fetch('/api/contadores/registro', { method: 'POST', body: fd });
      const data = await res.json();

      if (res.ok && data.exito) {
        play('success');
        btn.innerHTML = '✓ ¡Registrado como Pionero!';
        btn.classList.add('success');
        toast('¡Gracias! Te contactaremos pronto por WhatsApp.', 'ok');
        setTimeout(cargarGaleria, 1200);
      } else {
        throw new Error(data.mensaje || 'Error al enviar');
      }
    } catch(err) {
      play('error');
      toast(err.message || 'Error. Intenta de nuevo.', 'err');
      btn.disabled = false;
      btn.innerHTML = '🚀 Quiero ser Pionero — Enviar mi opinión';
    }
  });
}

/* ── PWA INSTALL ─────────────────────────────────────────────── */
let deferredPrompt = null;

function initPWA() {
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    deferredPrompt = e;
  });

  // Botón sticky "TOCA AQUÍ"
  document.getElementById('sticky-btn')?.addEventListener('click', () => {
    play('click');
    document.getElementById('modal-install').classList.add('open');
  });

  // Cerrar modal
  document.getElementById('modal-close')?.addEventListener('click', () => {
    document.getElementById('modal-install').classList.remove('open');
  });
  document.getElementById('modal-install')?.addEventListener('click', e => {
    if (e.target === e.currentTarget) e.currentTarget.classList.remove('open');
  });

  // Botón instalar dentro del modal
  document.getElementById('btn-install-now')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-install-now');
    if (!deferredPrompt) {
      toast('Instala desde el menú de tu navegador: "Agregar a pantalla de inicio"', 'inf');
      return;
    }
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      play('success');
      toast('¡alerta.pe instalada! Búscala en tu pantalla de inicio.', 'ok');
      document.getElementById('modal-install').classList.remove('open');
    }
    deferredPrompt = null;
  });
}

/* ── SERVICE WORKER ─────────────────────────────────────────── */
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

/* ── INIT ────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initControls();
  initDiscount();
  initAudio();
  initForm();
  initPWA();
  addStars();
  updatePhoneClock();
  setInterval(updatePhoneClock, 30000);

  // Lanzar ciclo de notificaciones (con pequeño delay para que el usuario vea el teléfono)
  setTimeout(runNotifCycle, 1200);

  // Cargar galería
  cargarGaleria();
});