"""
voz.py — Modelo ConsultaVoz: registro de consultas por voz al asistente IA.

Decisiones técnicas:
- PK: Integer autoincremental. Volumen alto (cada consulta de voz = 1 registro).
- Soft delete: NO. Son logs de interacción, se mantienen para analytics y mejora del modelo.
- Se guarda todo el pipeline: transcripción → intención → parámetros → query → respuesta.
  Esto permite auditar qué datos accedió el usuario por voz y mejorar el sistema.
- empresa_activa_id: la empresa que estaba activa al momento de la consulta.
  Crítico para reproducir el contexto de la consulta.
- query_sql: se guarda el SQL ejecutado para debugging y auditoría de seguridad
  (verificar que no se accedieron datos de otra empresa).
- tiempo_respuesta_ms: métrica de performance para optimización.
"""

import uuid
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ConsultaVoz(Base, TimestampMixin):
    """
    Registro de una consulta realizada por voz al asistente IA.
    Almacena el pipeline completo: audio → texto → intención → query → respuesta.
    """

    __tablename__ = "consultas_voz"
    __table_args__ = (
        Index("ix_consulta_voz_usuario_created", "usuario_id", "created_at"),
        Index("ix_consulta_voz_empresa", "empresa_activa_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    empresa_activa_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="SET NULL"),
        nullable=True,
        comment="Empresa activa al momento de la consulta (contexto de datos).",
    )

    # Pipeline de procesamiento
    transcripcion_original: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Texto transcrito del audio del usuario.",
    )
    intencion_detectada: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Intención clasificada: 'consultar_ventas', 'ver_alertas', etc.",
    )
    parametros_extraidos: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Parámetros extraídos: {periodo: '2025-03', tipo: 'ventas', empresa: 'X'}.",
    )
    query_sql: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Query SQL generado y ejecutado. Para auditoría y debugging.",
    )
    respuesta_entregada: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Respuesta en texto que se convirtió a voz para el usuario.",
    )

    # Métricas
    tiempo_respuesta_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Tiempo total de respuesta en milisegundos (audio → respuesta).",
    )

    # Si hubo error
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    usuario: Mapped["Usuario"] = relationship("Usuario")
    empresa_activa: Mapped[Optional["EmpresaCliente"]] = relationship("EmpresaCliente")
