"""
webapp/routers/contadores.py — alerta.pe (zAlerta-99 · puerta del estudio)
═══════════════════════════════════════════════════════════════════════
Registro + login del CONTADOR DUEÑO. La puerta de entrada del acceso-estudios.
REUSA el modelo z-89 (personas, estudios_contables, accesos, RolUsuario) y el
mecanismo de sesión/Argon2 existente (auth.py) — solo PUEBLA por primera vez.

Registro directo (fricción mínima): crea persona + estudio + acceso CONTADOR_DUENO,
inicia sesión y lleva a /cartera. Login es el /login existente (DNI + clave); el
aterrizaje por rol (→ /cartera) vive en auth.destino_por_acceso.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from db import get_session
from models import (
    Persona, EstudioContable, Acceso, RolUsuario, CargoInstitucional,
    TipoCuenta, ahora_lima,
)
from ..core import templates
from ..auth import (hash_clave, crear_token_persona, set_cookie_sesion,
                    leer_sesion, COOKIE_NOMBRE)
from .clientes import autocompletar_ficha

router = APIRouter(tags=["contadores"])

_RE_DNI = re.compile(r"^\d{8}$")
_RE_RUC = re.compile(r"^\d{8,11}$")


@router.get("/contadores", response_class=HTMLResponse)
async def contadores_landing(request: Request):
    """Puerta de los contadores: dos accesos (estudio / independiente) + registro.
    Si ya hay sesión, a su pantalla."""
    if leer_sesion(request.cookies.get(COOKIE_NOMBRE)):
        return RedirectResponse("/cartera", status_code=303)
    return templates.TemplateResponse(request, "contadores.html", {"error": None})


@router.post("/contadores/registro", response_class=HTMLResponse)
async def contadores_registro(
    request: Request,
    dni: str = Form(...),
    nombres: str = Form(...),
    clave: str = Form(...),
    clave_repetir: str = Form(...),
    nombre_estudio: str = Form(...),
    ruc: str = Form(...),
    tipo: str = Form("estudio"),          # 'estudio' | 'independiente'
):
    def _err(msg, code=400):
        return templates.TemplateResponse(
            request, "contadores.html",
            {"error": msg, "dni": dni, "nombres": nombres,
             "nombre_estudio": nombre_estudio, "ruc": ruc, "tipo": tipo},
            status_code=code)

    dni = (dni or "").strip()
    ruc = re.sub(r"\D", "", ruc or "")
    nombres = (nombres or "").strip()
    nombre_estudio = (nombre_estudio or "").strip()
    tipo = "independiente" if tipo == "independiente" else "estudio"

    # ── Validaciones (fricción mínima, pero sanas) ──
    if not _RE_DNI.match(dni):
        return _err("El DNI debe tener 8 dígitos.")
    if not nombres:
        return _err("Escribe tus apellidos y nombres.")
    if len(clave or "") < 6:
        return _err("La clave debe tener al menos 6 caracteres.")
    if clave != clave_repetir:
        return _err("Las claves no coinciden.")
    if not nombre_estudio:
        return _err("Escribe el nombre de tu estudio.")
    if not _RE_RUC.match(ruc):
        return _err("El RUC no es válido (8 a 11 dígitos).")

    async with get_session() as session:
        # DNI único (no registrar dos veces el mismo dueño).
        if await session.scalar(select(Persona.id).where(Persona.dni == dni)):
            return _err("Ya existe una cuenta con ese DNI. Inicia sesión.", 409)

        # RUC: confirmar razón social por API (best-effort; si no hay red, usa el
        # nombre que puso el contador — sin bloquear el registro).
        razon = nombre_estudio
        try:
            ficha = await autocompletar_ficha(ruc)
            if ficha and ficha.get("razon_social"):
                razon = ficha["razon_social"]
        except Exception:
            pass

        persona = Persona(
            dni=dni, nombre_completo=nombres,
            clave_hash=hash_clave(clave), debe_cambiar_clave=False,
            rol_sistema=None)   # dueño normal, NO soporte_global
        session.add(persona)
        await session.flush()   # persona.id

        estudio = EstudioContable(
            razon_social=nombre_estudio or razon, ruc=ruc,
            tipo_cuenta=TipoCuenta.ESTUDIO.value,
            contador_dueno_persona_id=persona.id,
            estado="activo", segmento=tipo, activo=True)
        session.add(estudio)
        await session.flush()   # estudio.id

        acceso = Acceso(
            persona_id=persona.id, estudio_id=estudio.id,
            rol=RolUsuario.CONTADOR_DUENO, cargo=CargoInstitucional.DUENO,
            vigencia_inicio=ahora_lima().date(), es_solo_lectura=False)
        session.add(acceso)
        await session.commit()

        # Inicia sesión automáticamente (fricción mínima) → /cartera.
        token = crear_token_persona(persona, estudio, acceso,
                                    tiene_usuario=False, multi=False)

    resp = RedirectResponse("/cartera", status_code=303)
    set_cookie_sesion(resp, token)
    return resp
