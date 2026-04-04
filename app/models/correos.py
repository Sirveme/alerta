"""
correos.py — Modelo CorreoCapturado: correos recibidos de bancos para captura de pagos.

Decisiones técnicas:
- PK: Integer autoincremental.
- Soft delete: NO. Los correos procesados se marcan como tal y se mantienen para auditoría.
- Esta tabla registra los correos que llegan a ventas@reenviame.pe reenviados
  desde el email de notificaciones bancarias de cada empresa.
- El campo raw_body almacena el contenido completo para reprocesamiento.
- pago_generado_id: FK al pago que se creó al procesar este correo (si se pudo parsear).
"""

from typing import Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CorreoCapturado(Base, TimestampMixin):
    """
    Correo de notificación bancaria capturado por el sistema.
    Se recibe en ventas@reenviame.pe, reenviado desde el email
    de notificaciones del banco de cada empresa.
    """

    __tablename__ = "correos_capturados"
    __table_args__ = (
        Index("ix_correos_empresa_procesado", "empresa_id", "procesado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Empresa a la que pertenece este correo. Null si no se pudo identificar.",
    )

    # Datos del correo
    remitente: Mapped[str] = mapped_column(String(255), nullable=False)
    asunto: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    raw_body: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Contenido completo del correo para reprocesamiento.",
    )

    # Estado de procesamiento
    procesado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    banco_detectado: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="Banco identificado en el correo: bcp, bbva, interbank, etc.",
    )
    monto_detectado: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="Monto parseado del correo (como string para preservar formato original).",
    )

    # Pago generado al procesar este correo
    pago_generado_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("pagos.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Si hubo error de parseo
    error_parseo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    empresa: Mapped[Optional["EmpresaCliente"]] = relationship("EmpresaCliente")
    pago_generado: Mapped[Optional["Pago"]] = relationship("Pago")
