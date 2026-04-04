"""
auditoria.py — Modelo RegistroAuditoria: log inmutable de cambios sensibles.

Decisiones técnicas:
- PK: Integer autoincremental (BigInteger para volumen extremo a futuro).
  Los logs de auditoría crecen indefinidamente.
- Soft delete: NO. Los registros de auditoría son INMUTABLES. No se modifican ni eliminan.
- JSONB para valor_anterior y valor_nuevo: permite almacenar cualquier estructura
  de datos sin conocer el schema de la tabla auditada. Ideal para auditoría genérica.
- Particionamiento: candidata a partición por rango de fecha (created_at) cuando
  supere ~10M de registros. Por ahora no se particiona.
- Índices: por tabla+registro (para ver historial de un registro) y por usuario
  (para ver actividad de un usuario).
- IP y user_agent: para rastreo de sesiones sospechosas.
"""

import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class RegistroAuditoria(Base, TimestampMixin):
    """
    Log inmutable de todo cambio sensible en el sistema.
    Registra: quién, qué, cuándo, dónde, valor anterior y nuevo.
    NO se modifica ni elimina — es append-only.
    """

    __tablename__ = "registros_auditoria"
    __table_args__ = (
        # Historial de un registro específico
        Index("ix_auditoria_tabla_registro", "tabla", "registro_id"),
        # Actividad de un usuario
        Index("ix_auditoria_usuario", "usuario_id"),
        # Búsqueda por acción
        Index("ix_auditoria_accion", "accion"),
    )

    # BigInteger para soportar volumen alto a largo plazo
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    usuario_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
        comment="Usuario que realizó la acción. Null para acciones del sistema.",
    )

    # Qué se hizo
    accion: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Acción realizada: 'crear', 'actualizar', 'eliminar', 'login', 'cambio_empresa', etc.",
    )

    # Sobre qué tabla/registro
    tabla: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Nombre de la tabla afectada.",
    )
    registro_id: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="ID del registro afectado (como string para soportar UUID e Integer).",
    )

    # Valores antes y después del cambio
    valor_anterior: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Estado del registro ANTES del cambio. Null en creación.",
    )
    valor_nuevo: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Estado del registro DESPUÉS del cambio. Null en eliminación.",
    )

    # Contexto de la sesión
    ip: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True,
        comment="IP del cliente (IPv4 o IPv6).",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True,
        comment="User-Agent del navegador/app.",
    )

    # Descripción legible (opcional)
    descripcion: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Descripción legible de la acción, ej: 'Cambió estado de pago #123 a cruzado'.",
    )

    # --- Relaciones ---
    usuario: Mapped[Optional["Usuario"]] = relationship("Usuario")
