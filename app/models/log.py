"""Logs de polling y auditoría."""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LogPolling(Base):
    __tablename__ = "logs_polling"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_ruc_id: Mapped[int] = mapped_column(
        ForeignKey("clientes_ruc.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    exito: Mapped[bool] = mapped_column(Boolean, default=False)
    mensajes_nuevos: Mapped[int] = mapped_column(Integer, default=0)
    error_mensaje: Mapped[Optional[str]] = mapped_column(Text)


class LogAuditoria(Base):
    __tablename__ = "logs_auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    accion: Mapped[str] = mapped_column(String(100), nullable=False)
    detalle: Mapped[Optional[str]] = mapped_column(Text)
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
