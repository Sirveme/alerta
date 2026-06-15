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
from sqlalchemy import select

from db import get_session
from models import Contribuyente, ahora_lima
from ..deps import UsuarioActual, requiere_escritura

logger = logging.getLogger("alertape.actualizar")

router = APIRouter(tags=["actualizar"])


@router.post("/contribuyentes/{contribuyente_id}/actualizar")
async def actualizar_ahora(
    contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(requiere_escritura)):
    # Marcar el flag de actualización (multi-tenant: filtra por estudio).
    async with get_session() as session:
        contrib = await session.scalar(
            select(Contribuyente).where(
                Contribuyente.id == contribuyente_id,
                Contribuyente.estudio_id == user.estudio_id))
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
