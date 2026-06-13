"""Plan + Suscripción."""
import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class PlanCodigo(str, enum.Enum):
    trial = "trial"
    basico = "basico"
    intermedio = "intermedio"
    avanzado = "avanzado"
    ilimitado = "ilimitado"  # super admin


class Plan(Base, TimestampMixin):
    __tablename__ = "planes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[PlanCodigo] = mapped_column(
        Enum(PlanCodigo, name="plan_codigo_enum", values_callable=lambda x: [e.value for e in x]),
        unique=True,
        nullable=False,
    )
    nombre: Mapped[str] = mapped_column(String(50), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(500), default="")
    precio_mensual: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    max_clientes_ruc: Mapped[int] = mapped_column(Integer, default=1)
    max_horarios_polling: Mapped[int] = mapped_column(Integer, default=3)
    incluye_panel_estudio: Mapped[bool] = mapped_column(Boolean, default=False)
    polling_interdiario: Mapped[bool] = mapped_column(Boolean, default=True)  # False = diario
    polling_cada_hora: Mapped[bool] = mapped_column(Boolean, default=False)
    dias_trial: Mapped[int] = mapped_column(Integer, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Suscripcion(Base, TimestampMixin):
    __tablename__ = "suscripciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("planes.id"), nullable=False)
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_vencimiento: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    activa: Mapped[bool] = mapped_column(Boolean, default=True)

    usuario = relationship("Usuario", back_populates="suscripcion")
    plan = relationship("Plan")
