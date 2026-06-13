"""Servicio de autenticación."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models import Usuario


async def autenticar_usuario(
    db: AsyncSession,
    email: str,
    password: str,
) -> Optional[Usuario]:
    """Devuelve Usuario si email+password correctos y activo, None caso contrario."""
    result = await db.execute(
        select(Usuario).where(Usuario.email == email.lower().strip())
    )
    usuario = result.scalar_one_or_none()
    if not usuario or not usuario.activo:
        return None
    if not verify_password(password, usuario.password_hash):
        return None

    usuario.ultimo_acceso = datetime.now(timezone.utc)
    await db.commit()
    return usuario


def generar_tokens(usuario: Usuario) -> dict:
    """Genera access + refresh token para un usuario autenticado."""
    access = create_access_token(
        subject=str(usuario.id),
        rol=usuario.rol.value,
        extra_claims={"email": usuario.email, "nombre": usuario.nombre},
    )
    refresh = create_refresh_token(subject=str(usuario.id))
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}
