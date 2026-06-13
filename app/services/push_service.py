"""Servicio de envío de Web Push Notifications.

Usa pywebpush + VAPID keys. Envío sincrónico dentro de ejecutor async.
"""
import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ClienteRuc, PushSuscripcion

logger = logging.getLogger(__name__)


async def enviar_push(
    db: AsyncSession,
    usuario_id: int,
    titulo: str,
    cuerpo: str,
    url: str = "/dashboard",
    icono: str = "",
) -> dict[str, int]:
    """Envía push a TODAS las suscripciones activas del usuario.

    Returns:
        {"enviadas": N, "fallidas": N, "desactivadas": N}
    """
    if not settings.vapid_private_key:
        logger.warning("VAPID_PRIVATE_KEY no configurada, push desactivado")
        return {"enviadas": 0, "fallidas": 0, "desactivadas": 0}

    result = await db.execute(
        select(PushSuscripcion)
        .where(PushSuscripcion.usuario_id == usuario_id)
        .where(PushSuscripcion.activa == True)
    )
    suscripciones = list(result.scalars().all())

    payload = json.dumps({
        "titulo": titulo,
        "cuerpo": cuerpo,
        "url": url,
        "icono": icono,
    })

    enviadas = 0
    fallidas = 0
    desactivadas = 0

    for sus in suscripciones:
        subscription_info = {
            "endpoint": sus.endpoint,
            "keys": {"p256dh": sus.p256dh, "auth": sus.auth},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
            )
            enviadas += 1
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None) if response else None
            body = ""
            try:
                body = response.text if response is not None else ""
            except Exception:
                body = ""
            # 404/410 = suscripción expirada; 400 = suele ser VAPID/applicationServerKey
            # desincronizada (la suscripción quedó atada a una clave pública vieja).
            # En todos esos casos la suscripción es inservible: la desactivamos para
            # que el navegador cree una nueva al re-suscribirse.
            if status in (400, 404, 410):
                sus.activa = False
                desactivadas += 1
                logger.warning(
                    f"WebPush {status}: suscripción {sus.id} desactivada. Body: {body[:300]}"
                )
            else:
                fallidas += 1
                logger.warning(f"WebPush error (status={status}): {exc}. Body: {body[:300]}")
        except Exception as exc:
            fallidas += 1
            logger.exception(f"Error inesperado enviando push: {exc}")

    await db.commit()
    return {"enviadas": enviadas, "fallidas": fallidas, "desactivadas": desactivadas}


async def notificar_mensajes_nuevos(
    db: AsyncSession,
    usuario_id: int,
    cliente: ClienteRuc,
    cantidad: int,
) -> None:
    """Envía push cuando el polling detecta mensajes nuevos."""
    nombre = cliente.nombre_referencia or cliente.razon_social[:40]
    titulo = f"🔔 Alerta SUNAT — {nombre}"
    cuerpo = (
        f"{cantidad} mensaje nuevo en el buzón"
        if cantidad == 1
        else f"{cantidad} mensajes nuevos en el buzón"
    )
    await enviar_push(
        db=db,
        usuario_id=usuario_id,
        titulo=titulo,
        cuerpo=cuerpo,
        url=f"/clientes/{cliente.id}",
    )
