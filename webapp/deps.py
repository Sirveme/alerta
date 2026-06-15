"""
webapp/deps.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Dependencias FastAPI: usuario actual desde la cookie firmada, filtro
multi-tenant (estudio_id) y RBAC.

REGLA CRÍTICA (zAlerta-01 transversal): ninguna query sin filtro estudio_id.
`UsuarioActual.estudio_id` es la única fuente de verdad del tenant; los
routers DEBEN usarlo en cada consulta.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from models import RolUsuario, TipoCuenta, Contribuyente
from .auth import COOKIE_NOMBRE, leer_sesion


class RedirigirALogin(Exception):
    """Se lanza cuando no hay sesión; el handler global redirige a /login."""


@dataclass
class UsuarioActual:
    id: uuid.UUID
    estudio_id: uuid.UUID
    rol: RolUsuario
    nombre: str
    tipo_cuenta: str = TipoCuenta.ESTUDIO.value

    @property
    def es_admin(self) -> bool:
        return self.rol == RolUsuario.ADMIN

    @property
    def es_empresario(self) -> bool:
        """Cuenta tipo empresario: ve SOLO su propio RUC, solo lectura."""
        return self.tipo_cuenta == TipoCuenta.EMPRESARIO.value

    @property
    def solo_lectura(self) -> bool:
        """Asistente o empresario = solo lectura (zAlerta-01 B.1 / zAlerta-06)."""
        return self.rol == RolUsuario.ASISTENTE or self.es_empresario


def usuario_actual(request: Request) -> UsuarioActual:
    """Dependencia: devuelve el usuario logueado o redirige a /login."""
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        raise RedirigirALogin()
    try:
        return UsuarioActual(
            id=uuid.UUID(sesion["uid"]),
            estudio_id=uuid.UUID(sesion["eid"]),
            rol=RolUsuario(sesion["rol"]),
            nombre=sesion.get("nombre", ""),
            tipo_cuenta=sesion.get("tc", TipoCuenta.ESTUDIO.value),
        )
    except (KeyError, ValueError):
        raise RedirigirALogin()


async def contribuyente_accesible(session, user: "UsuarioActual",
                                  contribuyente_id: uuid.UUID):
    """Devuelve el Contribuyente si el usuario puede verlo, o None.

    - Estudio: contribuyentes de su propio estudio_id (multi-tenant clásico).
    - Empresario: SOLO el RUC vinculado a su cuenta (cuenta_empresario_id).
    """
    if user.es_empresario:
        cond = Contribuyente.cuenta_empresario_id == user.estudio_id
    else:
        cond = Contribuyente.estudio_id == user.estudio_id
    return await session.scalar(
        select(Contribuyente).where(Contribuyente.id == contribuyente_id, cond))


def requiere_escritura(user: UsuarioActual = Depends(usuario_actual)) -> UsuarioActual:
    """Bloquea a los asistentes (solo lectura) en operaciones de escritura."""
    if user.solo_lectura:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Tu rol es de solo lectura.")
    return user
