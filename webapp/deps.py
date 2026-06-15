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

from models import RolUsuario
from .auth import COOKIE_NOMBRE, leer_sesion


class RedirigirALogin(Exception):
    """Se lanza cuando no hay sesión; el handler global redirige a /login."""


@dataclass
class UsuarioActual:
    id: uuid.UUID
    estudio_id: uuid.UUID
    rol: RolUsuario
    nombre: str

    @property
    def es_admin(self) -> bool:
        return self.rol == RolUsuario.ADMIN

    @property
    def solo_lectura(self) -> bool:
        """Asistente = solo lectura (zAlerta-01 B.1 RBAC)."""
        return self.rol == RolUsuario.ASISTENTE


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
        )
    except (KeyError, ValueError):
        raise RedirigirALogin()


def requiere_escritura(user: UsuarioActual = Depends(usuario_actual)) -> UsuarioActual:
    """Bloquea a los asistentes (solo lectura) en operaciones de escritura."""
    if user.solo_lectura:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Tu rol es de solo lectura.")
    return user
