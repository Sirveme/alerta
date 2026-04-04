"""
base.py — Clases base y mixins para todos los modelos de alerta.pe / notificado.pro.

Decisiones técnicas:
- Se usa DeclarativeBase de SQLAlchemy 2.0 con type_annotation_map para mapear
  tipos Python a tipos SQL de forma centralizada.
- TimestampMixin: agrega created_at y updated_at automáticos con timezone UTC.
  Todas las tablas lo heredan.
- SoftDeleteMixin: agrega deleted_at nullable. Solo las tablas que lo necesiten
  lo heredan (datos de negocio que no deben borrarse físicamente).
- Se define un type_annotation_map global para que Mapped[Decimal] mapee a
  Numeric(12,2) por defecto en campos monetarios.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    declared_attr,
)


class Base(DeclarativeBase):
    """Clase base declarativa para todos los modelos."""

    type_annotation_map = {
        # Campos monetarios: 12 dígitos, 2 decimales (hasta 9,999,999,999.99)
        Decimal: Numeric(12, 2),
        # Strings sin longitud explícita → VARCHAR(255) por defecto
        str: String(255),
    }


class TimestampMixin:
    """
    Mixin que agrega created_at y updated_at a cualquier modelo.
    - created_at: se fija al momento de INSERT (server_default).
    - updated_at: se actualiza en cada UPDATE (onupdate).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """
    Mixin para soft delete.
    - deleted_at = None → registro activo.
    - deleted_at = timestamp → registro eliminado lógicamente.
    Las queries deben filtrar WHERE deleted_at IS NULL por defecto.
    """

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,  # Índice para filtrar activos/eliminados eficientemente
    )
