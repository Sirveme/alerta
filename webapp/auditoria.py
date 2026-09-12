"""
webapp/auditoria.py — alerta.pe (Capa 1)
═══════════════════════════════════════════════════════════════════════
Helper único para escribir la bitácora APPEND-ONLY `auditoria`: QUIÉN
(persona_id) hizo QUÉ (accion) sobre QUÉ RUC/estudio, CUÁNDO. Toda acción
sensible (invitar, aceptar, crear asistente, revocar, …) llama aquí.

Diseño: NUNCA rompe la operación que audita. Si el INSERT de auditoría falla,
se loguea y se sigue (la traza es innegociable como POLÍTICA, pero un fallo de
escritura no debe tumbar la acción del usuario). Recibe la MISMA `session` para
que la fila viaje en la misma transacción que la acción.
"""

from __future__ import annotations

import logging
import uuid

from models import Auditoria

logger = logging.getLogger("alertape.auditoria")


async def registrar_auditoria(
    session, *, persona_id, accion: str,
    estudio_id=None, contribuyente_id=None,
    objeto_tipo: str | None = None, objeto_id=None,
    datos: dict | None = None) -> None:
    """Agrega (no commitea) una fila de auditoría a la sesión actual. El commit
    lo hace la acción que la invoca, para que traza y efecto sean atómicos."""
    try:
        session.add(Auditoria(
            persona_id=persona_id, accion=accion,
            estudio_id=estudio_id, contribuyente_id=contribuyente_id,
            objeto_tipo=objeto_tipo, objeto_id=objeto_id, datos=datos))
    except Exception as e:  # nunca romper la acción por la traza
        logger.warning("auditoria: no se pudo registrar %s (sigo): %s", accion, e)
