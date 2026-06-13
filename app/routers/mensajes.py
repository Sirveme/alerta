"""Acciones sobre mensajes (marcar visto, archivar, etc)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_authenticated
from app.models import ClienteRuc, MensajeBuzon, Usuario

router = APIRouter(prefix="/mensajes", tags=["mensajes"])


@router.post("/{mensaje_id}/marcar-visto")
async def marcar_visto(
    mensaje_id: int,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    # Verificar que el mensaje pertenece a un cliente del contador
    result = await db.execute(
        select(MensajeBuzon)
        .join(ClienteRuc, ClienteRuc.id == MensajeBuzon.cliente_ruc_id)
        .where(MensajeBuzon.id == mensaje_id)
        .where(ClienteRuc.contador_id == usuario.id)
    )
    mensaje = result.scalar_one_or_none()
    if not mensaje:
        raise HTTPException(404, "Mensaje no encontrado")

    if not mensaje.visto:
        mensaje.visto = True
        mensaje.fecha_primer_visto = datetime.now(timezone.utc)
        await db.commit()

    return JSONResponse({"ok": True, "visto": True})


@router.post("/{mensaje_id}/archivar")
async def archivar(
    mensaje_id: int,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MensajeBuzon)
        .join(ClienteRuc, ClienteRuc.id == MensajeBuzon.cliente_ruc_id)
        .where(MensajeBuzon.id == mensaje_id)
        .where(ClienteRuc.contador_id == usuario.id)
    )
    mensaje = result.scalar_one_or_none()
    if not mensaje:
        raise HTTPException(404, "Mensaje no encontrado")
    mensaje.archivado = True
    await db.commit()
    return JSONResponse({"ok": True, "archivado": True})
