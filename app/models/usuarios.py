"""
usuarios.py — Modelos de usuario, membresía en tenant, WebAuthn y recuperación de clave.

Decisiones técnicas:
- PK Usuario: UUID. Se referencia desde muchas tablas (auditoría, pagos, etc.) y
  no debe exponer secuencialidad.
- DNI como username (8 dígitos, UNIQUE). Es el identificador de negocio.
- Sin correo obligatorio: muchos usuarios peruanos no usan email como canal principal.
- Tabla pivote UsuarioTenant: relación N:N entre usuarios y tenants con rol por tenant.
  PK Integer autoincremental (tabla de relación interna, no expuesta en API).
- empresa_activa_id: FK nullable que indica qué empresa está operando el usuario en sesión.
  Cambia sin logout (requisito de negocio para contadores multi-empresa).
- RecuperacionClave: mecanismo de recuperación por DNI secundario (familiar) +
  fecha de nacimiento del titular del DNI secundario. Sin email reset.
- WebAuthnCredential: soporte para biometría (huella, Face ID). Credential_id y
  public_key almacenados como LargeBinary.
- Soft delete: SÍ en Usuario (datos de negocio) y UsuarioTenant (desvinculación lógica).
  NO en WebAuthnCredential ni RecuperacionClave (se borran físicamente al desactivar).
"""

import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class RolUsuario(str, enum.Enum):
    """Roles de un usuario dentro de un tenant."""
    ADMIN = "admin"
    CONTADOR = "contador"
    ASISTENTE = "asistente"
    SOLO_LECTURA = "solo_lectura"


class Usuario(Base, TimestampMixin, SoftDeleteMixin):
    """
    Usuario de la plataforma. Se identifica con DNI peruano (8 dígitos).
    Puede pertenecer a múltiples tenants con diferentes roles.
    """

    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # DNI peruano = username
    dni: Mapped[str] = mapped_column(
        String(8), unique=True, nullable=False, index=True,
        comment="DNI peruano de 8 dígitos. Es el username de login.",
    )
    nombres: Mapped[str] = mapped_column(String(150), nullable=False)
    apellidos: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Datos opcionales
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(
        String(15), nullable=True,
        comment="Número celular con código de país, ej: +51999888777",
    )
    fecha_nacimiento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Empresa activa en sesión — cambia sin logout
    empresa_activa_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="SET NULL"),
        nullable=True,
        comment="Empresa que el usuario está operando actualmente. Cambia sin logout.",
    )

    # Último acceso (para auditoría y métricas de uso)
    ultimo_acceso: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="Timestamp del último login exitoso.",
    )

    # --- Relaciones ---
    tenants_usuario: Mapped[list["UsuarioTenant"]] = relationship(
        "UsuarioTenant", back_populates="usuario", lazy="selectin",
    )
    empresa_activa: Mapped[Optional["EmpresaCliente"]] = relationship(
        "EmpresaCliente", foreign_keys=[empresa_activa_id], lazy="joined",
    )
    credenciales_webauthn: Mapped[list["WebAuthnCredential"]] = relationship(
        "WebAuthnCredential", back_populates="usuario", cascade="all, delete-orphan",
    )
    recuperaciones_clave: Mapped[list["RecuperacionClave"]] = relationship(
        "RecuperacionClave", back_populates="usuario", cascade="all, delete-orphan",
    )


class UsuarioTenant(Base, TimestampMixin, SoftDeleteMixin):
    """
    Tabla pivote N:N entre Usuario y Tenant, con rol específico por tenant.
    Ejemplo: un asistente que trabaja para dos estudios contables tiene 2 registros aquí.
    """

    __tablename__ = "usuarios_tenants"
    __table_args__ = (
        UniqueConstraint("usuario_id", "tenant_id", name="uq_usuario_tenant"),
    )

    # PK Integer — tabla interna de relación, no expuesta en API
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rol: Mapped[RolUsuario] = mapped_column(
        SAEnum(RolUsuario, name="rol_usuario_enum", create_constraint=True),
        nullable=False,
        default=RolUsuario.SOLO_LECTURA,
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Relaciones ---
    usuario: Mapped["Usuario"] = relationship("Usuario", back_populates="tenants_usuario")
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="usuarios_tenant")


class WebAuthnCredential(Base, TimestampMixin):
    """
    Credenciales WebAuthn (biometría: huella digital, Face ID) por usuario.
    Un usuario puede tener N dispositivos registrados.
    No tiene soft delete — se elimina físicamente al revocar un dispositivo.
    """

    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nombre descriptivo del dispositivo, ej: "iPhone de Juan"
    nombre_dispositivo: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Datos WebAuthn — binarios opacos almacenados tal cual
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Relaciones ---
    usuario: Mapped["Usuario"] = relationship("Usuario", back_populates="credenciales_webauthn")


class RecuperacionClave(Base, TimestampMixin):
    """
    Mecanismo de recuperación de contraseña SIN email.
    El usuario registra un DNI secundario (familiar) y la fecha de nacimiento
    del titular de ese DNI. Para resetear, debe proporcionar ambos datos.
    No tiene soft delete — se elimina físicamente al cambiar método de recuperación.
    """

    __tablename__ = "recuperaciones_clave"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # DNI del familiar (8 dígitos)
    dni_secundario: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="DNI del familiar que sirve como respaldo para reset de clave.",
    )

    # Fecha de nacimiento del titular del DNI secundario
    fecha_nacimiento_secundario: Mapped[date] = mapped_column(
        Date, nullable=False,
        comment="Fecha de nacimiento del titular del DNI secundario (dato de verificación).",
    )

    # Relación con titular del DNI secundario (descripción, no FK)
    parentesco: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="Relación con el familiar: padre, madre, hermano, cónyuge, etc.",
    )

    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Relaciones ---
    usuario: Mapped["Usuario"] = relationship("Usuario", back_populates="recuperaciones_clave")
