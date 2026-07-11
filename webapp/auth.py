"""
webapp/auth.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Autenticación de la WebApp (zAlerta-01 B.1):
  - LOGIN POR DNI (8 chars) + clave, verificada con Argon2.
  - Clave inicial = DNI; si debe_cambiar_clave, se fuerza el cambio.
  - Sesión por COOKIE FIRMADA (HMAC-SHA256, stdlib). NO sesión en BD.
  - Multi-tenant: la sesión guarda estudio_id; toda query filtra por él.
  - RBAC: admin / contador / asistente (asistente = solo lectura).

La cookie lleva un payload JSON firmado: {uid, eid, rol, exp}. No usamos
itsdangerous (no instalado) ni guardamos estado en BD; firmamos con
JWT_SECRET del entorno usando hmac, suficiente y sin dependencias extra.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from sqlalchemy import or_

from db import get_session
from models import (
    Usuario, EstudioContable, RolUsuario, ahora_lima,
    Persona, Acceso, AuditoriaSoporte,
)

from .core import templates

# ─────────────────────────────────────────────────────────────────────
# Configuración de sesión
# ─────────────────────────────────────────────────────────────────────
COOKIE_NOMBRE = "alertape_sesion"
DURACION_SESION = 60 * 60 * 12          # 12 horas
_SECRET = (os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")
           or "dev-inseguro-cambiar").encode("utf-8")

ph = PasswordHasher()   # Argon2 (mismo hashing que el seed)


# ─────────────────────────────────────────────────────────────────────
# Firma de cookie (HMAC-SHA256, stdlib)
# ─────────────────────────────────────────────────────────────────────
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def firmar_sesion(payload: dict) -> str:
    """Serializa y firma el payload. Devuelve 'cuerpo.firma'."""
    cuerpo = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    firma = _b64e(hmac.new(_SECRET, cuerpo.encode("ascii"), hashlib.sha256).digest())
    return f"{cuerpo}.{firma}"


def leer_sesion(token: str | None) -> dict | None:
    """Valida firma y expiración. Devuelve el payload o None."""
    if not token or "." not in token:
        return None
    cuerpo, _, firma = token.partition(".")
    esperada = _b64e(hmac.new(_SECRET, cuerpo.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        payload = json.loads(_b64d(cuerpo))
    except Exception:
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload


def crear_token_usuario(usuario: Usuario, tipo_cuenta: str = "estudio") -> str:
    """Token del login VIEJO (respaldo dual-read). tiene_usuario siempre True."""
    return firmar_sesion({
        "uid": str(usuario.id),
        "eid": str(usuario.estudio_id),
        "rol": usuario.rol.value,
        "tc": tipo_cuenta,            # tipo de cuenta (estudio | empresario)
        "nombre": usuario.nombre,
        "tu": True,
        "exp": int(time.time()) + DURACION_SESION,
    })


def crear_token_persona(persona: "Persona", estudio: "EstudioContable",
                        acceso: "Acceso | None", tiene_usuario: bool,
                        multi: bool) -> str:
    """Token del login NUEVO (Acceso Institucional, zAlerta-60). El contexto
    activo (eid/tc/rol) sale del acceso elegido → las queries existentes
    (WHERE estudio_id==...) siguen funcionando sin cambios."""
    return firmar_sesion({
        "pid": str(persona.id),
        # uid: para Duilio coincide con su usuarios.id (get(Usuario) funciona);
        # para personas sin usuario es el persona.id (get(Usuario) → None, tolerado).
        "uid": str(persona.id),
        "eid": str(estudio.id),
        "rol": (acceso.rol.value if acceso else RolUsuario.CONTADOR.value),
        "tc": estudio.tipo_cuenta,
        "rs": (persona.rol_sistema.name if persona.rol_sistema else None),
        "tu": tiene_usuario,
        "sl": bool(acceso.es_solo_lectura) if acceso else True,
        "mc": multi,
        "nombre": persona.nombre_completo or "",
        "exp": int(time.time()) + DURACION_SESION,
    })


async def accesos_vigentes(session, persona_id):
    """Accesos de la persona con vigencia vigente (fin NULL o >= hoy)."""
    hoy = ahora_lima().date()
    return (await session.execute(
        select(Acceso).where(
            Acceso.persona_id == persona_id,
            or_(Acceso.vigencia_fin.is_(None), Acceso.vigencia_fin >= hoy))
    )).scalars().all()


async def _resolver_contexto_persona(session, persona) -> "tuple | None":
    """Resuelve el contexto de entrada de una persona: (estudio, acceso, multi,
    tiene_usuario). Devuelve None si no tiene accesos vigentes ni es soporte."""
    accesos = await accesos_vigentes(session, persona.id)
    soporte = persona.rol_sistema is not None and persona.rol_sistema.name == "SOPORTE_GLOBAL"
    tiene_usuario = bool(await session.scalar(
        select(Usuario.id).where(Usuario.dni == persona.dni)))
    if accesos:
        acceso = accesos[0]
        estudio = await session.get(EstudioContable, acceso.estudio_id)
    elif soporte:
        estudio = await session.scalar(
            select(EstudioContable).where(EstudioContable.activo == True)  # noqa: E712
            .order_by(EstudioContable.razon_social).limit(1))
        acceso = None
    else:
        return None
    if estudio is None:
        return None
    multi = len(accesos) > 1 or soporte
    return estudio, acceso, multi, tiene_usuario


def set_cookie_sesion(resp, token: str) -> None:
    resp.set_cookie(
        COOKIE_NOMBRE, token, max_age=DURACION_SESION,
        httponly=True, samesite="lax", secure=False,  # secure=True en prod (HTTPS)
        path="/")


# ─────────────────────────────────────────────────────────────────────
# Hashing de claves (Argon2)
# ─────────────────────────────────────────────────────────────────────
def hash_clave(clave: str) -> str:
    return ph.hash(clave)


def verificar_clave(hash_guardado: str, clave: str) -> bool:
    try:
        return ph.verify(hash_guardado, clave)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


# ─────────────────────────────────────────────────────────────────────
# Rutas de autenticación
# ─────────────────────────────────────────────────────────────────────
router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    # Si ya hay sesión válida, al dashboard
    if leer_sesion(request.cookies.get(COOKIE_NOMBRE)):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    dni: str = Form(...),
    clave: str = Form(...),
):
    ident = (dni or "").strip()
    async with get_session() as session:
        # ═══ DUAL-READ (zAlerta-60) ═══
        # 1) Modelo NUEVO: persona por DNI. Si resuelve, manda el nuevo.
        persona = await session.scalar(select(Persona).where(Persona.dni == ident))
        if (persona and persona.clave_hash
                and verificar_clave(persona.clave_hash, clave)):
            ctx = await _resolver_contexto_persona(session, persona)
            if ctx is None:
                return templates.TemplateResponse(
                    request, "login.html",
                    {"error": "Tu usuario no tiene buzones activos. "
                              "Contacta a soporte."}, status_code=403)
            estudio, acceso, multi, tiene_usuario = ctx
            token = crear_token_persona(persona, estudio, acceso, tiene_usuario, multi)
            # Clave-DNI temporal → forzar cambio antes de operar.
            if persona.debe_cambiar_clave:
                destino = "/cambiar-clave"
            else:
                destino = "/seleccionar-buzon" if multi else "/"
            resp = RedirectResponse(destino, status_code=303)
            set_cookie_sesion(resp, token)
            return resp

        # 2) RESPALDO: login VIEJO (usuarios) — tal cual funciona hoy.
        #    DNI (estudio) o WhatsApp (empresario, zAlerta-06).
        usuario = await session.scalar(
            select(Usuario).where(
                or_(Usuario.dni == ident, Usuario.whatsapp == ident),
                Usuario.activo == True))  # noqa: E712

        if not usuario or not verificar_clave(usuario.access_code, clave):
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Datos incorrectos o clave aún no activada."},
                status_code=401)

        usuario.ultimo_acceso_at = ahora_lima()
        await session.commit()

        # Tipo de cuenta (para enrutar/escopar al empresario en la sesión).
        tipo_cuenta = await session.scalar(
            select(EstudioContable.tipo_cuenta).where(
                EstudioContable.id == usuario.estudio_id)) or "estudio"

        # ¿Forzar cambio de clave?
        if usuario.debe_cambiar_clave:
            resp = RedirectResponse("/cambiar-clave", status_code=303)
        else:
            resp = RedirectResponse("/", status_code=303)
        set_cookie_sesion(resp, crear_token_usuario(usuario, tipo_cuenta))
        return resp


@router.get("/cambiar-clave", response_class=HTMLResponse)
async def cambiar_clave_form(request: Request):
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "cambiar_clave.html", {"error": None})


@router.post("/cambiar-clave", response_class=HTMLResponse)
async def cambiar_clave_post(
    request: Request,
    clave_nueva: str = Form(...),
    clave_repetir: str = Form(...),
):
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if not sesion:
        return RedirectResponse("/login", status_code=303)

    if len(clave_nueva) < 6:
        return templates.TemplateResponse(
            request, "cambiar_clave.html",
            {"error": "La clave debe tener al menos 6 caracteres."}, status_code=400)
    if clave_nueva != clave_repetir:
        return templates.TemplateResponse(
            request, "cambiar_clave.html",
            {"error": "Las claves no coinciden."}, status_code=400)

    # ── Modo PERSONA (login nuevo): actualiza personas.clave_hash ──
    pid = sesion.get("pid")
    if pid:
        async with get_session() as session:
            persona = await session.get(Persona, uuid.UUID(pid))
            if not persona:
                return RedirectResponse("/login", status_code=303)
            if clave_nueva == persona.dni:
                return templates.TemplateResponse(
                    request, "cambiar_clave.html",
                    {"error": "La clave no puede ser igual a tu DNI."},
                    status_code=400)
            persona.clave_hash = hash_clave(clave_nueva)
            persona.debe_cambiar_clave = False
            await session.commit()
        # Ya con clave nueva: al selector si tiene varios buzones, si no directo.
        destino = "/seleccionar-buzon" if sesion.get("mc") else "/"
        return RedirectResponse(destino, status_code=303)

    # ── Modo USUARIO (login viejo) ──
    async with get_session() as session:
        usuario = await session.get(Usuario, uuid.UUID(sesion["uid"]))
        if not usuario:
            return RedirectResponse("/login", status_code=303)
        usuario.access_code = hash_clave(clave_nueva)
        usuario.debe_cambiar_clave = False
        await session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
@router.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NOMBRE, path="/")
    return resp
