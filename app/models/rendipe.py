"""
models/rendipe.py — Módulo RendiPe: Rendición de Gastos y Viáticos para Sector Público peruano.

Tablas:
- InstitucionConfig: configuración específica por institución pública
- Servidor: funcionario/servidor público de la institución
- Comision: comisión de servicio (autorización de viaje)
- GastoComision: comprobante/gasto capturado durante la comisión
- InformeComision: informe de resultados de la comisión
- SaldoComision: saldo final (devolución o a favor del servidor)

Decisiones técnicas:
- PK Integer en todas las tablas (volumen moderado, JOINs frecuentes).
- Soft delete: NO (datos públicos de rendición deben conservarse íntegramente).
- JSONB para: rubros_habilitados, escala_viaticos, formato_rendicion, formato_informe
  (configuraciones semi-estructuradas que varían por institución).
- Geolocalización en GastoComision: opcional, para verificar que el gasto
  se realizó en el lugar de la comisión (transparencia).
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, SmallInteger, String, Text, UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


# ── Enums ────────────────────────────────────────────────────

class TipoInstitucion(str, enum.Enum):
    MUNICIPALIDAD_DISTRITAL = "municipalidad_distrital"
    MUNICIPALIDAD_PROVINCIAL = "municipalidad_provincial"
    GOBIERNO_REGIONAL = "gobierno_regional"
    HOSPITAL = "hospital"
    UNIVERSIDAD = "universidad"
    INSTITUTO = "instituto"
    MINISTERIO = "ministerio"
    OTRO_PUBLICO = "otro_publico"


class EstadoComision(str, enum.Enum):
    AUTORIZADA = "autorizada"
    EN_CURSO = "en_curso"
    PENDIENTE_REND = "pendiente_rend"
    RENDIDA = "rendida"
    APROBADA = "aprobada"
    CERRADA = "cerrada"
    OBSERVADA = "observada"


class OrigenGasto(str, enum.Enum):
    FOTO = "foto"
    XML = "xml"
    MANUAL = "manual"
    DECLARACION_JURADA = "declaracion_jurada"
    CORREO = "correo"


class EstadoValidacionGasto(str, enum.Enum):
    PENDIENTE = "pendiente"
    VALIDO = "valido"
    OBSERVADO = "observado"
    BLOQUEADO = "bloqueado"
    SIN_COMPROBANTE_ELECTRONICO = "sin_comprobante_electronico"


class ViaticosPagadosPor(str, enum.Enum):
    PROPIA = "propia"
    INVITANTE = "invitante"
    MIXTO = "mixto"


class EstadoInforme(str, enum.Enum):
    BORRADOR = "borrador"
    GENERADO_IA = "generado_ia"
    REVISADO = "revisado"
    ENVIADO = "enviado"


class TipoSaldo(str, enum.Enum):
    DEVOLUCION = "devolucion"
    FAVOR_SERVIDOR = "favor_servidor"
    SALDO_CERO = "saldo_cero"


# ── Modelos ──────────────────────────────────────────────────

class InstitucionConfig(Base, TimestampMixin):
    """Configuración específica de cada institución pública para RendiPe."""
    __tablename__ = "rendipe_institucion_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), unique=True, nullable=False
    )
    ruc: Mapped[str] = mapped_column(String(11), nullable=False)
    nombre: Mapped[str] = mapped_column(String(300), nullable=False)
    tipo_institucion: Mapped[TipoInstitucion] = mapped_column(
        SAEnum(TipoInstitucion, name="tipo_institucion_enum"), nullable=False
    )
    ubigeo: Mapped[Optional[str]] = mapped_column(String(6), nullable=True, comment="Código INEI")

    plazo_rendicion_dias: Mapped[int] = mapped_column(SmallInteger, default=10)
    rubros_habilitados: Mapped[Optional[dict]] = mapped_column(JSONB, default=list)
    escala_viaticos: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    administrador_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    contador_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    tesorero_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    cajero_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)

    formato_rendicion: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    formato_informe: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class Servidor(Base, TimestampMixin):
    """Servidor o funcionario público de la institución."""
    __tablename__ = "rendipe_servidor"
    __table_args__ = (UniqueConstraint("tenant_id", "dni"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    usuario_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    dni: Mapped[str] = mapped_column(String(8), nullable=False)
    codigo_interno: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    apellidos: Mapped[str] = mapped_column(String(200), nullable=False)
    nombres: Mapped[str] = mapped_column(String(200), nullable=False)
    cargo: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    nivel_remunerativo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    celular: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Comision(Base, TimestampMixin):
    """Comisión de servicio — autoriza viaje y asigna viáticos."""
    __tablename__ = "rendipe_comision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    servidor_id: Mapped[int] = mapped_column(Integer, ForeignKey("rendipe_servidor.id"), nullable=False)

    destino_ciudad: Mapped[str] = mapped_column(String(200), nullable=False)
    destino_pais: Mapped[str] = mapped_column(String(3), default="PER")
    destino_detalle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    por_invitacion: Mapped[bool] = mapped_column(Boolean, default=False)
    institucion_invitante: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    viaticos_pagados_por: Mapped[ViaticosPagadosPor] = mapped_column(
        SAEnum(ViaticosPagadosPor, name="viaticos_pagados_por_enum"), default=ViaticosPagadosPor.PROPIA
    )

    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    objetivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False)
    dias_comision: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    resolucion_numero: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    resolucion_fecha: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    resolucion_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    viaticos_por_dia: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_viaticos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(String(3), default="PEN")
    rubros_asignados: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    administrador_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    contador_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    tesorero_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    cajero_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)

    plazo_rendicion_dias: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fecha_limite_rendicion: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Lugar exacto del destino (sesión 8)
    lugar_especifico: Mapped[Optional[str]] = mapped_column(String(500), nullable=True,
        comment="Ej: Auditorio Gobierno Regional de Cusco, Av. De la Cultura 734")
    lugar_latitud: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    lugar_longitud: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    lugar_radio_metros: Mapped[int] = mapped_column(SmallInteger, default=300,
        comment="Radio de tolerancia para validar asistencia en campo")

    # Comisión internacional (sesión 8)
    es_exterior: Mapped[bool] = mapped_column(Boolean, default=False)
    pais_destino_iso: Mapped[str] = mapped_column(String(3), default="PER")
    moneda_exterior: Mapped[Optional[str]] = mapped_column(String(3), nullable=True,
        comment="USD, EUR, BRL para comisiones internacionales")

    # Cobertura de invitación detallada (sesión 8)
    cobertura_invitacion: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True,
        comment='{"cubre":["pasajes_aereos","alojamiento"],"dias_cubiertos":null,"monto_maximo":null}')

    # Límites de DJ (sesión 8)
    dj_porcentaje_max: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True,
        comment="% máximo del total que puede ser DJ, ej: 30")
    dj_monto_dia_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True,
        comment="Monto máximo de DJ por día")

    estado: Mapped[EstadoComision] = mapped_column(
        SAEnum(EstadoComision, name="estado_comision_enum"), default=EstadoComision.AUTORIZADA, index=True
    )

    servidor: Mapped["Servidor"] = relationship("Servidor")
    gastos: Mapped[list["GastoComision"]] = relationship("GastoComision", back_populates="comision", cascade="all, delete-orphan")
    informe: Mapped[Optional["InformeComision"]] = relationship("InformeComision", back_populates="comision", uselist=False)
    saldo: Mapped[Optional["SaldoComision"]] = relationship("SaldoComision", uselist=False)


class GastoComision(Base, TimestampMixin):
    """Comprobante o gasto capturado durante la comisión."""
    __tablename__ = "rendipe_gasto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comision_id: Mapped[int] = mapped_column(Integer, ForeignKey("rendipe_comision.id"), nullable=False, index=True)
    servidor_id: Mapped[int] = mapped_column(Integer, ForeignKey("rendipe_servidor.id"), nullable=False)

    rubro: Mapped[str] = mapped_column(String(50), nullable=False)
    tipo_comprobante: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    serie: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    correlativo: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    fecha_emision: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ruc_emisor: Mapped[Optional[str]] = mapped_column(String(11), nullable=True)
    nombre_emisor: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    descripcion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(String(3), default="PEN")

    origen: Mapped[OrigenGasto] = mapped_column(
        SAEnum(OrigenGasto, name="origen_gasto_enum"), nullable=False
    )
    imagen_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    xml_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    estado_validacion: Mapped[EstadoValidacionGasto] = mapped_column(
        SAEnum(EstadoValidacionGasto, name="estado_validacion_gasto_enum"),
        default=EstadoValidacionGasto.PENDIENTE,
    )
    errores_validacion: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    latitud: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    longitud: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)

    aprobado_contador: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    observacion_contador: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    comprobante_alertape_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("comprobantes.id"), nullable=True
    )

    # Campos DJ — Declaración Jurada (sesión 8)
    dj_motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True,
        comment="Razón sin CE: zona rural, negocio no emite, extraviado, etc.")
    dj_establecimiento: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    dj_pdf_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Verificación de asistencia/presencia (sesión 8)
    asistencia_validada: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    asistencia_distancia_m: Mapped[Optional[int]] = mapped_column(Integer, nullable=True,
        comment="Distancia al lugar declarado en metros")
    asistencia_foto_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True,
        comment="Selfie del servidor en el lugar")
    asistencia_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Gasto en moneda extranjera (sesión 8)
    monto_moneda_ext: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    tipo_cambio_usado: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True,
        comment="TC BCRP al momento del gasto")

    comision: Mapped["Comision"] = relationship("Comision", back_populates="gastos")


class InformeComision(Base, TimestampMixin):
    """Informe de resultados de la comisión."""
    __tablename__ = "rendipe_informe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comision_id: Mapped[int] = mapped_column(Integer, ForeignKey("rendipe_comision.id"), unique=True, nullable=False)

    antecedentes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    objetivos: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actividades: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resultados: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conclusiones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recomendaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    estado: Mapped[EstadoInforme] = mapped_column(
        SAEnum(EstadoInforme, name="estado_informe_enum"), default=EstadoInforme.BORRADOR
    )
    generado_por_ia: Mapped[bool] = mapped_column(Boolean, default=False)
    pdf_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pdf_generado_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    comision: Mapped["Comision"] = relationship("Comision", back_populates="informe")


class SaldoComision(Base, TimestampMixin):
    """Saldo final de la comisión (devolución o a favor del servidor)."""
    __tablename__ = "rendipe_saldo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comision_id: Mapped[int] = mapped_column(Integer, ForeignKey("rendipe_comision.id"), unique=True, nullable=False)

    total_asignado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_gastado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_observado: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    saldo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    tipo_saldo: Mapped[TipoSaldo] = mapped_column(
        SAEnum(TipoSaldo, name="tipo_saldo_enum"), nullable=False
    )

    fecha_registro: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    numero_recibo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cajero_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    registrado_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
