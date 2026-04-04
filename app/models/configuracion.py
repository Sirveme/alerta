"""
configuracion.py — ConfigUsuario y ConfigEmpresa: configuración personalizada.

Decisiones técnicas:
- PK: Integer autoincremental. Tablas 1:1 con usuario/empresa.
- Soft delete: NO. La configuración se actualiza in-place, no se versiona.
- ConfigUsuario: preferencias de UI, notificaciones, IA y voz. Una fila por usuario.
- ConfigEmpresa: configuración tributaria y de negocio. Una fila por empresa.
- JSONB para listas de palabras clave y proveedores/clientes frecuentes:
  son datos semi-estructurados que varían por empresa y no se consultan con JOINs.
  Se leen completos cuando se carga la configuración.
"""

import enum
import uuid
from datetime import time
from typing import Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Time,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


# --- Enums ConfigUsuario ---

class TemaUI(str, enum.Enum):
    DARK = "dark"
    SEMI = "semi"
    FEMININE = "feminine"
    CLASSIC = "classic"


class FuenteSize(str, enum.Enum):
    SM = "sm"
    MD = "md"
    LG = "lg"


class CanalPreferido(str, enum.Enum):
    PUSH = "push"
    WHATSAPP = "whatsapp"
    AMBOS = "ambos"


class TonoIA(str, enum.Enum):
    FORMAL = "formal"
    DIRECTO = "directo"


class VelocidadVoz(str, enum.Enum):
    LENTA = "lenta"
    NORMAL = "normal"
    RAPIDA = "rapida"


# --- Enums ConfigEmpresa ---

class RegimenTributario(str, enum.Enum):
    RER = "RER"
    RMT = "RMT"
    GENERAL = "GENERAL"
    NRUS = "NRUS"
    RUS = "RUS"


class ConfigUsuario(Base, TimestampMixin):
    """
    Configuración de preferencias del usuario.
    Relación 1:1 con Usuario. Se crea con valores por defecto al registrar usuario.
    """

    __tablename__ = "config_usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Apariencia
    tema: Mapped[TemaUI] = mapped_column(
        SAEnum(TemaUI, name="tema_ui_enum", create_constraint=True),
        nullable=False,
        default=TemaUI.CLASSIC,
    )
    fuente_size: Mapped[FuenteSize] = mapped_column(
        SAEnum(FuenteSize, name="fuente_size_enum", create_constraint=True),
        nullable=False,
        default=FuenteSize.MD,
    )

    # Notificaciones
    canal_preferido: Mapped[CanalPreferido] = mapped_column(
        SAEnum(CanalPreferido, name="canal_preferido_enum", create_constraint=True),
        nullable=False,
        default=CanalPreferido.PUSH,
    )
    horario_no_molestar_inicio: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True,
        comment="Hora de inicio del horario 'no molestar'. Null = sin restricción.",
    )
    horario_no_molestar_fin: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True,
        comment="Hora de fin del horario 'no molestar'.",
    )

    # IA y voz
    tono_ia: Mapped[TonoIA] = mapped_column(
        SAEnum(TonoIA, name="tono_ia_enum", create_constraint=True),
        nullable=False,
        default=TonoIA.DIRECTO,
    )
    velocidad_voz: Mapped[VelocidadVoz] = mapped_column(
        SAEnum(VelocidadVoz, name="velocidad_voz_enum", create_constraint=True),
        nullable=False,
        default=VelocidadVoz.NORMAL,
    )

    # Empresa por defecto al iniciar sesión
    empresa_default_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="SET NULL"),
        nullable=True,
        comment="Empresa que se carga automáticamente al iniciar sesión.",
    )

    # --- Relaciones ---
    usuario: Mapped["Usuario"] = relationship("Usuario")
    empresa_default: Mapped[Optional["EmpresaCliente"]] = relationship("EmpresaCliente")


class ConfigEmpresa(Base, TimestampMixin):
    """
    Configuración tributaria y de negocio de una empresa.
    Relación 1:1 con EmpresaCliente. Se crea al registrar la empresa.
    """

    __tablename__ = "config_empresas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Datos tributarios
    regimen_tributario: Mapped[Optional[RegimenTributario]] = mapped_column(
        SAEnum(RegimenTributario, name="regimen_tributario_enum", create_constraint=True),
        nullable=True,
    )
    ciiu: Mapped[Optional[str]] = mapped_column(
        String(6), nullable=True,
        comment="Código CIIU de actividad económica.",
    )

    # Configuración de alertas
    umbral_alerta_monto: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True,
        comment="Monto a partir del cual se genera alerta automática.",
    )

    # Características de la empresa
    tiene_trabajadores: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exporta: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dia_cierre_mensual: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True,
        comment="Día del mes para cierre contable (1-31).",
    )

    # Listas configurables (JSONB) — se leen completas, no se consultan con JOINs
    # ["combustible", "gasolina", "peaje"]
    palabras_clave_deducibles: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Palabras clave para detectar gastos deducibles en comprobantes.",
    )
    # ["útiles escolares", "ropa"]
    palabras_clave_no_deducibles: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Palabras clave para detectar gastos NO deducibles.",
    )
    # [{"ruc": "20100047218", "nombre": "Distribuidora X", "categoria": "combustible"}]
    proveedores_frecuentes: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Proveedores frecuentes con RUC, nombre y categoría.",
    )
    # [{"ruc": "20512345678", "nombre": "Cliente Y"}]
    clientes_frecuentes: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Clientes frecuentes con RUC y nombre.",
    )

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship(
        "EmpresaCliente", back_populates="config_empresa",
    )
