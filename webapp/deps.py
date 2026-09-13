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

import logging
import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, or_, false

from db import get_session
from models import (RolUsuario, TipoCuenta, Contribuyente, Persona, Usuario,
                    Asignacion, AsignacionGrupo, ContribuyenteGrupo)
from .auth import COOKIE_NOMBRE, leer_sesion

logger = logging.getLogger("alertape.sesion")


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
    # RUC único al que se limita el acceso (SOCIO, contribuyente-scoped). None para
    # accesos de estudio. Latente en Fase 0 (el filtrado de vista por este id se
    # cablea en Fase 1); hoy siempre None porque no hay accesos contribuyente-scoped.
    contribuyente_scope: uuid.UUID | None = None
    # Versión de sesión del TOKEN (revocación server-side). Se compara contra la
    # versión en BD en el gate; None = token viejo sin `sv` → rechazado (corte limpio).
    sesion_version: int | None = None

    def puede_invitar(self, tipo: str) -> bool:
        """AUTORIDAD para emitir invitaciones (Capa 1 Fase B) — SEPARADO de
        `requiere_escritura`/`solo_lectura`: invitar es ADMINISTRAR ACCESOS, no
        operar el buzón, así que el empresario/socio (solo-lectura del buzón) SÍ
        puede. Se evalúa contra el CONTEXTO ACTIVO (rol + estudio del buzón activo).
          - 'cliente'   : el contador onboarda un cliente (crea org + invita dueño).
          - 'asistente' : el contador crea un asistente de SU estudio.
          - 'socio'     : el dueño/socio de la EMPRESA invita a otro co-dueño.
        Alcance Fase B = solo destinos estudio_id. Nadie más emite (SUPERVISOR,
        ASISTENTE, EMPRESARIO_ASISTENTE)."""
        if tipo in ("cliente", "asistente"):
            return self.rol == RolUsuario.CONTADOR_DUENO
        if tipo == "socio":
            return (self.rol in (RolUsuario.EMPRESARIO_LECTURA, RolUsuario.SOCIO)
                    and self.tipo_cuenta == TipoCuenta.EMPRESARIO.value)
        return False

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
    cid = sesion.get("cid")
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
        contribuyente_scope=uuid.UUID(cid) if cid else None,
        sesion_version=sesion.get("sv"),
    )


async def _sesion_no_revocada(user: "UsuarioActual") -> bool:
    """Gate de REVOCACIÓN server-side. Devuelve si la sesión sigue vigente:
      - token SIN `sv` (viejo, pre-fix) → False (corte limpio: re-login único).
      - `sv` del token == sesion_version en BD → True.
      - `sv` NO coincide (logout/cambio de clave lo incrementó) → False (revocada).
      - identidad inexistente / versión NULL → False (rechaza por seguridad).
      - BD NO responde (excepción/timeout) → True — NO expulsar a todos por un fallo
        de BD; la firma+exp ya se validaron (leer_sesion) y la ventana de replay es
        ínfima. Distingue 'revocada' (BD respondió y no casa) de 'BD no responde'.
    ANTI-LOCKOUT: un fallo de BD nunca deja a todo el mundo afuera."""
    if user.sesion_version is None:
        return False
    try:
        async with get_session() as session:
            if user.persona_id:
                actual = await session.scalar(
                    select(Persona.sesion_version).where(Persona.id == user.persona_id))
            else:
                actual = await session.scalar(
                    select(Usuario.sesion_version).where(Usuario.id == user.id))
    except Exception as e:
        # BD lenta/caída: NO revocar (no lockout masivo). Se registra y se permite.
        logger.warning("sesion: no se pudo verificar sesion_version (permito): %s", e)
        return True
    if actual is None:
        return False   # la identidad ya no existe / versión nula → rechazar
    return actual == user.sesion_version


async def usuario_actual(request: Request) -> UsuarioActual:
    """Dependencia: devuelve el usuario logueado o redirige a /login. Además del
    token firmado (leer_sesion), aplica el GATE de revocación server-side."""
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        raise RedirigirALogin()
    try:
        user = _desde_sesion(sesion)
    except (KeyError, ValueError):
        raise RedirigirALogin()
    if not await _sesion_no_revocada(user):
        raise RedirigirALogin()
    return user


async def usuario_actual_opcional(request: Request) -> "UsuarioActual | None":
    """Como usuario_actual pero NO redirige: devuelve None si no hay sesión.

    Para rutas que sirven contenido público a anónimos y contenido propio a
    logueados (p.ej. la raíz "/" → landing si anónimo, dashboard si logueado)."""
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        return None
    try:
        user = _desde_sesion(sesion)
    except (KeyError, ValueError):
        return None
    if not await _sesion_no_revocada(user):
        return None
    return user


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


async def rucs_de_asistente(session, estudio_id, persona_asistente_id) -> set:
    """Capa 1 Fase C — scope de un ASISTENTE: conjunto de contribuyente_ids que
    tiene asignados en un estudio = individuales (`Asignacion`) ∪ RUCs de sus grupos
    (`AsignacionGrupo`, resueltos EN VIVO por `ContribuyenteGrupo`). Helper ÚNICO que
    en Fase C4 usarán cartera, cliente._puede_ver y /resumen (para que el asistente
    vea lo MISMO en las tres). En C1 solo se DEFINE — nadie lo llama aún → cero
    cambio de comportamiento."""
    indiv = set(await session.scalars(
        select(Asignacion.contribuyente_id).where(
            Asignacion.estudio_id == estudio_id,
            Asignacion.persona_asistente_id == persona_asistente_id)))
    por_grupo = set(await session.scalars(
        select(ContribuyenteGrupo.contribuyente_id)
        .join(AsignacionGrupo, AsignacionGrupo.grupo_id == ContribuyenteGrupo.grupo_id)
        .where(AsignacionGrupo.estudio_id == estudio_id,
               AsignacionGrupo.persona_asistente_id == persona_asistente_id)))
    return indiv | por_grupo


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
