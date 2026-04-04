"""
services/rendipe_alertas.py — Alertas específicas del módulo RendiPe.

Define los tipos de alerta propios de rendición de gastos y viáticos,
y expone un helper que delega la creación a alertas_service.crear_alerta_por_tipo.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.services.alertas_service import crear_alerta_por_tipo

logger = logging.getLogger(__name__)

# Mapeo de tipos de alerta RendiPe → nivel de alerta
# Nivel: urgente (rojo), importante (amarillo), info (azul)
ALERTAS_RENDIPE: dict[str, str] = {
    "rendicion_vencida":           "urgente",
    "rendicion_por_vencer":        "importante",
    "gasto_observado":             "importante",
    "gasto_sin_comprobante":       "importante",
    "ruc_no_valido":               "importante",
    "saldo_negativo":              "urgente",
    "informe_pendiente":           "importante",
    "comision_aprobada":           "info",
    "rendicion_aprobada":          "info",
    "rendicion_rechazada":         "urgente",
    "comision_creada":             "info",
}


def crear_alerta_rendipe(
    db: Session,
    tenant_id: int,
    tipo: str,
    comision_id: Optional[int] = None,
    mensaje: str = "",
) -> None:
    """
    Crea una alerta de tipo RendiPe delegando a alertas_service.crear_alerta_por_tipo.

    Si el tipo no está registrado en ALERTAS_RENDIPE, loguea warning y no falla.
    La referencia se guarda como referencia a la tabla 'comisiones_rendipe'.
    """
    nivel = ALERTAS_RENDIPE.get(tipo)
    if nivel is None:
        logger.warning(f"Tipo de alerta RendiPe desconocido: {tipo}")
        return

    # Título legible a partir del tipo
    titulo = tipo.replace("_", " ").capitalize()

    crear_alerta_por_tipo(
        db=db,
        empresa_id=tenant_id,  # en RendiPe el tenant actúa como empresa
        tipo=tipo,
        mensaje=f"[RendiPe] {titulo}: {mensaje}",
        referencia_id=comision_id,
        referencia_tabla="comisiones_rendipe",
    )

    logger.info(
        f"Alerta RendiPe creada: tipo={tipo}, comision_id={comision_id}, nivel={nivel}"
    )
