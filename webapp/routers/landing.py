"""
webapp/routers/landing.py — alerta.pe (zAlerta-11a, Flujo 1)
═══════════════════════════════════════════════════════════════════════
Cara pública del producto para el EMPRESARIO (1 RUC, S/5):

  - GET  /landing   → landing persuasiva (variante C, equilibrada).
  - GET  /activar   → alta de fricción mínima (inteligencia del RUC).
  - POST /api/activar → crea la cuenta-empresario en "prueba" 7 días
                        (sin cobro — Etapa 2 aparte), opcionalmente con
                        credencial SOL cifrada, y deja la sesión iniciada.

La raíz "/" muestra esta landing a los anónimos (ver dashboard.py).
Multi-tenant: cada alta crea su propia organización aislada.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from db import get_session
import uuid

from sqlalchemy import select

from models import (
    EstudioContable, Usuario, Contribuyente, CredencialSol,
    EstadoContribuyente, RolUsuario, TipoCuenta, PlanComercial,
    EstadoSuscripcion, SolicitudValidacionCredencial, EstadoValidacion,
    limites_de, ahora_lima,
)
from cifrado import cifrar_clave_sol
from datetime import timedelta
from ..core import templates, WHATSAPP_SOPORTE
from ..auth import (
    hash_clave, crear_token_usuario, set_cookie_sesion,
    leer_sesion, COOKIE_NOMBRE,
)
from .clientes import consultar_ruc_api

router = APIRouter(tags=["landing"])

DIAS_PRUEBA = 7


def _dni_desde_ruc(ruc: str) -> str | None:
    """RUC de persona natural (empieza en 10) → DNI = 8 dígitos centrales.

    Formato: 10 + DNI(8) + verificador(1). El DNI son las posiciones 3..10.
    """
    if len(ruc) == 11 and ruc.startswith("10") and ruc.isdigit():
        return ruc[2:10]
    return None


@router.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    """Landing pública (no requiere sesión)."""
    user = None
    sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
    if sesion:
        # Logueado: ofrecer ir a su panel, pero la landing sigue siendo pública.
        user = {"nombre": sesion.get("nombre", "")}
    return templates.TemplateResponse(request, "landing.html", {
        "logueado": bool(sesion),
        "whatsapp_soporte": WHATSAPP_SOPORTE,
    })


@router.get("/activar", response_class=HTMLResponse)
async def activar_form(request: Request, ruc: str = ""):
    """Alta del empresario. Si ya hay sesión, al panel."""
    if leer_sesion(request.cookies.get(COOKIE_NOMBRE)):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "activar.html", {
        "ruc_pre": (ruc or "").strip(),
        "whatsapp_soporte": WHATSAPP_SOPORTE,
        "dias_prueba": DIAS_PRUEBA,
    })


@router.post("/api/activar")
async def api_activar(request: Request):
    """Crea la cuenta-empresario (prueba 7 días, SIN cobro) y la deja logueada.

    - tiene_clave=True  → crea el contribuyente CON credencial SOL cifrada.
    - tiene_clave=False → crea el contribuyente PENDIENTE de credenciales
      (el contador las cargará luego por el link viral). No bloquea.
    """
    data = await request.json()
    ruc = (data.get("ruc") or "").strip()
    razon_social = (data.get("razon_social") or "").strip() or None
    tiene_clave = bool(data.get("tiene_clave"))
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    cargo = (data.get("cargo") or "").strip() or None

    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse({"ok": False, "error": "RUC inválido (11 dígitos)."},
                            status_code=400)
    if tiene_clave and (not usuario_sol or not clave_sol):
        return JSONResponse(
            {"ok": False, "error": "Ingresa tu usuario y clave SOL, o elige "
                                   "«No la tengo»."}, status_code=400)

    async with get_session() as session:
        # Razón social: completar con la API RUC si no vino del front.
        if not razon_social:
            ficha = await consultar_ruc_api(session, ruc)
            razon_social = ficha.get("razon_social")
        nombre = razon_social or f"Empresario · RUC {ruc}"

        lim = limites_de(PlanComercial.EMPRESARIO.value)
        ahora = ahora_lima()
        estudio = EstudioContable(
            razon_social=nombre,
            tipo_cuenta=TipoCuenta.EMPRESARIO.value,
            plan=PlanComercial.EMPRESARIO.value,
            max_contribuyentes=lim["max_contribuyentes"],
            max_usuarios=lim["max_usuarios"],
            estado_suscripcion=EstadoSuscripcion.PRUEBA.value,
            suscripcion_vence_at=ahora + timedelta(days=DIAS_PRUEBA),
        )
        session.add(estudio)
        await session.flush()

        # Usuario del empresario: login sin fricción → clave aleatoria, se
        # auto-loguea por cookie. La fija después (debe_cambiar_clave).
        usuario = Usuario(
            estudio_id=estudio.id, nombre=nombre,
            dni=_dni_desde_ruc(ruc), whatsapp=None,
            access_code=hash_clave(secrets.token_urlsafe(24)),
            rol=RolUsuario.ADMIN, cargo=cargo,
            debe_cambiar_clave=True, clave_pendiente=True)
        session.add(usuario)
        await session.flush()

        # Contribuyente: el empresario se vigila a sí mismo. estudio_id y
        # cuenta_empresario_id apuntan a su propia organización (la vista de
        # empresario filtra por cuenta_empresario_id).
        contrib = Contribuyente(
            estudio_id=estudio.id, ruc=ruc, razon_social=razon_social,
            cuenta_empresario_id=estudio.id,
            estado=EstadoContribuyente.ACTIVO)
        session.add(contrib)
        await session.flush()

        if tiene_clave:
            session.add(CredencialSol(
                contribuyente_id=contrib.id, estudio_id=estudio.id,
                usuario_sol=usuario_sol,
                clave_sol_cifrada=cifrar_clave_sol(clave_sol),
                tipo_usuario=2, quien_cargo=usuario.id, valida=True))

        await session.commit()
        await session.refresh(usuario)

    # Login automático → su vista de empresario (solo lectura de su RUC).
    resp = JSONResponse({"ok": True, "redirect": "/"})
    set_cookie_sesion(resp, crear_token_usuario(usuario, TipoCuenta.EMPRESARIO.value))
    return resp


# ── "Comprobar conexión" PÚBLICO (antes de crear la cuenta, zAlerta-11a B.2) ──
# Reusa el ciclo flag→worker→resultado con estudio_id NULL (aún no hay tenant).
@router.post("/api/comprobar-credenciales")
async def comprobar_credenciales_publico(request: Request):
    data = await request.json()
    ruc = (data.get("ruc") or "").strip()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    if not (ruc.isdigit() and len(ruc) == 11) or not usuario_sol or not clave_sol:
        return JSONResponse(
            {"ok": False, "error": "Faltan RUC, usuario o clave SOL."},
            status_code=400)
    async with get_session() as session:
        sol = SolicitudValidacionCredencial(
            estudio_id=None, ruc=ruc, usuario_sol=usuario_sol,
            clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            estado=EstadoValidacion.PENDIENTE)
        session.add(sol)
        await session.commit()
        sol_id = str(sol.id)
    return JSONResponse({"ok": True, "id": sol_id})


@router.get("/api/comprobar-credenciales/{solicitud_id}")
async def estado_comprobacion_publico(solicitud_id: uuid.UUID):
    async with get_session() as session:
        sol = await session.get(SolicitudValidacionCredencial, solicitud_id)
        if not sol or sol.estudio_id is not None:
            # Solo solicitudes públicas (estudio_id NULL) son consultables aquí.
            return JSONResponse({"ok": False}, status_code=404)
        estado = sol.estado.value if hasattr(sol.estado, "value") else sol.estado
    listo = estado in ("conecta", "no_conecta", "error")
    return JSONResponse({"ok": True, "estado": estado,
                         "conecta": estado == "conecta", "listo": listo})
