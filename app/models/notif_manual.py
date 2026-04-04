"""
models/notif_manual.py — Notificaciones manuales del contador a clientes.

Permite al contador enviar mensajes personalizados (informativos, urgentes,
felicitaciones, advertencias) a empresas cliente o usuarios específicos.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TipoNotifManual(str, enum.Enum):
    INFORMATIVO = "informativo"
    URGENTE = "urgente"
    FELICITACION = "felicitacion"
    ADVERTENCIA = "advertencia"


class CanalNotifManual(str, enum.Enum):
    PUSH = "push"
    WHATSAPP = "whatsapp"
    AMBOS = "ambos"


class EstadoNotifManual(str, enum.Enum):
    BORRADOR = "borrador"
    ENVIADA = "enviada"
    LEIDA = "leida"


class NotifManual(Base, TimestampMixin):
    """Notificación manual del contador hacia un cliente."""

    __tablename__ = "notif_manual"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    contador_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False)
    empresa_id: Mapped[int] = mapped_column(Integer, ForeignKey("empresas_cliente.id"), nullable=False)

    destinatario_tipo: Mapped[str] = mapped_column(String(20), nullable=False, default="empresa_cliente")
    destinatario_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    tipo: Mapped[TipoNotifManual] = mapped_column(
        SAEnum(TipoNotifManual, name="tipo_notif_manual_enum"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    adjunto_gcs: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    canal: Mapped[CanalNotifManual] = mapped_column(
        SAEnum(CanalNotifManual, name="canal_notif_manual_enum"), default=CanalNotifManual.PUSH
    )
    estado: Mapped[EstadoNotifManual] = mapped_column(
        SAEnum(EstadoNotifManual, name="estado_notif_manual_enum"), default=EstadoNotifManual.BORRADOR
    )

    enviada_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    leida_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    generada_por_agente: Mapped[bool] = mapped_column(Boolean, default=False)
    referencia_tabla: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    referencia_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relaciones
    contador: Mapped["Usuario"] = relationship("Usuario", foreign_keys=[contador_id])
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")
