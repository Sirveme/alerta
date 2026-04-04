"""
alertas.py — Modelo Alerta: alertas SUNAT/SUNAFIL y reglas de negocio.

Decisiones técnicas:
- PK: Integer autoincremental. Volumen moderado-alto.
- Soft delete: SÍ. Las alertas resueltas se mantienen para auditoría y reportes.
- Origen: SUNAT, SUNAFIL, sistema (generada por regla automática), manual.
- Estado: activa → en_revision → resuelta. Si no aplica: descartada.
- Se vincula opcionalmente a un comprobante o pago que la originó.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class OrigenAlerta(str, enum.Enum):
    """De dónde proviene la alerta."""
    SUNAT = "sunat"
    SUNAFIL = "sunafil"
    SISTEMA = "sistema"    # Generada por regla automática
    MANUAL = "manual"      # Creada manualmente por un usuario


class EstadoAlerta(str, enum.Enum):
    """Estado del ciclo de vida de la alerta."""
    ACTIVA = "activa"
    EN_REVISION = "en_revision"
    RESUELTA = "resuelta"
    DESCARTADA = "descartada"


class Alerta(Base, TimestampMixin, SoftDeleteMixin):
    """
    Alerta generada por detección de inconsistencia tributaria,
    notificación SUNAT/SUNAFIL, o regla de negocio.
    """

    __tablename__ = "alertas"
    __table_args__ = (
        Index("ix_alertas_empresa_estado", "empresa_id", "estado"),
        Index("ix_alertas_empresa_created", "empresa_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    origen: Mapped[OrigenAlerta] = mapped_column(
        SAEnum(OrigenAlerta, name="origen_alerta_enum", create_constraint=True),
        nullable=False,
    )
    estado: Mapped[EstadoAlerta] = mapped_column(
        SAEnum(EstadoAlerta, name="estado_alerta_enum", create_constraint=True),
        nullable=False,
        default=EstadoAlerta.ACTIVA,
    )

    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)

    # Código de la alerta SUNAT/SUNAFIL (si aplica)
    codigo_entidad: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="Código de alerta SUNAT/SUNAFIL, ej: 'SUNAT-0263-2025'.",
    )

    # Referencia opcional al registro que originó la alerta
    comprobante_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("comprobantes.id", ondelete="SET NULL"),
        nullable=True,
    )
    pago_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("pagos.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Quién resolvió/descartó la alerta
    resuelto_por_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    fecha_resolucion: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    nota_resolucion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Datos adicionales de la alerta (varía por origen)
    metadata_alerta: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Datos extra según origen: detalle SUNAT, regla que disparó, etc.",
    )

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship("EmpresaCliente")
    comprobante: Mapped[Optional["Comprobante"]] = relationship("Comprobante")
    pago: Mapped[Optional["Pago"]] = relationship("Pago")
    resuelto_por: Mapped[Optional["Usuario"]] = relationship("Usuario")
