"""Usuario base + Rol."""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class RolEnum(str, enum.Enum):
    super_admin = "super_admin"
    contador = "contador"
    asistente = "asistente"
    cliente_final = "cliente_final"  # reservado v2


class Usuario(Base, TimestampMixin):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    telefono: Mapped[Optional[str]] = mapped_column(String(20))

    rol: Mapped[RolEnum] = mapped_column(
        Enum(RolEnum, name="rol_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=RolEnum.contador,
    )

    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verificado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    ultimo_acceso: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # RUC propio del contador/estudio (para auto-detección de "Tu estudio")
    ruc_propio: Mapped[Optional[str]] = mapped_column(
        String(11),
        comment="RUC del propio estudio del contador (no cuenta para límite del plan)",
    )

    # Para asistentes: a qué contador pertenecen
    contador_padre_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"),
    )

    # Relationships
    contador_padre = relationship("Usuario", remote_side="Usuario.id", backref="asistentes")
    suscripcion = relationship("Suscripcion", back_populates="usuario", uselist=False)
    clientes_ruc = relationship("ClienteRuc", back_populates="contador", cascade="all, delete-orphan")
    configuracion_polling = relationship("ConfiguracionPolling", back_populates="usuario", uselist=False, cascade="all, delete-orphan")
    configuracion_ui = relationship("ConfiguracionUI", back_populates="usuario", uselist=False, cascade="all, delete-orphan")
