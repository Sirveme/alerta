"""FastAPI dependencies (current_user, require_role)."""
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import RolEnum, Usuario
from sqlalchemy import select


async def get_current_user(
    access_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> Optional[Usuario]:
    """Devuelve usuario autenticado o None."""
    if not access_token:
        return None
    payload = decode_token(access_token)
    if not payload or payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    result = await db.execute(select(Usuario).where(Usuario.id == int(user_id)))
    usuario = result.scalar_one_or_none()
    return usuario if usuario and usuario.activo else None


async def require_authenticated(
    usuario: Optional[Usuario] = Depends(get_current_user),
) -> Usuario:
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )
    return usuario


def require_role(*roles: RolEnum):
    """Factory de dependency. Uso: require_role(RolEnum.super_admin)."""
    async def checker(usuario: Usuario = Depends(require_authenticated)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permiso insuficiente",
            )
        return usuario
    return checker
