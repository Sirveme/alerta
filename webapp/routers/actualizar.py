"""
webapp/routers/actualizar.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Botón "Actualizar ahora": actualización bajo demanda.

zAlerta-04: la WebApp es LIVIANA (no tiene Playwright a propósito), así que
el botón NO scrapea en el proceso web. En su lugar MARCA el flag
`actualizar_solicitado` en el contribuyente; el worker separado (worker.py,
con Playwright) lo scrapea con prioridad en su próximo ciclo corto y limpia
el flag. La respuesta al contador es optimista (nunca un 502 crudo).

La función consulta_ahora.consultar_ahora se CONSERVA para uso directo en
local o en el worker (scraping sincrónico real).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, func

from db import get_session
from models import Contribuyente, Notificacion, ETIQUETA_TIPO_DOCUMENTO, ahora_lima
from ..deps import (
    UsuarioActual, usuario_actual, requiere_escritura, contribuyente_accesible,
)

logger = logging.getLogger("alertape.actualizar")

router = APIRouter(tags=["actualizar"])


def _etiqueta_tipo(tipo_enum) -> str:
    clave = tipo_enum.value if tipo_enum is not None else None
    return ETIQUETA_TIPO_DOCUMENTO.get(clave, "Otros")


@router.get("/contribuyentes/{contribuyente_id}/estado-scrapeo")
async def estado_scrapeo(
    contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(usuario_actual)):
    """Estado liviano para el indicador de 'Actualizar ahora' (zAlerta-07 D):
    ultimo_scrapeo_at + conteos por tipo (instantáneo, desde BD). Multi-tenant."""
    async with get_session() as session:
        contrib = await contribuyente_accesible(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        total = await session.scalar(
            select(func.count(Notificacion.id)).where(
                Notificacion.contribuyente_id == contribuyente_id)) or 0
        rows = (await session.execute(
            select(Notificacion.tipo_documento_enum, func.count(Notificacion.id))
            .where(Notificacion.contribuyente_id == contribuyente_id)
            .group_by(Notificacion.tipo_documento_enum))).all()
    conteos = [{"etiqueta": _etiqueta_tipo(t), "n": n} for t, n in rows]
    return JSONResponse({
        "ok": True,
        "ultimo_scrapeo_at": (contrib.ultimo_scrapeo_at.isoformat()
                              if contrib.ultimo_scrapeo_at else None),
        "total": total,
        "conteos": conteos,
    })


@router.post("/contribuyentes/{contribuyente_id}/actualizar")
async def actualizar_ahora(
    contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(usuario_actual)):
    # zAlerta-37 BUG B: "Actualizar ahora" es un refresco del PROPIO buzón, no una
    # mutación de tenant. El EMPRESARIO (solo-lectura) debe poder dispararlo sobre
    # su RUC — antes `requiere_escritura` le devolvía 403 y el flag nunca se marcaba.
    # Seguridad: el scope multi-tenant (estudio propio o cuenta_empresario) impide
    # tocar RUCs ajenos. La mecánica del flag NO cambia.
    async with get_session() as session:
        contrib = await session.scalar(
            select(Contribuyente).where(
                Contribuyente.id == contribuyente_id,
                (Contribuyente.estudio_id == user.estudio_id)
                | (Contribuyente.cuenta_empresario_id == user.estudio_id)))
        if not contrib:
            return JSONResponse(
                {"exito": False, "mensaje": "Contribuyente no encontrado."},
                status_code=404)

        contrib.actualizar_solicitado = True
        contrib.actualizar_solicitado_at = ahora_lima()
        await session.commit()

    logger.info("Actualización solicitada para contribuyente %s (estudio %s).",
                contribuyente_id, user.estudio_id)

    # Respuesta optimista: el worker la procesará en unos momentos.
    return JSONResponse({
        "exito": True,
        "solicitado": True,
        "mensaje": ("Actualización solicitada. En unos momentos verás "
                    "los datos frescos."),
    })
