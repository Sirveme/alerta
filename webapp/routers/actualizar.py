"""
webapp/routers/actualizar.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Botón "Actualizar ahora" (zAlerta-01 B.6): scrapea AL INSTANTE un
contribuyente (ignora frescura) vía consulta_ahora.consultar_ahora.

NUNCA decimos "hay que esperar". Si SUNAT falla por intermitencia,
devolvemos un mensaje amable para reintentar (no error técnico crudo).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from db import get_session
from models import Contribuyente
from consulta_ahora import consultar_ahora, ScraperNoDisponible
from ..deps import UsuarioActual, requiere_escritura

router = APIRouter(tags=["actualizar"])


@router.post("/contribuyentes/{contribuyente_id}/actualizar")
async def actualizar_ahora(
    contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(requiere_escritura)):
    # Verificar que el contribuyente sea del tenant ANTES de scrapear
    async with get_session() as session:
        existe = await session.scalar(
            select(Contribuyente.id).where(
                Contribuyente.id == contribuyente_id,
                Contribuyente.estudio_id == user.estudio_id))
    if not existe:
        return JSONResponse(
            {"exito": False, "mensaje": "Contribuyente no encontrado."},
            status_code=404)

    try:
        resultado = await consultar_ahora(user.estudio_id, contribuyente_id)
    except ScraperNoDisponible:
        # Entorno sin Playwright (servicio web liviano): degradar con
        # elegancia, NO romper. El monitoreo automático sigue activo aparte.
        return JSONResponse({
            "exito": False,
            "mensaje": ("La actualización en vivo no está disponible en este "
                        "entorno. El monitoreo automático sigue activo."),
        }, status_code=503)
    except Exception:
        # No exponer el error técnico crudo al contador.
        return JSONResponse({
            "exito": False,
            "mensaje": "No se pudo conectar con SUNAT, reintentar.",
        }, status_code=502)

    if not resultado.get("exito"):
        return JSONResponse({
            "exito": False,
            "mensaje": "No se pudo conectar con SUNAT, reintentar.",
        }, status_code=502)

    stats = resultado.get("stats") or {}
    nuevos = stats.get("mensajes_nuevos", 0)
    return JSONResponse({
        "exito": True,
        "mensaje": (f"{nuevos} notificación(es) nueva(s)."
                    if nuevos else "Sin novedades nuevas."),
        "stats": stats,
        "notificaciones": resultado.get("notificaciones", []),
    })
