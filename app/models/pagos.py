"""
pagos.py — Modelo Pago: registro de pagos digitales recibidos por la empresa.

Decisiones técnicas:
- PK: Integer autoincremental. Alto volumen de registros (miles por empresa/mes).
  Integer es más eficiente para índices, JOINs y ordenamiento que UUID.
- Soft delete: SÍ. Los pagos son datos contables que no deben eliminarse.
- Estado: enum con 5 valores del ciclo de vida del pago.
  pendiente_cruce → se acaba de registrar, esperando match con comprobante.
  cruzado → match exitoso con comprobante SUNAT.
  sin_comprobante → el pagador no emitió comprobante.
  duplicado → detectado como pago duplicado (mismo monto, hora cercana, mismo canal).
  rechazado → pago rechazado manualmente o por regla de negocio.
- Canal: los canales de pago digital más usados en Perú + efectivo.
- Índices compuestos: (empresa_id, fecha_pago) y (empresa_id, estado) para las
  queries más frecuentes (listado por fecha, filtrado por estado).
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class EstadoPago(str, enum.Enum):
    """Ciclo de vida de un pago registrado."""
    PENDIENTE_CRUCE = "pendiente_cruce"
    CRUZADO = "cruzado"
    SIN_COMPROBANTE = "sin_comprobante"
    DUPLICADO = "duplicado"
    RECHAZADO = "rechazado"


class CanalPago(str, enum.Enum):
    """Canal por el cual se recibió el pago."""
    YAPE = "yape"
    PLIN = "plin"
    BCP = "bcp"
    BBVA = "bbva"
    INTERBANK = "interbank"
    BNACION = "bnacion"
    SCOTIABANK = "scotiabank"
    EFECTIVO = "efectivo"
    OTRO = "otro"


class Pago(Base, TimestampMixin, SoftDeleteMixin):
    """
    Registro de un pago digital recibido.
    Se captura de notificaciones bancarias, Yape/Plin, o registro manual.
    """

    __tablename__ = "pagos"
    __table_args__ = (
        # Consultas frecuentes: pagos de una empresa en un rango de fechas
        Index("ix_pagos_empresa_fecha", "empresa_id", "fecha_pago"),
        # Filtrado por estado dentro de una empresa
        Index("ix_pagos_empresa_estado", "empresa_id", "estado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Datos del pago
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(
        String(3), nullable=False, default="PEN",
        comment="Código ISO 4217: PEN, USD.",
    )
    canal: Mapped[CanalPago] = mapped_column(
        SAEnum(CanalPago, name="canal_pago_enum", create_constraint=True),
        nullable=False,
    )
    estado: Mapped[EstadoPago] = mapped_column(
        SAEnum(EstadoPago, name="estado_pago_enum", create_constraint=True),
        nullable=False,
        default=EstadoPago.PENDIENTE_CRUCE,
    )

    fecha_pago: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Fecha/hora del pago según la fuente (banco, Yape, etc.).",
    )

    # Identificación del pagador
    pagador_nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pagador_documento: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="DNI o RUC del pagador, si se conoce.",
    )
    pagador_telefono: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)

    # Referencia bancaria / operación
    numero_operacion: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True,
        comment="Número de operación del banco o código de transferencia.",
    )
    referencia_banco: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Cruce con comprobante (cuando estado = cruzado)
    comprobante_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("comprobantes.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Metadata adicional capturada del canal (varía por banco/billetera)
    metadata_canal: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Datos extra del canal: screenshot parseado, notificación raw, etc.",
    )

    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente", back_populates="pagos")
    comprobante: Mapped[Optional["Comprobante"]] = relationship(
        "Comprobante", back_populates="pagos", foreign_keys=[comprobante_id],
    )
