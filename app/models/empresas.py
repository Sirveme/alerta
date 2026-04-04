"""
empresas.py — Modelo EmpresaCliente: las empresas que un tenant gestiona.

Decisiones técnicas:
- PK: Integer autoincremental. Las empresas se consultan masivamente en JOINs
  con pagos/comprobantes; Integer es más eficiente que UUID para índices B-tree
  y JOINs frecuentes. El ID no se expone directamente al usuario (se usa RUC).
- Soft delete: SÍ. Desactivar una empresa no debe perder su historial.
- JSONB vs tablas normalizadas:
  * cuentas_bancarias → JSONB. Son pocos registros por empresa (2-5 cuentas),
    no se consultan con JOINs, y la estructura varía por banco. JSONB es ideal.
  * numeros_yape_plin → JSONB. Mismo razonamiento: 1-3 números, estructura simple.
  * credenciales_sol → bytea (LargeBinary). Encriptado a nivel de aplicación.
    No usar pgcrypto para evitar dependencia de extensión y mantener las claves
    de cifrado fuera de la BD.
- email_notificaciones_bancarias: el email que reenvía notificaciones de bancos
  a ventas@reenviame.pe para captura automática de pagos.
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class EmpresaCliente(Base, TimestampMixin, SoftDeleteMixin):
    """
    Empresa cliente gestionada por un tenant.
    Un estudio contable (tenant) puede tener N empresas cliente.
    """

    __tablename__ = "empresas_cliente"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ruc", name="uq_tenant_ruc"),
    )

    # PK Integer — alto volumen de JOINs con pagos/comprobantes
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Datos tributarios
    ruc: Mapped[str] = mapped_column(
        String(11), nullable=False, index=True,
        comment="RUC de 11 dígitos de la empresa cliente.",
    )
    razon_social: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_comercial: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # --- Cuentas bancarias (JSONB) ---
    # Estructura esperada: [{"banco": "bcp", "tipo": "corriente", "moneda": "PEN",
    #   "numero": "123-456-789", "cci": "002-123-456-789-00"}]
    cuentas_bancarias: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Array de cuentas bancarias. Estructura: [{banco, tipo, moneda, numero, cci}]",
    )

    # --- Números Yape/Plin (JSONB) ---
    # Estructura esperada: [{"tipo": "yape", "numero": "999888777", "titular": "Juan Pérez"}]
    numeros_yape_plin: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Array de números Yape/Plin. Estructura: [{tipo, numero, titular}]",
    )

    # --- Credenciales SOL secundario ---
    # Encriptado a nivel de aplicación (AES-256 o similar), no pgcrypto.
    # Se almacena como bytea para máxima flexibilidad.
    clave_sol_usuario: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True,
        comment="Usuario SOL secundario, encriptado a nivel de app.",
    )
    clave_sol_password: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True,
        comment="Clave SOL secundaria, encriptada a nivel de app.",
    )

    # Email que reenvía notificaciones bancarias
    email_notificaciones_bancarias: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        comment="Email que reenvía a ventas@reenviame.pe para captura automática de pagos.",
    )

    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="empresas")
    pagos: Mapped[list["Pago"]] = relationship(
        "Pago", back_populates="empresa", lazy="dynamic",
    )
    comprobantes: Mapped[list["Comprobante"]] = relationship(
        "Comprobante", back_populates="empresa", lazy="dynamic",
    )
    config_empresa: Mapped[Optional["ConfigEmpresa"]] = relationship(
        "ConfigEmpresa", back_populates="empresa", uselist=False,
    )
    productos_no_deducibles: Mapped[list["ProductoNoDeducible"]] = relationship(
        "ProductoNoDeducible", back_populates="empresa", lazy="selectin",
    )
    deudas: Mapped[list["Deuda"]] = relationship(
        "Deuda", back_populates="empresa", lazy="dynamic",
    )
