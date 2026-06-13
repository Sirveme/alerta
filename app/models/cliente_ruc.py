"""ClienteRuc — empresa que el contador monitorea."""
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class ClienteRuc(Base, TimestampMixin):
    __tablename__ = "clientes_ruc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contador_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    ruc: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    razon_social: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_referencia: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Alias amigable (ej: 'Cliente VIP', 'Mi empresa')",
    )

    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    es_propio_estudio: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="True si es el RUC del propio contador/estudio (gratis, no cuenta para límite)",
    )

    contador = relationship("Usuario", back_populates="clientes_ruc")
    credencial = relationship(
        "CredencialSol",
        back_populates="cliente_ruc",
        uselist=False,
        cascade="all, delete-orphan",
    )
