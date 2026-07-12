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
from datetime import timedelta

from sqlalchemy import select, or_

from models import (
    PushSuscripcion, Usuario, Contribuyente, Notificacion, ahora_lima, TZ_LIMA,
    Acceso,
)

# Imagen FIJA del push expandido = leyenda del semáforo de colores (zAlerta-17).
# La provee el fundador; si no existe, el navegador la ignora (degrada bien).
LEYENDA_IMG = os.getenv("PUSH_LEYENDA_IMG", "/static/img/leyenda-colores.png")

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
            ttl=86400,   # 1 día: si el dispositivo está offline, se entrega al reconectar.
            # Alta prioridad (zAlerta-64): pide al proveedor/SO no agrupar ni
            # diferir el aviso. El control TOTAL de sonido/canal es solo nativo.
            headers={"Urgency": "high"},
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


async def notificar_usuario(session, usuario_id, title: str, body: str,
                            url: str = "/resumen", acciones: bool = False,
                            tag: str | None = None, requiere: bool = False) -> int:
    """Envía un push genérico a TODAS las suscripciones de un usuario (zAlerta-13:
    recordatorios "Recuérdame esto" y avisos de credencial caída). Seguro ante
    fallos. Devuelve cuántas salieron OK.

    tag/requiere (zAlerta-64): el sw.js usa `tag` para re-avisar sin apilar y
    `requiere` (requireInteraction) para que la deuda/urgente no se descarte sola."""
    if not _vapid_private_key():
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url,
                          "acciones": acciones,
                          "tag": tag or "alertape-buzon", "requiere": requiere})
    try:
        n = await _enviar_a_usuario(session, usuario_id, payload)
        await session.commit()
        return n
    except Exception as e:
        logger.warning("push: notificar_usuario %s falló: %s", usuario_id, e)
        return 0


async def personas_del_buzon(session, contrib: Contribuyente) -> list:
    """persona_ids con acceso NOMINAL vigente al buzón (zAlerta-67).

    Nominal = tiene una fila en `accesos` a la organización (estudio o cuenta
    empresario) o al propio contribuyente, con vigencia vigente. Esto EXCLUYE al
    SOPORTE_GLOBAL sobre buzones ajenos: soporte ve todo, pero no tiene acceso
    nominal al CCPL, así que no aparece aquí → no recibe push del CCPL."""
    hoy = ahora_lima().date()
    orgs = [contrib.estudio_id]
    if contrib.cuenta_empresario_id:
        orgs.append(contrib.cuenta_empresario_id)
    return list(await session.scalars(
        select(Acceso.persona_id).where(
            or_(Acceso.estudio_id.in_(orgs),
                Acceso.contribuyente_id == contrib.id),
            or_(Acceso.vigencia_fin.is_(None), Acceso.vigencia_fin >= hoy))
        .distinct()))


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
    # Resumen de urgencia con el vencimiento más próximo (zAlerta-17 P1). Solo
    # fechas EXPLÍCITAS; no se infiere nada.
    ahora = ahora_lima()
    proximos = list(await session.scalars(
        select(Notificacion.plazo_vencimiento).where(
            Notificacion.contribuyente_id == contrib.id,
            Notificacion.plazo_vencimiento.is_not(None),
            Notificacion.plazo_vencimiento >= ahora)
        .order_by(Notificacion.plazo_vencimiento.asc())))
    cuerpo = f"Tienes {n_nuevas} aviso{plural} nuevo{plural}."
    if proximos:
        venc = proximos[0]
        try:
            venc_txt = venc.astimezone(TZ_LIMA).strftime("%d/%m")
        except Exception:
            venc_txt = venc.strftime("%d/%m")
        urgentes = sum(1 for v in proximos if v <= ahora + timedelta(days=7))
        if urgentes > 0:
            cuerpo += f" {urgentes} vence pronto el {venc_txt}."
        else:
            cuerpo += f" El más próximo vence el {venc_txt}."
    cuerpo += " Toca RESUMEN para verlos."

    payload = json.dumps({
        # Identidad alerta.pe (NO SUNAT). Cuerpo con resumen de urgencia.
        "title": "Novedades en tu Buzón SUNAT",
        "body": cuerpo,
        "url": "/resumen?from=push",   # RESUMEN → tabla con semáforo + splash
        "image": LEYENDA_IMG,          # imagen grande fija = leyenda de colores
        "acciones": True,              # el SW añade GRACIAS / RESUMEN
        "tag": f"buzon-{contrib.id}",  # re-aviso por buzón sin apilar (zAlerta-64)
        "requiere": bool(proximos),    # hay vencimientos → no descartar sola
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
