"""
notificaciones.py — Modelo Notificacion: notificaciones enviadas a usuarios.

Decisiones técnicas:
- PK: Integer autoincremental. Volumen alto, se consultan por usuario y fecha.
- Soft delete: NO. Las notificaciones se archivan, no se eliminan lógicamente.
  Estado 'leida' cumple esa función.
- Canal: push, whatsapp, sms, email.
- Nivel: urgente (🔴), importante (🟡), info (🔵).
- Estado: pendiente → enviada → leida. Si falla: fallida.
- Se indexa por (usuario_id, estado) para badge de no leídas y por
  (usuario_id, created_at) para timeline.
"""

import enum
import uuid
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CanalNotificacion(str, enum.Enum):
    """Canal de envío de la notificación."""
    PUSH = "push"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class EstadoNotificacion(str, enum.Enum):
    """Estado de la notificación."""
    PENDIENTE = "pendiente"
    ENVIADA = "enviada"
    LEIDA = "leida"
    FALLIDA = "fallida"


class NivelAlertaNotificacion(str, enum.Enum):
    """Nivel visual/prioridad de la notificación."""
    URGENTE = "urgente"       # 🔴
    IMPORTANTE = "importante"  # 🟡
    INFO = "info"             # 🔵


class Notificacion(Base, TimestampMixin):
    """
    Notificación enviada a un usuario por cualquier canal.
    Puede originarse por alerta SUNAT, vencimiento de deuda, pago recibido, etc.
    """

    __tablename__ = "notificaciones"
    __table_args__ = (
        Index("ix_notif_usuario_estado", "usuario_id", "estado"),
        Index("ix_notif_usuario_created", "usuario_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    empresa_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="SET NULL"),
        nullable=True,
        comment="Empresa relacionada (si aplica). Nullable para notificaciones generales.",
    )

    canal: Mapped[CanalNotificacion] = mapped_column(
        SAEnum(CanalNotificacion, name="canal_notificacion_enum", create_constraint=True),
        nullable=False,
    )
    estado: Mapped[EstadoNotificacion] = mapped_column(
        SAEnum(EstadoNotificacion, name="estado_notificacion_enum", create_constraint=True),
        nullable=False,
        default=EstadoNotificacion.PENDIENTE,
    )
    nivel: Mapped[NivelAlertaNotificacion] = mapped_column(
        SAEnum(NivelAlertaNotificacion, name="nivel_alerta_notif_enum", create_constraint=True),
        nullable=False,
        default=NivelAlertaNotificacion.INFO,
    )

    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)

    # ID externo del proveedor de envío (Twilio, Firebase, etc.)
    proveedor_id_externo: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="ID de tracking del proveedor: message_sid de Twilio, etc.",
    )

    # Error si falló
    error_detalle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    usuario: Mapped["Usuario"] = relationship("Usuario")
    empresa: Mapped[Optional["EmpresaCliente"]] = relationship("EmpresaCliente")
