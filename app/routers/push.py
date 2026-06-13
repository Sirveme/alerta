"""Router de Web Push: suscripción, prueba, gestión."""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.dependencies import require_authenticated
from app.core.templates import templates
from app.models import PushSuscripcion, Usuario
from app.services.push_service import enviar_push

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Devuelve la clave pública VAPID (necesaria para suscribirse en el cliente)."""
    return {"public_key": settings.vapid_public_key}


@router.post("/suscribir")
async def suscribir(
    request: Request,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    """Recibe suscripción del navegador y la guarda."""
    data = await request.json()
    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return JSONResponse({"ok": False, "error": "Datos incompletos"}, status_code=400)

    # ¿ya existe esta suscripción?
    result = await db.execute(
        select(PushSuscripcion)
        .where(PushSuscripcion.usuario_id == usuario.id)
        .where(PushSuscripcion.endpoint == endpoint)
    )
    existente = result.scalar_one_or_none()

    if existente:
        existente.activa = True
        existente.ultima_actividad = datetime.now(timezone.utc)
        existente.p256dh = p256dh
        existente.auth = auth
        existente.user_agent = request.headers.get("user-agent", "")[:500]
    else:
        nueva = PushSuscripcion(
            usuario_id=usuario.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=request.headers.get("user-agent", "")[:500],
            activa=True,
            ultima_actividad=datetime.now(timezone.utc),
        )
        db.add(nueva)

    await db.commit()
    return {"ok": True}


@router.post("/test")
async def enviar_push_prueba(
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    """Manda un push de prueba al usuario actual."""
    resultado = await enviar_push(
        db=db,
        usuario_id=usuario.id,
        titulo="🔔 Alerta.pe — Notificación de prueba",
        cuerpo="Si ves esto, tus notificaciones funcionan correctamente.",
        url="/dashboard",
    )
    return resultado
