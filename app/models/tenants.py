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
from typing import Optional

from sqlalchemy import String, Text, Boolean, Enum as SAEnum
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
