"""
models/portal.py — Modelos para el portal público reenviame.pe.

Separados del core para claridad — pero comparten la misma BD.
EnvioPortal: cada envío hecho por el portal (UUID como PK, expuesto en URLs).
EstadoSistema: métricas públicas actualizadas cada 5 min.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CanalEnvio(str, enum.Enum):
    PORTAL_WEB = "portal_web"
    CORREO = "correo"
    API = "api"


class TipoArchivo(str, enum.Enum):
    XML = "xml"
    PDF = "pdf"
    IMAGEN = "imagen"


class EstadoValidacionPortal(str, enum.Enum):
    PENDIENTE = "pendiente"
    VALIDO = "valido"
    OBSERVADO = "observado"
    BLOQUEADO = "bloqueado"
    ERROR_SUNAT = "error_sunat"


class EnvioPortal(Base, TimestampMixin):
    """
    Registro de cada envío hecho por el portal público.
    UUID como PK: se expone en URLs públicas — no revelar secuencialidad.
    """
    __tablename__ = "envio_portal"
    __table_args__ = (
        Index("ix_envio_ruc_emisor_receptor", "ruc_emisor", "ruc_receptor"),
        Index("ix_envio_serie_correlativo", "serie", "correlativo", "ruc_emisor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ruc_emisor: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    nombre_emisor: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    ruc_receptor: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    nombre_receptor: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # Archivo recibido
    canal_envio: Mapped[CanalEnvio] = mapped_column(
        SAEnum(CanalEnvio, name="canal_envio_portal_enum"), default=CanalEnvio.PORTAL_WEB
    )
    tipo_archivo: Mapped[TipoArchivo] = mapped_column(
        SAEnum(TipoArchivo, name="tipo_archivo_portal_enum"), nullable=False
    )
    archivo_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    xml_original: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Datos del comprobante extraídos
    tipo_comprobante: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    serie: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    correlativo: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    fecha_emision: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    moneda: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    total: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)

    # Resultado de validación
    estado_validacion: Mapped[EstadoValidacionPortal] = mapped_column(
        SAEnum(EstadoValidacionPortal, name="estado_validacion_portal_enum"),
        default=EstadoValidacionPortal.PENDIENTE, index=True,
    )
    errores_validacion: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    validado_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    respuesta_sunat: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Asociación con empresa interna
    empresa_cliente_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("empresas_cliente.id"), nullable=True
    )
    comprobante_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("comprobantes.id"), nullable=True
    )

    # Acuse de recepción
    acuse_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )
    acuse_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    acuse_generado: Mapped[bool] = mapped_column(Boolean, default=False)

    # Metadata del envío
    ip_origen: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    email_notif: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class EstadoSistema(Base, TimestampMixin):
    """Métricas del sistema para /estado. Se actualiza cada 5 min via Celery."""
    __tablename__ = "estado_sistema"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sunat_disponible: Mapped[bool] = mapped_column(Boolean, default=True)
    sunat_tiempo_respuesta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="ms")
    envios_hoy: Mapped[int] = mapped_column(Integer, default=0)
    validaciones_exitosas_hoy: Mapped[int] = mapped_column(Integer, default=0)
    uptime_porcentaje: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    incidencias_activas: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
