"""
documentos.py — Modelo ProductoNoDeducible: productos/servicios marcados como no deducibles.

Decisiones técnicas:
- Tabla separada (no JSONB) porque se necesitan queries eficientes:
  "¿este producto es no deducible para esta empresa?" en cada comprobante procesado.
- PK: Integer. Tabla de configuración con volumen bajo-medio.
- Soft delete: NO. Se elimina físicamente; es configuración, no dato histórico.
- Índice compuesto (empresa_id, palabra_clave) para búsqueda rápida.
"""

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ProductoNoDeducible(Base, TimestampMixin):
    """
    Producto o servicio marcado como no deducible para una empresa específica.
    Aparece en reportes en sección separada, nunca mezclado con gastos deducibles.
    Configurable por empresa: lo que es no deducible para una puede ser válido para otra.
    """

    __tablename__ = "productos_no_deducibles"
    __table_args__ = (
        Index("ix_prod_nodeducible_empresa_palabra", "empresa_id", "palabra_clave"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    palabra_clave: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Palabra clave para detectar el producto en descripciones de comprobantes.",
    )
    descripcion: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        comment="Descripción legible del tipo de gasto no deducible.",
    )
    categoria: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Categoría de agrupación: 'personal', 'recreación', 'educación', etc.",
    )

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship(
        "EmpresaCliente", back_populates="productos_no_deducibles",
    )
