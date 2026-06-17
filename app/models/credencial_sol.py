"""CredencialSol — credenciales SOL encriptadas con Fernet."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class CredencialSol(Base, TimestampMixin):
    __tablename__ = "credenciales_sol"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_ruc_id: Mapped[int] = mapped_column(
        ForeignKey("clientes_ruc.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # tipo_usuario: 1=DNI titular, 2=RUC + Usuario SOL alfanumérico
    tipo_usuario: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Campos encriptados con Fernet (Texts largos por seguridad)
    dni_encriptado: Mapped[Optional[str]] = mapped_column(Text)
    usuario_sol_encriptado: Mapped[Optional[str]] = mapped_column(Text)
    clave_sol_encriptada: Mapped[str] = mapped_column(Text, nullable=False)

    # Estado de la última verificación
    ultima_verificacion: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ultima_verificacion_exitosa: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ultimo_error: Mapped[Optional[str]] = mapped_column(String(500))

    cliente_ruc = relationship("ClienteRuc", back_populates="credencial")
