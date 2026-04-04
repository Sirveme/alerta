"""
directorio.py — Directorio acumulativo de RUCs consultados.

Se construye automaticamente con cada comprobante procesado.
Primero se busca aqui; si no existe o tiene >30 dias, se consulta API SUNAT.
Incluye metricas de fiabilidad por proveedor.

Decisiones tecnicas:
- PK: Integer. Volumen alto de consultas por RUC, JOINs con comprobantes.
- Soft delete: NO. El directorio es acumulativo, no se borran entradas.
- Fiabilidad: calculada como comprobantes_validos / total_comprobantes * 100.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DirectorioRUC(Base, TimestampMixin):
    """
    Tabla acumulativa de RUCs consultados.
    Se construye automaticamente con cada comprobante procesado.
    Primero busca aqui, luego en API SUNAT si no existe.
    """

    __tablename__ = "directorio_ruc"
    __table_args__ = (
        Index("ix_directorio_ruc", "ruc"),
        Index("ix_directorio_razon", "razon_social"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, nullable=False, index=True)
    razon_social: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    nombre_comercial: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    estado_sunat: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="Estado en SUNAT: ACTIVO, BAJA, SUSPENSION TEMPORAL, etc.",
    )
    condicion: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="Condicion: HABIDO, NO HABIDO, NO HALLADO.",
    )
    tipo_contribuyente: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ubigeo: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    direccion: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    actividad_economica: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    ciiu: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Contacto (acumulado de comprobantes recibidos)
    email_contacto: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    whatsapp_contacto: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Metricas de fiabilidad
    total_comprobantes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comprobantes_validos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comprobantes_error: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fiabilidad_pct: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True,
        comment="100% = todos validos, 0% = todos con error.",
    )

    ultima_consulta_sunat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Si tiene mas de 30 dias, refrescar de API SUNAT.",
    )
