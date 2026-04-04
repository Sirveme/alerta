"""
acumulados.py — Tablas de acumulados: AcumBancos, AcumSIRE, AcumMensual.

Decisiones técnicas:
- PK: Integer autoincremental. Tablas de resumen con volumen moderado.
- Soft delete: NO. Son datos calculados/derivados, se recalculan si hay error.
  Si se necesita recalcular, se hace UPSERT (INSERT ON CONFLICT UPDATE).
- UNIQUE constraints compuestos para garantizar un solo registro por combinación
  de dimensiones (empresa + periodo + canal, etc.) y habilitar UPSERT.
- Particionamiento: NO por ahora. El volumen de acumulados mensuales es manejable
  (~12 registros/empresa/año para AcumMensual). Se puede particionar por año si
  crece a +10,000 empresas.
"""

from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

import enum


class TipoRegistroSIRE(str, enum.Enum):
    """Tipo de registro en el SIRE de SUNAT."""
    VENTAS = "ventas"
    COMPRAS = "compras"


class AcumBancos(Base, TimestampMixin):
    """
    Acumulado de ventas recibidas por canal bancario.
    Un registro por: empresa + emisor + mes + año + canal.
    Permite responder: "¿cuánto recibí por Yape en marzo 2025 del proveedor X?"
    """

    __tablename__ = "acum_bancos"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id", "ruc_emisor", "mes", "anio", "canal",
            name="uq_acum_bancos_periodo",
        ),
        Index("ix_acum_bancos_empresa_periodo", "empresa_id", "anio", "mes"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
    )
    ruc_emisor: Mapped[str] = mapped_column(
        String(11), nullable=False,
        comment="RUC del emisor del pago (cliente que pagó).",
    )
    mes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    anio: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    canal: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="Canal de pago: yape, plin, bcp, etc.",
    )

    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    impuestos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")


class AcumSIRE(Base, TimestampMixin):
    """
    Acumulado según SUNAT SIRE — sin detalle de productos.
    Un registro por: empresa + periodo + tipo_registro.
    Se usa para conciliación con lo declarado en SUNAT.
    """

    __tablename__ = "acum_sire"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id", "periodo", "tipo_registro",
            name="uq_acum_sire_periodo",
        ),
        Index("ix_acum_sire_empresa_periodo", "empresa_id", "periodo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
    )
    periodo: Mapped[str] = mapped_column(
        String(7), nullable=False,
        comment="Periodo tributario en formato YYYY-MM, ej: 2025-03.",
    )
    tipo_registro: Mapped[TipoRegistroSIRE] = mapped_column(
        SAEnum(TipoRegistroSIRE, name="tipo_registro_sire_enum", create_constraint=True),
        nullable=False,
    )

    total_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_igv: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")


class AcumMensual(Base, TimestampMixin):
    """
    Consolidado mensual general por empresa.
    Un registro por: empresa + mes + año.
    Vista de alto nivel para dashboards y reportes ejecutivos.
    """

    __tablename__ = "acum_mensual"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id", "mes", "anio",
            name="uq_acum_mensual_periodo",
        ),
        Index("ix_acum_mensual_empresa_periodo", "empresa_id", "anio", "mes"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
    )
    mes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    anio: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    total_ventas: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_compras: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_cobrado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_pendiente: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_alertas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")
