"""
vigilante.py — alerta.pe  ·  Watchdog EXTERNO del worker (como el de Facturalo)
═══════════════════════════════════════════════════════════════════════
Proceso SEPARADO del worker. Lee el latido (`worker_estado.ultimo_ciclo_ok_at`)
y, si el worker no completa un ciclo hace más de UMBRAL horas, AVISA por
Telegram y por correo. Es externo A PROPÓSITO: un worker vivo-pero-congelado no
puede auto-avisar (incidente 27/10: proceso "running" en Railway pero sin
completar ciclos 3 semanas, sin que nadie se enterara).

Ejecución:
  - Cron (recomendado): `python vigilante.py`  → un chequeo y sale. Programar
    cada ~15-30 min (Railway Cron, o cron externo / uptime).
  - Loop:  `python vigilante.py --loop`  → chequea cada VIGILANTE_INTERVALO_SEG.

ENV:
  VIGILANTE_UMBRAL_HORAS   (default 2)   — sin ciclo completo => alerta
  VIGILANTE_COOLDOWN_HORAS (default 6)   — no repetir alerta antes de esto
  VIGILANTE_INTERVALO_SEG  (default 900) — solo en --loop
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   — alerta Telegram (si faltan, se omite)
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASS, ALERTA_EMAIL_TO, ALERTA_EMAIL_FROM
                                          — alerta correo (si faltan, se omite)
"""
from __future__ import annotations

import asyncio
import os
import smtplib
import sys
from datetime import timedelta
from email.message import EmailMessage

from db import get_session, engine
from models import WorkerEstado, ahora_lima

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from seguridad_logs import enmascarar as _mask
except Exception:
    def _mask(x):
        return str(x) if x is not None else ""

UMBRAL_HORAS = float(os.getenv("VIGILANTE_UMBRAL_HORAS", "2"))
COOLDOWN_HORAS = float(os.getenv("VIGILANTE_COOLDOWN_HORAS", "6"))
INTERVALO_SEG = int(os.getenv("VIGILANTE_INTERVALO_SEG", "900"))


def log(msg: str, nivel: str = "INFO") -> None:
    print(f"[{ahora_lima().strftime('%d/%m/%Y %H:%M:%S')}] [{nivel}] {_mask(msg)}",
          flush=True)


def _enviar_telegram(texto: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        log("Telegram no configurado (TELEGRAM_BOT_TOKEN/CHAT_ID) — omito.", "WARN")
        return False
    try:
        import httpx
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": texto,
                             "disable_web_page_preview": True},
                       timeout=20)
        ok = r.status_code == 200
        log(f"Telegram enviado: {ok} (HTTP {r.status_code})",
            "OK" if ok else "ERROR")
        return ok
    except Exception as e:
        log(f"Telegram falló: {e}", "ERROR")
        return False


def _enviar_correo(asunto: str, cuerpo: str) -> bool:
    host = os.getenv("SMTP_HOST", "")
    to = os.getenv("ALERTA_EMAIL_TO", "")
    if not host or not to:
        log("Correo no configurado (SMTP_HOST/ALERTA_EMAIL_TO) — omito.", "WARN")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = os.getenv("ALERTA_EMAIL_FROM") or os.getenv("SMTP_USER", to)
        msg["To"] = to
        msg.set_content(cuerpo)
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=25) as s:
            s.starttls()
            user, pw = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", "")
            if user:
                s.login(user, pw)
            s.send_message(msg)
        log("Correo enviado.", "OK")
        return True
    except Exception as e:
        log(f"Correo falló: {e}", "ERROR")
        return False


async def _chequear_una_vez() -> bool:
    """Devuelve True si está SANO, False si alertó (o intentó) por caído."""
    async with get_session() as session:
        est = await session.get(WorkerEstado, "singleton")
        ahora = ahora_lima()
        ultimo = est.ultimo_ciclo_ok_at if est else None
        if ultimo is None:
            edad_h = None
        else:
            edad_h = (ahora - ultimo).total_seconds() / 3600.0

        sano = (edad_h is not None and edad_h <= UMBRAL_HORAS)
        if sano:
            log(f"OK · último ciclo completo hace {edad_h:.1f}h "
                f"(umbral {UMBRAL_HORAS}h).")
            return True

        # Caído: respetar cooldown para no spamear.
        ult_alerta = est.ultima_alerta_caido_at if est else None
        if ult_alerta and (ahora - ult_alerta) < timedelta(hours=COOLDOWN_HORAS):
            log(f"CAÍDO pero en cooldown (última alerta hace "
                f"{(ahora - ult_alerta).total_seconds()/3600:.1f}h) — no repito.", "WARN")
            return False

        detalle = ("nunca completó un ciclo" if edad_h is None
                   else f"último ciclo completo hace {edad_h:.1f}h")
        texto = (f"🚨 alerta.pe · WORKER DETENIDO\n"
                 f"El worker de scraping no completa un ciclo ({detalle}; "
                 f"umbral {UMBRAL_HORAS}h).\n"
                 f"Revisa el servicio alertape-worker en Railway "
                 f"(¿running? ¿último deploy? ¿traceback de arranque?). "
                 f"Hora Lima: {ahora.strftime('%d/%m/%Y %H:%M')}.")
        log("WORKER DETENIDO — enviando alertas.", "ERROR")
        _enviar_telegram(texto)
        _enviar_correo("🚨 alerta.pe — worker detenido", texto)
        if est is not None:
            est.ultima_alerta_caido_at = ahora
            await session.commit()
        return False


async def _main():
    loop = "--loop" in sys.argv
    if not loop:
        await _chequear_una_vez()
        await engine.dispose()
        return
    log(f"Vigilante en loop cada {INTERVALO_SEG}s (umbral {UMBRAL_HORAS}h).")
    while True:
        try:
            await _chequear_una_vez()
        except Exception as e:
            log(f"Error en el chequeo (sigo): {e}", "ERROR")
        await asyncio.sleep(INTERVALO_SEG)


if __name__ == "__main__":
    asyncio.run(_main())
