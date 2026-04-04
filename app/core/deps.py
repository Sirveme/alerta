"""
core/deps.py — Dependencias FastAPI: sesión de BD, autenticación, autorización.

Decisiones técnicas:
- Se usa AsyncSession con SQLAlchemy 2.0 para IO no bloqueante.
  Fallback a sesión síncrona si asyncpg no está disponible.
- get_current_user extrae el usuario del JWT en el header Authorization o cookie.
- get_empresa_activa lee empresa_activa_id del JWT o header X-Empresa-ID.
- require_rol es un dependency factory que valida el rol del usuario en el tenant actual.
"""

import os
if not os.environ.get('DATABASE_URL'):
    # Fallback para desarrollo local cuando .env no parsea bien
    from dotenv import dotenv_values
    vals = dotenv_values('.env', encoding='latin-1')
    for k, v in vals.items():
        if k and v and k not in os.environ:
            os.environ[k] = v

import functools
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.security import decode_token
from app.models.usuarios import Usuario, UsuarioTenant, RolUsuario
from app.models.empresas import EmpresaCliente

# --- Database session (síncrona para simplicidad inicial) ---
# Decisión: empezar síncrono con SQLAlchemy. Migrar a async cuando se necesite
# concurrencia real (WebSockets, muchas queries paralelas).
engine = create_engine(settings.DATABASE_URL_SYNC, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    """Dependency que provee una sesión de BD y la cierra al terminar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Auth ---


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Usuario:
    """
    Extrae el usuario actual del JWT.
    Prioridad: cookie access_token > header Authorization: Bearer <token>.
    """
    # 1. Intentar desde cookie primero
    token = request.cookies.get("access_token")

    # 2. Si no hay cookie, intentar desde header Authorization
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    usuario = db.execute(
        select(Usuario).where(Usuario.id == user_id, Usuario.activo == True, Usuario.deleted_at == None)
    ).scalar_one_or_none()

    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")

    # Inyectar datos del token en el request state para acceso rápido
    request.state.token_payload = payload
    return usuario


def get_empresa_activa(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Optional[EmpresaCliente]:
    """
    Obtiene la empresa activa del usuario.
    Prioridad: header X-Empresa-ID > JWT empresa_activa_id > usuario.empresa_activa_id.
    """
    empresa_id = None

    # 1. Header explícito (para cambios sin nuevo JWT)
    header_empresa = request.headers.get("X-Empresa-ID")
    if header_empresa:
        try:
            empresa_id = int(header_empresa)
        except ValueError:
            pass

    # 2. JWT
    if not empresa_id:
        payload = getattr(request.state, "token_payload", {})
        empresa_id = payload.get("empresa_activa_id")

    # 3. Campo del usuario
    if not empresa_id:
        empresa_id = current_user.empresa_activa_id

    if not empresa_id:
        return None

    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.id == empresa_id,
            EmpresaCliente.deleted_at == None,
        )
    ).scalar_one_or_none()

    return empresa


def require_rol(*roles: RolUsuario):
    """
    Dependency factory que valida que el usuario tenga uno de los roles
    especificados en el tenant actual.

    Uso:
        @router.get("/admin-only", dependencies=[Depends(require_rol(RolUsuario.ADMIN))])
    """
    def dependency(
        request: Request,
        current_user: Usuario = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        payload = getattr(request.state, "token_payload", {})
        tenant_id = payload.get("tenant_id")

        if not tenant_id:
            raise HTTPException(status_code=403, detail="Tenant no identificado en sesión")

        usuario_tenant = db.execute(
            select(UsuarioTenant).where(
                UsuarioTenant.usuario_id == current_user.id,
                UsuarioTenant.tenant_id == tenant_id,
                UsuarioTenant.activo == True,
                UsuarioTenant.deleted_at == None,
            )
        ).scalar_one_or_none()

        if not usuario_tenant or usuario_tenant.rol not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Rol insuficiente. Se requiere: {', '.join(r.value for r in roles)}",
            )

        return usuario_tenant

    return Depends(dependency)
