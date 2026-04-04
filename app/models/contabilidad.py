"""
models/contabilidad.py — Plan Contable PCGE, Asientos, Tipo de Cambio, Cronograma SUNAT.

Nuevas tablas de sesión 5:
- PlanContable: PCGE 2020 peruano
- AsientoContable + LineaAsiento: partida doble automática
- TipoCambioHistorico: TC diario BCRP/SBS
- CronogramaSunat: vencimientos tributarios
- SeguimientoCorreccion: proceso de corrección de comprobantes errados
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric,
    SmallInteger, String, Text, UniqueConstraint, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


# ── Plan Contable ────────────────────────────────────────────

class TipoCuenta(str, enum.Enum):
    ACTIVO = "activo"
    PASIVO = "pasivo"
    PATRIMONIO = "patrimonio"
    INGRESO = "ingreso"
    GASTO = "gasto"
    RESULTADO = "resultado"


class NaturalezaCuenta(str, enum.Enum):
    DEUDORA = "deudora"
    ACREEDORA = "acreedora"


class PlanContable(Base):
    """Plan Contable General Empresarial (PCGE 2020) — Perú."""
    __tablename__ = "plan_contable"

    codigo: Mapped[str] = mapped_column(String(10), primary_key=True)
    denominacion: Mapped[str] = mapped_column(String(200), nullable=False)
    tipo: Mapped[Optional[TipoCuenta]] = mapped_column(
        SAEnum(TipoCuenta, name="tipo_cuenta_enum"), nullable=True
    )
    naturaleza: Mapped[Optional[NaturalezaCuenta]] = mapped_column(
        SAEnum(NaturalezaCuenta, name="naturaleza_cuenta_enum"), nullable=True
    )
    nivel: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2,
        comment="1=elemento, 2=cuenta, 3=subcuenta, 4=divisionaria"
    )


# ── Asientos Contables ──────────────────────────────────────

class EstadoAsiento(str, enum.Enum):
    BORRADOR = "borrador"
    APROBADO = "aprobado"
    EXPORTADO = "exportado"


class GeneradoPor(str, enum.Enum):
    AUTOMATICO = "automatico"
    MANUAL = "manual"
    IA = "ia"


class AsientoContable(Base, TimestampMixin):
    """Asiento contable de partida doble."""
    __tablename__ = "asiento_contable"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    empresa_id: Mapped[int] = mapped_column(Integer, ForeignKey("empresas_cliente.id"), nullable=False)
    comprobante_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("comprobantes.id"), nullable=True)
    periodo: Mapped[str] = mapped_column(String(7), nullable=False, comment="YYYY-MM")
    numero_asiento: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    glosa: Mapped[str] = mapped_column(String(500), nullable=False)
    moneda: Mapped[str] = mapped_column(String(3), default="PEN")
    tipo_cambio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    generado_por: Mapped[GeneradoPor] = mapped_column(
        SAEnum(GeneradoPor, name="generado_por_asiento_enum"), default=GeneradoPor.AUTOMATICO
    )
    estado: Mapped[EstadoAsiento] = mapped_column(
        SAEnum(EstadoAsiento, name="estado_asiento_enum"), default=EstadoAsiento.BORRADOR
    )

    lineas: Mapped[list["LineaAsiento"]] = relationship(
        "LineaAsiento", back_populates="asiento", cascade="all, delete-orphan", lazy="selectin"
    )
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")
    comprobante: Mapped[Optional["Comprobante"]] = relationship("Comprobante")


class LineaAsiento(Base):
    """Línea de un asiento contable (debe/haber)."""
    __tablename__ = "linea_asiento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asiento_id: Mapped[int] = mapped_column(Integer, ForeignKey("asiento_contable.id"), nullable=False)
    orden: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    cuenta_codigo: Mapped[str] = mapped_column(String(10), ForeignKey("plan_contable.codigo"), nullable=False)
    denominacion: Mapped[str] = mapped_column(String(200), nullable=False)
    debe: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    haber: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    debe_me: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True, comment="Moneda extranjera")
    haber_me: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    glosa_linea: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    asiento: Mapped["AsientoContable"] = relationship("AsientoContable", back_populates="lineas")
    cuenta: Mapped["PlanContable"] = relationship("PlanContable")


# ── Tipo de Cambio ───────────────────────────────────────────

class TipoCambioHistorico(Base):
    """Tipo de cambio diario BCRP/SBS."""
    __tablename__ = "tipo_cambio_historico"

    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    compra: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    venta: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    fuente: Mapped[str] = mapped_column(String(10), default="bcrp")


# ── Cronograma SUNAT ─────────────────────────────────────────

class CronogramaSunat(Base):
    """Vencimientos tributarios según cronograma anual SUNAT."""
    __tablename__ = "cronograma_sunat"
    __table_args__ = (
        UniqueConstraint("anio", "mes", "ultimo_digito_ruc", "tipo_obligacion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anio: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ultimo_digito_ruc: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="0-9")
    tipo_obligacion: Mapped[str] = mapped_column(String(50), nullable=False)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)


# ── Seguimiento de Corrección ────────────────────────────────

class EstadoCorreccion(str, enum.Enum):
    PENDIENTE = "pendiente"
    CONTACTADO = "contactado"
    EN_PROCESO = "en_proceso"
    CORREGIDO = "corregido"
    BLOQUEADO_DEFINITIVO = "bloqueado_definitivo"


class SeguimientoCorreccion(Base, TimestampMixin):
    """Seguimiento del proceso de corrección de un comprobante errado."""
    __tablename__ = "seguimiento_correccion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comprobante_id: Mapped[int] = mapped_column(Integer, ForeignKey("comprobantes.id"), nullable=False)
    empresa_id: Mapped[int] = mapped_column(Integer, ForeignKey("empresas_cliente.id"), nullable=False)
    ruc_proveedor: Mapped[str] = mapped_column(String(11), nullable=False)
    nombre_proveedor: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    correo_proveedor: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    whatsapp_proveedor: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    errores_detectados: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    nivel_actual: Mapped[int] = mapped_column(SmallInteger, default=1)
    estado: Mapped[EstadoCorreccion] = mapped_column(
        SAEnum(EstadoCorreccion, name="estado_correccion_enum"), default=EstadoCorreccion.PENDIENTE
    )
    nc_recibida_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("comprobantes.id"), nullable=True)
    fecha_ultimo_contacto: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    historial: Mapped[Optional[dict]] = mapped_column(JSONB, default=list)

    comprobante: Mapped["Comprobante"] = relationship("Comprobante", foreign_keys=[comprobante_id])
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")
