"""
services/duplicados.py — Motor de detección de duplicados en 3 niveles.

REGLA DE ORO: nunca rechazar silenciosamente.
Un duplicado se guarda pero se marca — el contador decide qué hacer.

Nivel 1 — Exacto: misma combinación (ruc_emisor, serie, correlativo, ruc_receptor)
Nivel 2 — Multi-origen: mismo comprobante llegó por canales distintos
Nivel 3 — Fuzzy: mismo emisor + mismo monto + misma fecha ±1 día (posible duplicado)
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.comprobantes import Comprobante, EstadoComprobante

logger = logging.getLogger(__name__)


@dataclass
class ResultadoDuplicado:
    """Resultado del motor de duplicados."""
    es_duplicado: bool
    nivel: int  # 0=no duplicado, 1=exacto, 2=multi_origen, 3=fuzzy
    original_id: Optional[int] = None
    accion: str = "guardar"  # guardar | guardar_como_duplicado | alerta_revision


def verificar_duplicado(
    db: Session,
    empresa_id: int,
    ruc_emisor: str,
    serie: str,
    correlativo: str,
    ruc_receptor: str,
    monto_total: Optional[Decimal] = None,
    fecha_emision=None,
) -> ResultadoDuplicado:
    """
    Verifica si un comprobante es duplicado en 3 niveles.

    Nivel 1 — Exacto (UNIQUE constraint):
      (ruc_emisor, serie, correlativo, ruc_receptor)
      → guardar con estado 'duplicado_exacto'

    Nivel 2 — Mismo comprobante, distinto origen:
      Mismo ruc_emisor + serie + correlativo en otra empresa del mismo tenant
      → guardar con referencia al original

    Nivel 3 — Posible duplicado (fuzzy):
      Mismo emisor + mismo monto (±0.01) + misma fecha ±1 día
      → NO bloquear, generar alerta para revisión manual
    """

    # --- Nivel 1: Exacto ---
    existente = db.execute(
        select(Comprobante).where(
            Comprobante.ruc_emisor == ruc_emisor,
            Comprobante.serie == serie,
            Comprobante.correlativo == correlativo,
            Comprobante.ruc_receptor == ruc_receptor,
            Comprobante.deleted_at == None,
        )
    ).scalar_one_or_none()

    if existente:
        logger.info(
            f"Duplicado exacto (Nivel 1): {ruc_emisor} {serie}-{correlativo} "
            f"ya existe como comprobante #{existente.id}"
        )
        return ResultadoDuplicado(
            es_duplicado=True,
            nivel=1,
            original_id=existente.id,
            accion="guardar_como_duplicado",
        )

    # --- Nivel 2: Multi-origen ---
    # Mismo comprobante (emisor+serie+correlativo) pero para otra empresa
    # Esto pasa cuando el emisor envía el XML y el receptor también lo reenvía
    multi_origen = db.execute(
        select(Comprobante).where(
            Comprobante.ruc_emisor == ruc_emisor,
            Comprobante.serie == serie,
            Comprobante.correlativo == correlativo,
            Comprobante.deleted_at == None,
            # Diferente receptor o diferente empresa
            Comprobante.ruc_receptor != ruc_receptor,
        )
    ).scalar_one_or_none()

    if multi_origen:
        logger.info(
            f"Duplicado multi-origen (Nivel 2): {ruc_emisor} {serie}-{correlativo} "
            f"existe para otro receptor, comprobante #{multi_origen.id}"
        )
        return ResultadoDuplicado(
            es_duplicado=True,
            nivel=2,
            original_id=multi_origen.id,
            accion="guardar_como_duplicado",
        )

    # --- Nivel 3: Fuzzy ---
    if monto_total and fecha_emision:
        tolerancia_monto = Decimal("0.01")
        tolerancia_fecha = timedelta(days=1)

        fuzzy_match = db.execute(
            select(Comprobante).where(
                Comprobante.ruc_emisor == ruc_emisor,
                Comprobante.ruc_receptor == ruc_receptor,
                Comprobante.empresa_id == empresa_id,
                Comprobante.deleted_at == None,
                # Monto similar (±0.01)
                Comprobante.total >= monto_total - tolerancia_monto,
                Comprobante.total <= monto_total + tolerancia_monto,
                # Fecha similar (±1 día)
                Comprobante.fecha_emision >= fecha_emision - tolerancia_fecha,
                Comprobante.fecha_emision <= fecha_emision + tolerancia_fecha,
                # Pero serie/correlativo DISTINTO (si fuera igual, sería Nivel 1)
                ~and_(Comprobante.serie == serie, Comprobante.correlativo == correlativo),
            )
        ).scalar_one_or_none()

        if fuzzy_match:
            logger.info(
                f"Posible duplicado (Nivel 3): {ruc_emisor} {serie}-{correlativo} "
                f"similar a comprobante #{fuzzy_match.id}"
            )
            return ResultadoDuplicado(
                es_duplicado=True,
                nivel=3,
                original_id=fuzzy_match.id,
                accion="alerta_revision",
            )

    # No es duplicado
    return ResultadoDuplicado(
        es_duplicado=False,
        nivel=0,
        accion="guardar",
    )
