"""
services/cruce_service.py — Servicio de cruce Pago <-> Comprobante.

Algoritmo:
1. Buscar comprobantes pendientes de la empresa
2. Filtrar por monto exacto (tolerancia ±0.01 por redondeo bancario)
3. Un solo match → cruce automático
4. Múltiples matches → alerta para revisión manual
5. Sin match → pago queda sin_comprobante, alerta
6. Pago menor al comprobante → posible pago parcial, alerta
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.comprobantes import Comprobante, EstadoComprobante
from app.models.pagos import Pago, EstadoPago

logger = logging.getLogger(__name__)

TOLERANCIA_MONTO = Decimal("0.01")


@dataclass
class ResultadoCruce:
    """Resultado de un intento de cruce."""
    exito: bool
    tipo: str  # cruce_exacto | multiples_matches | sin_match | pago_parcial
    comprobante_id: Optional[int] = None
    mensaje: str = ""


def cruzar_pago_con_comprobante(
    db: Session,
    pago_id: int,
    empresa_id: int,
) -> ResultadoCruce:
    """
    Intenta cruzar un pago con comprobantes pendientes de la empresa.
    """
    from app.services.alertas_service import crear_alerta_por_tipo

    pago = db.execute(
        select(Pago).where(Pago.id == pago_id, Pago.deleted_at == None)
    ).scalar_one_or_none()

    if not pago:
        return ResultadoCruce(exito=False, tipo="error", mensaje="Pago no encontrado")

    if pago.estado == EstadoPago.CRUZADO:
        return ResultadoCruce(exito=True, tipo="ya_cruzado", mensaje="Pago ya estaba cruzado")

    # Buscar comprobantes pendientes con monto similar
    monto_min = pago.monto - TOLERANCIA_MONTO
    monto_max = pago.monto + TOLERANCIA_MONTO

    comprobantes = db.execute(
        select(Comprobante).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.estado == EstadoComprobante.PENDIENTE,
            Comprobante.total >= monto_min,
            Comprobante.total <= monto_max,
            Comprobante.deleted_at == None,
        )
    ).scalars().all()

    if len(comprobantes) == 1:
        # Cruce exacto automático
        comp = comprobantes[0]
        pago.estado = EstadoPago.CRUZADO
        pago.comprobante_id = comp.id
        comp.estado = EstadoComprobante.VALIDADO
        db.commit()

        crear_alerta_por_tipo(
            db, empresa_id, "cruce_exitoso",
            mensaje=f"Pago #{pago_id} (S/{pago.monto}) cruzado con {comp.serie}-{comp.correlativo}",
            referencia_id=pago_id,
            referencia_tabla="pagos",
        )

        logger.info(f"Cruce exitoso: pago #{pago_id} ↔ comprobante #{comp.id}")
        return ResultadoCruce(
            exito=True, tipo="cruce_exacto",
            comprobante_id=comp.id,
            mensaje=f"Cruzado con {comp.serie}-{comp.correlativo}",
        )

    if len(comprobantes) > 1:
        # Múltiples matches — requiere revisión manual
        crear_alerta_por_tipo(
            db, empresa_id, "pago_sin_comprobante",
            mensaje=(
                f"Pago #{pago_id} (S/{pago.monto}) tiene {len(comprobantes)} "
                f"comprobantes posibles. Requiere revisión manual."
            ),
            referencia_id=pago_id,
            referencia_tabla="pagos",
        )
        return ResultadoCruce(
            exito=False, tipo="multiples_matches",
            mensaje=f"{len(comprobantes)} comprobantes posibles",
        )

    # Sin match exacto — buscar posible pago parcial
    comprobantes_mayores = db.execute(
        select(Comprobante).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.estado == EstadoComprobante.PENDIENTE,
            Comprobante.total > pago.monto + TOLERANCIA_MONTO,
            Comprobante.deleted_at == None,
        ).order_by(Comprobante.total)
    ).scalars().all()

    if comprobantes_mayores:
        # Posible pago parcial
        pago.estado = EstadoPago.SIN_COMPROBANTE
        db.commit()

        crear_alerta_por_tipo(
            db, empresa_id, "pago_parcial",
            mensaje=(
                f"Pago #{pago_id} (S/{pago.monto}) podría ser parcial. "
                f"Comprobante más cercano: {comprobantes_mayores[0].serie}-"
                f"{comprobantes_mayores[0].correlativo} (S/{comprobantes_mayores[0].total})"
            ),
            referencia_id=pago_id,
            referencia_tabla="pagos",
        )
        return ResultadoCruce(
            exito=False, tipo="pago_parcial",
            mensaje="Posible pago parcial",
        )

    # Sin match de ningún tipo
    pago.estado = EstadoPago.SIN_COMPROBANTE
    db.commit()

    crear_alerta_por_tipo(
        db, empresa_id, "pago_sin_comprobante",
        mensaje=f"Pago #{pago_id} (S/{pago.monto}) sin comprobante asociado",
        referencia_id=pago_id,
        referencia_tabla="pagos",
    )

    return ResultadoCruce(
        exito=False, tipo="sin_match",
        mensaje="Sin comprobante asociado",
    )


def recalcular_cruces_pendientes(db: Session, empresa_id: int) -> int:
    """
    Recalcula cruces: intenta cruzar pagos sin_comprobante con comprobantes nuevos.
    Corre periódicamente (Celery beat, cada hora).
    Retorna cantidad de cruces exitosos.
    """
    pagos_pendientes = db.execute(
        select(Pago).where(
            Pago.empresa_id == empresa_id,
            Pago.estado == EstadoPago.SIN_COMPROBANTE,
            Pago.deleted_at == None,
        )
    ).scalars().all()

    cruces = 0
    for pago in pagos_pendientes:
        resultado = cruzar_pago_con_comprobante(db, pago.id, empresa_id)
        if resultado.exito:
            cruces += 1

    return cruces
