"""
webapp/routers/registro.py — alerta.pe (zAlerta-06 Parte B)
═══════════════════════════════════════════════════════════════════════
Registro PÚBLICO (autoservicio). Un contador o un empresario se registra
solo, elige tipo y plan, y queda logueado en su dashboard.

MODO TESTERS: la suscripción queda en "prueba" (sin pago). Los pagos llegan
en la Etapa 2. Multi-tenant: cada registro crea su propia Organización
(EstudioContable) aislada.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from db import get_session
from models import (
    EstudioContable, Usuario, RolUsuario,
    TipoCuenta, EstadoSuscripcion, Contribuyente, EstadoContribuyente,
    LIMITES_PLAN, PLANES_POR_TIPO, limites_de,
)
from ..core import templates
from ..auth import hash_clave, crear_token_usuario, set_cookie_sesion, leer_sesion, COOKIE_NOMBRE

router = APIRouter(tags=["registro"])


def _planes_para_template() -> list[dict]:
    """Planes ofrecidos (con precio/límites) para pintar las tarjetas."""
    planes = []
    for tipo, claves in PLANES_POR_TIPO.items():
        for clave in claves:
            lim = LIMITES_PLAN[clave]
            planes.append({
                "tipo": tipo, "clave": clave, "nombre": lim["nombre"],
                "precio": lim["precio_soles"], "max_rucs": lim["max_contribuyentes"],
                "max_usuarios": lim["max_usuarios"],
            })
    return planes


def _error(request: Request, msg: str, status: int = 400,
           ruc_pre: str = "", empresario_pre: str = ""):
    return templates.TemplateResponse(
        request, "registro.html",
        {"planes": _planes_para_template(), "error": msg,
         "ruc_pre": ruc_pre, "empresario_pre": empresario_pre, "tipo_pre": ""},
        status_code=status)


@router.get("/registro", response_class=HTMLResponse)
async def registro_form(request: Request, ruc: str = "", empresario: str = "",
                        tipo: str = ""):
    # Si ya hay sesión, no tiene sentido registrarse: al dashboard.
    if leer_sesion(request.cookies.get(COOKIE_NOMBRE)):
        return RedirectResponse("/", status_code=303)
    # Link viral (zAlerta-11a B.4): el empresario manda a su contador aquí con
    # su RUC ya puesto. El contador registra su estudio y queda vigilando ese RUC.
    ruc = (ruc or "").strip()
    ruc_pre = ruc if (ruc.isdigit() and len(ruc) == 11) else ""
    return templates.TemplateResponse(
        request, "registro.html", {
            "planes": _planes_para_template(), "error": None,
            "ruc_pre": ruc_pre, "empresario_pre": (empresario or "").strip(),
            "tipo_pre": (tipo or "").strip()})


@router.post("/registro", response_class=HTMLResponse)
async def registro_post(
    request: Request,
    tipo_cuenta: str = Form(...),
    plan: str = Form(...),
    razon_social: str = Form(...),
    dni: str = Form(...),
    clave: str = Form(...),
    whatsapp: str = Form(""),
    correo: str = Form(""),
    ruc_precarga: str = Form(""),
    empresario_precarga: str = Form(""),
):
    tipo_cuenta = (tipo_cuenta or "").strip()
    plan = (plan or "").strip()
    razon_social = (razon_social or "").strip()
    dni = (dni or "").strip()
    whatsapp = (whatsapp or "").strip()
    correo = (correo or "").strip() or None
    ruc_precarga = (ruc_precarga or "").strip()
    empresario_precarga = (empresario_precarga or "").strip()
    ruc_valido = ruc_precarga.isdigit() and len(ruc_precarga) == 11

    # ── Validaciones ──
    if tipo_cuenta not in (TipoCuenta.EMPRESARIO.value, TipoCuenta.ESTUDIO.value):
        return _error(request, "Elige un tipo de cuenta.")
    if plan not in PLANES_POR_TIPO.get(tipo_cuenta, []):
        return _error(request, "El plan elegido no corresponde al tipo de cuenta.")
    if not razon_social:
        return _error(request, "Ingresa tu nombre o razón social.")
    if not (dni.isdigit() and len(dni) == 8):
        return _error(request, "El DNI debe tener 8 dígitos.")
    if len(clave) < 6:
        return _error(request, "La clave debe tener al menos 6 caracteres.")
    if tipo_cuenta == TipoCuenta.EMPRESARIO.value and not whatsapp:
        return _error(request, "El WhatsApp es obligatorio para cuentas de empresario.")

    lim = limites_de(plan)

    async with get_session() as session:
        # DNI único a nivel login (evita ambigüedad de sesión).
        ya = await session.scalar(select(Usuario.id).where(Usuario.dni == dni))
        if ya:
            return _error(request, "Ese DNI ya tiene una cuenta. Inicia sesión.", 409)

        estudio = EstudioContable(
            razon_social=razon_social,
            tipo_cuenta=tipo_cuenta,
            plan=plan,
            max_contribuyentes=lim["max_contribuyentes"],
            max_usuarios=lim["max_usuarios"],
            estado_suscripcion=EstadoSuscripcion.PRUEBA.value,
            whatsapp=whatsapp or None,
            correo_contacto=correo,
        )
        session.add(estudio)
        await session.flush()

        usuario = Usuario(
            estudio_id=estudio.id, nombre=razon_social, dni=dni,
            whatsapp=whatsapp or None, correo=correo,
            access_code=hash_clave(clave),
            rol=RolUsuario.ADMIN,
            debe_cambiar_clave=False,   # la clave la eligió él mismo
        )
        session.add(usuario)
        await session.flush()

        # Link viral (zAlerta-11a B.4): si vino con un RUC pre-cargado y es un
        # estudio, dejarlo ya vigilando ese RUC (pendiente de credenciales SOL,
        # que el contador cargará desde su alta). No bloquea el registro.
        if ruc_valido and tipo_cuenta == TipoCuenta.ESTUDIO.value:
            ya = await session.scalar(select(Contribuyente.id).where(
                Contribuyente.estudio_id == estudio.id,
                Contribuyente.ruc == ruc_precarga))
            if not ya:
                session.add(Contribuyente(
                    estudio_id=estudio.id, ruc=ruc_precarga,
                    razon_social=empresario_precarga or None,
                    estado=EstadoContribuyente.ACTIVO))

        await session.commit()
        await session.refresh(usuario)

    # Login automático → su dashboard.
    resp = RedirectResponse("/", status_code=303)
    set_cookie_sesion(resp, crear_token_usuario(usuario, tipo_cuenta))
    return resp
