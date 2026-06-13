"""Configuración por usuario (polling y UI)."""
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class ConfiguracionPolling(Base, TimestampMixin):
    __tablename__ = "configuraciones_polling"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # JSON con horarios: ["08:00", "12:00", "16:00"]
    horarios: Mapped[list] = mapped_column(JSON, default=lambda: ["08:00", "12:00", "16:00"])

    # "interdiario" (lun-mie-vie) o "diario" o "personalizado"
    tipo_dias: Mapped[str] = mapped_column(String(20), default="interdiario")

    # Para "personalizado": JSON ["lunes", "martes", ...]
    dias_personalizados: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    usuario = relationship("Usuario", back_populates="configuracion_polling")


class ConfiguracionUI(Base, TimestampMixin):
    __tablename__ = "configuraciones_ui"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    tema: Mapped[str] = mapped_column(String(20), default="vino-ambar")
    modo: Mapped[str] = mapped_column(String(10), default="oscuro")  # "oscuro" | "claro"
    idioma: Mapped[str] = mapped_column(String(5), default="es")

    usuario = relationship("Usuario", back_populates="configuracion_ui")
