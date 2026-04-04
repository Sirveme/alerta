"""
deudas.py — Modelo Deuda y DeudaPago para notificado.pro (gestión de cobranzas).

Decisiones técnicas:
- PK: Integer autoincremental. Alto volumen, JOINs frecuentes con pagos y notificaciones.
- Soft delete: SÍ en Deuda (datos de cobranza no se eliminan).
  NO en DeudaPago (es tabla de relación; si se deshace un pago, se borra físicamente).
- Ciclo: determina la recurrencia de la deuda (mensual, quincenal, etc.).
- Escalamiento: nivel 1-3 determina la urgencia y tipo de gestión.
  Se escala automáticamente según días de atraso configurable por empresa.
- DeudaPago: tabla pivote que registra pagos parciales o totales aplicados a una deuda.
  Una deuda puede tener N pagos parciales. Un pago puede cubrir N deudas.
"""

import enum
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class CicloDeuda(str, enum.Enum):
    """Frecuencia de cobro de la deuda."""
    MENSUAL = "mensual"
    QUINCENAL = "quincenal"
    SEMANAL = "semanal"
    UNICO = "unico"


class EstadoDeuda(str, enum.Enum):
    """Estado del ciclo de vida de la deuda."""
    PENDIENTE = "pendiente"
    PARCIAL = "parcial"
    PAGADO = "pagado"
    VENCIDO = "vencido"
    EN_GESTION = "en_gestion"
    INCOBRABLE = "incobrable"


class NivelEscalamiento(int, enum.Enum):
    """Nivel de escalamiento de cobranza."""
    RECORDATORIO = 1       # Nivel 1: recordatorio amigable
    URGENTE = 2            # Nivel 2: aviso urgente
    COBRANZA_EXTERNA = 3   # Nivel 3: derivar a cobranza externa


class Deuda(Base, TimestampMixin, SoftDeleteMixin):
    """
    Deuda de un deudor hacia una empresa (pensión, membresía, cuota, etc.).
    Módulo notificado.pro: academias, colegios, gimnasios, clubes, condominios.
    """

    __tablename__ = "deudas"
    __table_args__ = (
        Index("ix_deudas_empresa_estado", "empresa_id", "estado"),
        Index("ix_deudas_empresa_vencimiento", "empresa_id", "fecha_vencimiento"),
        Index("ix_deudas_deudor", "deudor_documento"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Datos del deudor
    deudor_nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    deudor_documento: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="DNI o RUC del deudor.",
    )
    deudor_telefono: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    deudor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Datos de la deuda
    concepto: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Descripción: 'Pensión Marzo 2025', 'Membresía Abril', etc.",
    )
    monto_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    monto_pagado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    moneda: Mapped[str] = mapped_column(String(3), nullable=False, default="PEN")

    ciclo: Mapped[CicloDeuda] = mapped_column(
        SAEnum(CicloDeuda, name="ciclo_deuda_enum", create_constraint=True),
        nullable=False,
        default=CicloDeuda.MENSUAL,
    )
    estado: Mapped[EstadoDeuda] = mapped_column(
        SAEnum(EstadoDeuda, name="estado_deuda_enum", create_constraint=True),
        nullable=False,
        default=EstadoDeuda.PENDIENTE,
    )

    fecha_emision: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)

    # Escalamiento
    nivel_escalamiento: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1,
        comment="Nivel 1=recordatorio, 2=urgente, 3=cobranza externa.",
    )

    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente", back_populates="deudas")
    pagos_aplicados: Mapped[list["DeudaPago"]] = relationship(
        "DeudaPago", back_populates="deuda", cascade="all, delete-orphan",
    )


class DeudaPago(Base, TimestampMixin):
    """
    Relación N:N entre Deuda y Pago.
    Permite pagos parciales: una deuda de S/500 puede tener 2 pagos de S/250.
    También permite que un pago de S/1000 cubra 2 deudas de S/500.
    No tiene soft delete — se elimina físicamente si se revierte el pago.
    """

    __tablename__ = "deudas_pagos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    deuda_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("deudas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pago_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pagos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Monto aplicado de este pago a esta deuda (puede ser parcial)
    monto_aplicado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # --- Relaciones ---
    deuda: Mapped["Deuda"] = relationship("Deuda", back_populates="pagos_aplicados")
    pago: Mapped["Pago"] = relationship("Pago")
