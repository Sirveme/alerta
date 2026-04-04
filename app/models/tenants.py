"""
tenants.py — Modelo Tenant (organización/estudio contable) de alerta.pe / notificado.pro.

Decisiones técnicas:
- PK: UUID. Los tenants son pocos pero se referencian desde todas las tablas.
  UUID evita colisiones en migración de datos y no expone secuencialidad.
- Soft delete: SÍ. Un tenant desactivado no debe perder su historial.
- tipo_servicio: determina qué módulos están habilitados para este tenant.
  'alerta' = verificación de pagos/SUNAT, 'notificado' = cobranzas, 'ambos' = full.
- activo: flag rápido para deshabilitar acceso sin soft-delete (suspensión temporal).
- plan: tier de suscripción que controla límites de uso.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class TipoServicio(str, enum.Enum):
    """Qué módulos tiene habilitados este tenant."""
    ALERTA = "alerta"
    NOTIFICADO = "notificado"
    AMBOS = "ambos"


class PlanTenant(str, enum.Enum):
    """Tier de suscripción del tenant."""
    GRATIS = "gratis"
    BASICO = "basico"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Tenant(Base, TimestampMixin, SoftDeleteMixin):
    """
    Organización (estudio contable, empresa, academia) que agrupa usuarios y empresas cliente.
    Un contador independiente es un tenant con 1 usuario.
    """

    __tablename__ = "tenants"

    # PK UUID — no expone orden de creación, seguro para APIs públicas
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    ruc: Mapped[Optional[str]] = mapped_column(
        String(11), unique=True, nullable=True,
        comment="RUC del estudio/empresa titular. Nullable para personas naturales sin RUC.",
    )
    tipo_servicio: Mapped[TipoServicio] = mapped_column(
        SAEnum(TipoServicio, name="tipo_servicio_enum", create_constraint=True),
        nullable=False,
        default=TipoServicio.ALERTA,
    )
    plan: Mapped[PlanTenant] = mapped_column(
        SAEnum(PlanTenant, name="plan_tenant_enum", create_constraint=True),
        nullable=False,
        default=PlanTenant.GRATIS,
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    es_produccion: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="Protege tenants reales del reset de datos de prueba. True = tenant real (SOTE, etc).",
    )
    dominio_custom: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True,
        comment="Subdominio o dominio personalizado, ej: estudio-lopez.alerta.pe",
    )
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    usuarios_tenant: Mapped[list["UsuarioTenant"]] = relationship(
        "UsuarioTenant", back_populates="tenant", lazy="selectin",
    )
    empresas: Mapped[list["EmpresaCliente"]] = relationship(
        "EmpresaCliente", back_populates="tenant", lazy="selectin",
    )


class Invitacion(Base, TimestampMixin):
    """
    Token de invitacion para registro de nuevos tenants.
    Generado por SOTE, valido 72 horas. Se marca como usado al completar registro.
    """

    __tablename__ = "invitaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="Token unico de invitacion (URL-safe).",
    )
    whatsapp: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tipo_tenant: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="Tipo de tenant: estudio_contable, contador_independiente, empresa, etc.",
    )
    plan: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True,
        comment="Plan sugerido: gratis, basico, pro, enterprise.",
    )
    nombre_contacto: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    creado_por: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    expira_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Timestamp de expiracion (created_at + 72 horas).",
    )
    usado_en: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Timestamp cuando se completo el registro.",
    )
    tenant_creado_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
