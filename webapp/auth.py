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

from db import get_session
from models import Usuario, RolUsuario, ahora_lima

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


def crear_token_usuario(usuario: Usuario) -> str:
    return firmar_sesion({
        "uid": str(usuario.id),
        "eid": str(usuario.estudio_id),
        "rol": usuario.rol.value,
        "nombre": usuario.nombre,
        "exp": int(time.time()) + DURACION_SESION,
    })


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
    dni = (dni or "").strip()
    async with get_session() as session:
        usuario = await session.scalar(
            select(Usuario).where(Usuario.dni == dni, Usuario.activo == True))  # noqa: E712

        if not usuario or not verificar_clave(usuario.access_code, clave):
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "DNI o clave incorrectos."}, status_code=401)

        usuario.ultimo_acceso_at = ahora_lima()
        await session.commit()

        # ¿Forzar cambio de clave?
        if usuario.debe_cambiar_clave:
            resp = RedirectResponse("/cambiar-clave", status_code=303)
        else:
            resp = RedirectResponse("/", status_code=303)
        set_cookie_sesion(resp, crear_token_usuario(usuario))
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
