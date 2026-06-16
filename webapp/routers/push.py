"""
webapp/routers/push.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Suscripción a Web Push (zAlerta-07 C).

Persiste las suscripciones en la tabla push_suscripciones (una por
dispositivo/navegador). El ENVÍO lo hace el worker vía push_service.
Multi-tenant: cada suscripción cuelga del estudio del usuario.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from db import get_session
from models import PushSuscripcion
from ..deps import UsuarioActual, usuario_actual

router = APIRouter(tags=["push"])

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")


@router.get("/push/clave-publica")
async def clave_publica():
    """Clave pública VAPID para que el navegador cree la suscripción."""
    return JSONResponse({"public_key": VAPID_PUBLIC_KEY})


@router.post("/push/suscribir")
async def suscribir(request: Request,
                    user: UsuarioActual = Depends(usuario_actual)):
    sub = await request.json()
    endpoint = (sub or {}).get("endpoint")
    keys = (sub or {}).get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return JSONResponse({"ok": False, "error": "Suscripción inválida."},
                            status_code=400)

    async with get_session() as session:
        existente = await session.scalar(
            select(PushSuscripcion).where(
                PushSuscripcion.usuario_id == user.id,
                PushSuscripcion.endpoint == endpoint))
        if existente:
            # Reactivar/actualizar claves (pueden rotar al re-suscribirse).
            existente.p256dh = p256dh
            existente.auth = auth
            existente.activa = True
        else:
            session.add(PushSuscripcion(
                estudio_id=user.estudio_id, usuario_id=user.id,
                endpoint=endpoint, p256dh=p256dh, auth=auth, activa=True))
        await session.commit()
    return JSONResponse({"ok": True})


@router.post("/push/desuscribir")
async def desuscribir(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    sub = await request.json()
    endpoint = (sub or {}).get("endpoint")
    if not endpoint:
        return JSONResponse({"ok": True})
    async with get_session() as session:
        existente = await session.scalar(
            select(PushSuscripcion).where(
                PushSuscripcion.usuario_id == user.id,
                PushSuscripcion.endpoint == endpoint))
        if existente:
            existente.activa = False
            await session.commit()
    return JSONResponse({"ok": True})
