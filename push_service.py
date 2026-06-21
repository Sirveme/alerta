"""
push_service.py — alerta.pe   (C:\\alertape\\push_service.py)
═══════════════════════════════════════════════════════════════════════
Envío de Web Push (pywebpush + VAPID). Vive en la RAÍZ (junto al motor) para
que el WORKER lo importe sin acoplar webapp/. NO usa Playwright: solo
pywebpush + BD, así que es seguro importarlo desde cualquier proceso.

Flujo (zAlerta-07 C):
  worker, tras ingestar y detectar nuevas para un contribuyente →
    notificar_nuevas(session, contrib, n_nuevas)
      → push al/los usuario(s) del ESTUDIO que lo vigila
      → push al usuario EMPRESARIO dueño (cuenta_empresario_id), si tiene.

Claves VAPID por env (ya en Railway): VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY,
VAPID_CLAIM_EMAIL. Un fallo de suscripción NUNCA rompe el ciclo del worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from sqlalchemy import select

from models import PushSuscripcion, Usuario, Contribuyente

try:  # cargar .env en local; en Railway las env ya están en el entorno
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("alertape.push")


def _vapid_private_key() -> str:
    return os.getenv("VAPID_PRIVATE_KEY", "")


def _vapid_claim_email() -> str:
    return os.getenv("VAPID_CLAIM_EMAIL", "info@perusistemas.pro")


def _enviar_webpush_sync(sub: PushSuscripcion, payload: str) -> int | None:
    """Envía un push (bloqueante). Devuelve un status_code de error
    (400/404/410 = suscripción muerta) o None si se envió OK / error transitorio.
    Lanza solo para errores realmente inesperados (los captura el caller)."""
    from pywebpush import webpush, WebPushException  # import perezoso
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=payload,
            vapid_private_key=_vapid_private_key(),
            vapid_claims={"sub": f"mailto:{_vapid_claim_email()}"},
        )
        return None
    except WebPushException as exc:
        resp = getattr(exc, "response", None)
        return getattr(resp, "status_code", None) if resp is not None else None


async def _enviar_a_usuario(session, usuario_id, payload: str) -> int:
    """Envía a TODAS las suscripciones activas del usuario. Devuelve cuántas
    salieron OK. Desactiva las suscripciones muertas (400/404/410)."""
    subs = list(await session.scalars(
        select(PushSuscripcion).where(
            PushSuscripcion.usuario_id == usuario_id,
            PushSuscripcion.activa.is_(True))))
    enviadas = 0
    for sub in subs:
        try:
            status = await asyncio.to_thread(_enviar_webpush_sync, sub, payload)
        except Exception as e:
            # Un fallo de una suscripción no rompe el resto ni el ciclo.
            logger.warning("push: error enviando a sub %s: %s", sub.id, e)
            continue
        if status in (400, 404, 410):
            sub.activa = False   # suscripción expirada/invalida → desactivar
            logger.info("push: suscripción %s desactivada (status %s)", sub.id, status)
        else:
            enviadas += 1
    return enviadas


async def _usuarios_objetivo(session, contrib: Contribuyente) -> list:
    """IDs de usuarios a notificar: del estudio que vigila + empresario dueño."""
    ids: list = []
    # Usuarios del ESTUDIO que vigila el contribuyente.
    ids += list(await session.scalars(
        select(Usuario.id).where(
            Usuario.estudio_id == contrib.estudio_id,
            Usuario.activo.is_(True))))
    # Usuario(s) de la cuenta EMPRESARIO dueña del RUC (si existe).
    if contrib.cuenta_empresario_id:
        ids += list(await session.scalars(
            select(Usuario.id).where(
                Usuario.estudio_id == contrib.cuenta_empresario_id,
                Usuario.activo.is_(True))))
    return ids


async def notificar_nuevas(session, contrib: Contribuyente, n_nuevas: int) -> dict:
    """Notifica por push que hay N notificaciones nuevas de SUNAT para un RUC.

    Seguro ante fallos: cualquier error se loguea y se sigue (no propaga).
    Devuelve {"usuarios": X, "enviadas": Y}.
    """
    if n_nuevas <= 0:
        return {"usuarios": 0, "enviadas": 0}
    if not _vapid_private_key():
        logger.warning("push: VAPID_PRIVATE_KEY no configurada; push desactivado.")
        return {"usuarios": 0, "enviadas": 0}

    plural = "" if n_nuevas == 1 else "s"
    payload = json.dumps({
        # Identidad alerta.pe (NO SUNAT). Cuerpo breve.
        "title": "Novedades en tu Buzón SUNAT",
        "body": f"Tienes {n_nuevas} aviso{plural} nuevo{plural}. Toca para ver.",
        "url": "/resumen",   # ENTRAR → tabla resumen (offline tras 1ª entrada)
        # El service worker añade las acciones GRACIAS / ENTRAR si el navegador
        # las soporta (zAlerta-12 P1.b).
        "acciones": True,
    })

    enviadas = 0
    usuarios = await _usuarios_objetivo(session, contrib)
    for uid in usuarios:
        try:
            enviadas += await _enviar_a_usuario(session, uid, payload)
        except Exception as e:
            logger.warning("push: error con usuario %s: %s", uid, e)
    try:
        await session.commit()   # persistir desactivaciones de suscripciones
    except Exception as e:
        logger.warning("push: no se pudo commitear cambios de suscripción: %s", e)
    return {"usuarios": len(usuarios), "enviadas": enviadas}
