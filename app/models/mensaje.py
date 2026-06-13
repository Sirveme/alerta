"""MensajeBuzon — metadata de mensajes SUNAT.

NOTA: queries de inserción se hacen en 13b (polling). Aquí solo el modelo.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class MensajeBuzon(Base, TimestampMixin):
    __tablename__ = "mensajes_buzon"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_ruc_id: Mapped[int] = mapped_column(
        ForeignKey("clientes_ruc.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Datos de SUNAT
    codigo_mensaje: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    cod_carpeta: Mapped[str] = mapped_column(String(10), nullable=False)
    nombre_carpeta: Mapped[str] = mapped_column(String(100))
    asunto: Mapped[str] = mapped_column(Text)
    fecha_envio_sunat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    tiene_adjunto: Mapped[bool] = mapped_column(Boolean, default=False)
    cantidad_adjuntos: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata de nuestro sistema
    fecha_detectado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visto: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fecha_primer_visto: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    archivado: Mapped[bool] = mapped_column(Boolean, default=False)
    importante: Mapped[bool] = mapped_column(Boolean, default=False)

    cliente_ruc = relationship("ClienteRuc")
