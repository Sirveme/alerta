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
from sqlalchemy import select, or_, false

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
    # ── Acceso Institucional (zAlerta-60, Fase 3) ──
    persona_id: uuid.UUID | None = None       # login por persona (nuevo); None = login viejo
    rol_sistema: str | None = None            # 'SOPORTE_GLOBAL' → ve todos los buzones
    tiene_usuario: bool = True                # ¿hay fila en `usuarios`? (para FKs usuario_id)
    solo_lectura_ctx: bool = False            # es_solo_lectura del acceso activo
    multi_contexto: bool = False              # tiene >1 buzón → mostrar "cambiar buzón"
    cargo: str | None = None                  # cargo del acceso activo (DECANO, DUENO, …)

    @property
    def es_admin(self) -> bool:
        return self.rol == RolUsuario.ADMIN

    @property
    def es_empresario(self) -> bool:
        """Cuenta tipo empresario: ve SOLO su propio RUC, solo lectura."""
        return self.tipo_cuenta == TipoCuenta.EMPRESARIO.value

    @property
    def es_soporte_global(self) -> bool:
        """SOPORTE_GLOBAL (zAlerta-60): ve todos los buzones, solo lectura, auditado."""
        return self.rol_sistema == "SOPORTE_GLOBAL"

    @property
    def solo_lectura(self) -> bool:
        """Asistente, empresario o acceso marcado es_solo_lectura = solo lectura."""
        return (self.rol == RolUsuario.ASISTENTE or self.es_empresario
                or self.solo_lectura_ctx)

    @property
    def rol_tema(self) -> str:
        """Rol de DISEÑO (zAlerta-75) → set de tokens (acento + radio) por
        data-rol. institucion=ángulos rectos ámbar; empresario=redondeado azul;
        contador=verde; asistente=violeta; soporte=gris."""
        if self.es_soporte_global:
            return "soporte"
        # Cargo institucional (directivos de una institución: CCPL, colegios…).
        if self.cargo in ("DECANO", "DIRECTOR", "ADMINISTRADOR", "CONTADOR"):
            return "institucion"
        if self.es_empresario:
            return "empresario"
        if self.rol == RolUsuario.ASISTENTE:
            return "asistente"
        return "contador"

    def autoria(self) -> dict:
        """Columnas de identidad (usuario_id/persona_id) para INSERTs en tablas
        con columnas hermanas (reacciones, recordatorios, push_suscripciones).
        Login por DNI → persona_id (NO hay fila en `usuarios`, la FK reventaría);
        login legacy → usuario_id. Migración usuarios→personas (zAlerta-67).
        Se usa como Reaccion(..., **user.autoria())."""
        if self.persona_id:
            return {"usuario_id": None, "persona_id": self.persona_id}
        return {"usuario_id": self.id, "persona_id": None}

    def cargo_trazabilidad(self) -> dict:
        """Igual que autoria() pero con los nombres de columna de credenciales_sol
        (quien_cargo / quien_cargo_persona_id). CredencialSol(..., **user.cargo_trazabilidad())."""
        a = self.autoria()
        return {"quien_cargo": a["usuario_id"],
                "quien_cargo_persona_id": a["persona_id"]}

    def filtro_autoria(self, col_usuario, col_persona):
        """Condición para LEER filas de ESTA identidad en tablas con columnas
        hermanas. OR sobre ambas columnas: reconoce filas viejas (usuario_id) y
        nuevas (persona_id) de la misma persona durante la transición. Ej.:
        select(Reaccion).where(user.filtro_autoria(Reaccion.usuario_id,
                                                    Reaccion.persona_id))."""
        conds = []
        if self.tiene_usuario:
            conds.append(col_usuario == self.id)
        if self.persona_id:
            conds.append(col_persona == self.persona_id)
        # Toda sesión tiene al menos una identidad; si no, no casar nada.
        return or_(*conds) if conds else false()


def _desde_sesion(sesion: dict) -> UsuarioActual:
    """Construye UsuarioActual desde el payload de la cookie. Tolera cookies
    viejas (sin los campos de Fase 3) con defaults."""
    pid = sesion.get("pid")
    return UsuarioActual(
        id=uuid.UUID(sesion["uid"]),
        estudio_id=uuid.UUID(sesion["eid"]),
        rol=RolUsuario(sesion["rol"]),
        nombre=sesion.get("nombre", ""),
        tipo_cuenta=sesion.get("tc", TipoCuenta.ESTUDIO.value),
        persona_id=uuid.UUID(pid) if pid else None,
        rol_sistema=sesion.get("rs"),
        tiene_usuario=sesion.get("tu", True),
        solo_lectura_ctx=sesion.get("sl", False),
        multi_contexto=sesion.get("mc", False),
        cargo=sesion.get("cg"),
    )


def usuario_actual(request: Request) -> UsuarioActual:
    """Dependencia: devuelve el usuario logueado o redirige a /login."""
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        raise RedirigirALogin()
    try:
        return _desde_sesion(sesion)
    except (KeyError, ValueError):
        raise RedirigirALogin()


def usuario_actual_opcional(request: Request) -> "UsuarioActual | None":
    """Como usuario_actual pero NO redirige: devuelve None si no hay sesión.

    Para rutas que sirven contenido público a anónimos y contenido propio a
    logueados (p.ej. la raíz "/" → landing si anónimo, dashboard si logueado)."""
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        return None
    try:
        return _desde_sesion(sesion)
    except (KeyError, ValueError):
        return None


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


def requiere_admin(user: UsuarioActual = Depends(usuario_actual)) -> UsuarioActual:
    """Solo ADMIN (dueño del estudio). El empresario/asistente NO accede
    (zAlerta-40: panel del blog)."""
    if not user.es_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Acceso solo para administradores.")
    return user
