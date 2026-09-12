"""
webapp/routers/invitaciones.py — alerta.pe (Capa 1 · Fase B)
═══════════════════════════════════════════════════════════════════════
Invitaciones por LINK con destino estudio_id (las SEGURAS — no requieren el
filtrado de vista de Fase D). Principio: quien tiene AUTORIDAD sobre un
estudio/empresa invita; al aceptar nace Persona + Acceso según el rol y destino
que viajan en la Invitacion.

ALCANCE Fase B (SOLO estudio_id):
  - cliente/empresario  → Acceso(estudio_id=org-empresario, EMPRESARIO_LECTURA)
  - socio               → Acceso(estudio_id=org-empresario, SOCIO)
  - asistente de estudio→ Acceso(estudio_id=estudio, ASISTENTE)
EXCLUIDO (Fase D): invitaciones con contribuyente_id (contador invitado).

NO toca el flujo viral en producción (crear_contribuyente / _crear_cuenta_empresario)
ni migra activacion_token: eso es el reencauzamiento (#5), en revisión aparte.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, func

from db import get_session
from models import (
    Invitacion, EstadoInvitacion, Acceso, Persona, EstudioContable, Contribuyente,
    CredencialSol, RolUsuario, CargoInstitucional, TipoCuenta, PlanComercial,
    EstadoContribuyente, EstadoSuscripcion, limites_de, ahora_lima,
)
from cifrado import cifrar_clave_sol
from ..core import templates, WHATSAPP_SOPORTE, BASE_URL
from ..auth import (hash_clave, crear_token_persona, set_cookie_sesion,
                    leer_sesion, COOKIE_NOMBRE)
from ..deps import usuario_actual, UsuarioActual
from ..auditoria import registrar_auditoria

router = APIRouter(tags=["invitaciones"])

CADUCIDAD_DIAS = 7
_RE_RUC = re.compile(r"^\d{11}$")

# Etiquetas legibles del rol para la pantalla de aceptar.
_ROL_LABEL = {
    RolUsuario.EMPRESARIO_LECTURA: "dueño de la empresa",
    RolUsuario.SOCIO: "socio (co-dueño)",
    RolUsuario.ASISTENTE: "asistente del estudio",
}


def _link(token: str) -> str:
    return f"{BASE_URL}/invitacion?t={token}"


def _wa(contacto: str | None, texto: str) -> str | None:
    c = re.sub(r"\D", "", contacto or "")
    return f"https://wa.me/{c}?text={quote(texto)}" if c else None


async def _nueva_invitacion(session, *, user, rol_destino, estudio_id,
                            contacto=None, dni_esperado=None):
    inv = Invitacion(
        token=secrets.token_urlsafe(32), rol_destino=rol_destino,
        estudio_id=estudio_id, contribuyente_id=None,
        invitado_por_persona_id=user.persona_id,
        estudio_contexto_id=user.estudio_id,
        destino_contacto=contacto, dni_esperado=(dni_esperado or None),
        estado=EstadoInvitacion.PENDIENTE,
        caduca_at=ahora_lima() + timedelta(days=CADUCIDAD_DIAS))
    session.add(inv)
    return inv


# ═════════════════════ EMISORES (crear invitación) ═════════════════════
@router.post("/api/invitar/socio")
async def invitar_socio(request: Request,
                        user: UsuarioActual = Depends(usuario_actual)):
    """El dueño/socio de la EMPRESA invita a otro co-dueño (SOCIO). Destino = su
    propia org-empresario (estudio_id del contexto activo)."""
    if not user.puede_invitar("socio"):
        return JSONResponse({"ok": False, "error": "No autorizado para invitar socios."},
                            status_code=403)
    data = await request.json()
    contacto = (data.get("whatsapp") or data.get("contacto") or "").strip() or None
    dni_esp = re.sub(r"\D", "", data.get("dni") or "") or None
    async with get_session() as session:
        org = await session.get(EstudioContable, user.estudio_id)
        if not org or org.tipo_cuenta != TipoCuenta.EMPRESARIO.value:
            return JSONResponse({"ok": False, "error": "Solo desde una cuenta de empresa."},
                                status_code=400)
        inv = await _nueva_invitacion(session, user=user, rol_destino=RolUsuario.SOCIO,
                                      estudio_id=org.id, contacto=contacto, dni_esperado=dni_esp)
        await registrar_auditoria(session, persona_id=user.persona_id, accion="INVITAR",
                                  estudio_id=org.id, objeto_tipo="invitacion", objeto_id=inv.id,
                                  datos={"tipo": "socio", "rol": "SOCIO"})
        await session.commit()
        token = inv.token
        nombre = org.razon_social or "tu empresa"
    link = _link(token)
    wa = _wa(contacto, f"Te invito como SOCIO de {nombre} en alerta.pe. "
                       f"Activa tu acceso con tu DNI aquí: {link}")
    return JSONResponse({"ok": True, "link": link, "wa_url": wa})


@router.post("/api/invitar/asistente")
async def invitar_asistente(request: Request,
                            user: UsuarioActual = Depends(usuario_actual)):
    """El contador dueño crea un ASISTENTE de SU estudio. Destino = su estudio_id."""
    if not user.puede_invitar("asistente"):
        return JSONResponse({"ok": False, "error": "No autorizado para crear asistentes."},
                            status_code=403)
    data = await request.json()
    contacto = (data.get("whatsapp") or data.get("contacto") or "").strip() or None
    dni_esp = re.sub(r"\D", "", data.get("dni") or "") or None
    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)
        if not estudio or estudio.tipo_cuenta != TipoCuenta.ESTUDIO.value:
            return JSONResponse({"ok": False, "error": "Solo desde un estudio."},
                                status_code=400)
        inv = await _nueva_invitacion(session, user=user, rol_destino=RolUsuario.ASISTENTE,
                                      estudio_id=estudio.id, contacto=contacto, dni_esperado=dni_esp)
        await registrar_auditoria(session, persona_id=user.persona_id, accion="INVITAR",
                                  estudio_id=estudio.id, objeto_tipo="invitacion", objeto_id=inv.id,
                                  datos={"tipo": "asistente", "rol": "ASISTENTE"})
        await session.commit()
        token = inv.token
        nombre = estudio.razon_social or "el estudio"
    link = _link(token)
    wa = _wa(contacto, f"Te sumo como asistente de {nombre} en alerta.pe. "
                       f"Activa tu acceso con tu DNI aquí: {link}")
    return JSONResponse({"ok": True, "link": link, "wa_url": wa})


@router.post("/api/invitar/cliente")
async def invitar_cliente(request: Request,
                          user: UsuarioActual = Depends(usuario_actual)):
    """El contador onboarda un CLIENTE: crea la org-empresario + el Contribuyente
    SIN credencial (el CLIENTE pondrá su Clave SOL al aceptar → hasta entonces el
    RUC figura 'sin credencial = no vigilado') y emite la invitación al dueño.

    NUEVO y PARALELO al alta viral existente (que NO se toca); su unificación es #5.
    """
    if not user.puede_invitar("cliente"):
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    data = await request.json()
    ruc = re.sub(r"\D", "", data.get("ruc") or "")
    razon = (data.get("razon_social") or "").strip() or None
    emp_nombre = (data.get("empresario_nombre") or "").strip() or None
    emp_wa = (data.get("empresario_whatsapp") or "").strip() or None
    if not _RE_RUC.match(ruc):
        return JSONResponse({"ok": False, "error": "RUC inválido (11 dígitos)."},
                            status_code=400)
    async with get_session() as session:
        # Unicidad del RUC dentro del estudio que vigila.
        if await session.scalar(select(Contribuyente.id).where(
                Contribuyente.estudio_id == user.estudio_id,
                Contribuyente.ruc == ruc)):
            return JSONResponse({"ok": False, "error": "Ese RUC ya está en tu cartera."},
                                status_code=409)
        lim = limites_de(PlanComercial.CLIENTE_DE_ESTUDIO.value)
        org = EstudioContable(
            razon_social=(emp_nombre or razon or f"Empresa · RUC {ruc}"),
            tipo_cuenta=TipoCuenta.EMPRESARIO.value,
            plan=PlanComercial.CLIENTE_DE_ESTUDIO.value,
            max_contribuyentes=lim["max_contribuyentes"], max_usuarios=lim["max_usuarios"],
            estado_suscripcion=EstadoSuscripcion.ACTIVA.value,
            whatsapp=emp_wa, creado_por_estudio_id=user.estudio_id)
        session.add(org)
        await session.flush()
        # Contribuyente en el estudio del contador (lo vigila), dueño = la org.
        # SIN CredencialSol → el worker no lo scrapea aún ("no vigilado todavía").
        contrib = Contribuyente(
            estudio_id=user.estudio_id, ruc=ruc, razon_social=razon,
            cuenta_empresario_id=org.id, estado=EstadoContribuyente.ACTIVO)
        session.add(contrib)
        await session.flush()
        inv = await _nueva_invitacion(session, user=user,
                                      rol_destino=RolUsuario.EMPRESARIO_LECTURA,
                                      estudio_id=org.id, contacto=emp_wa)
        await registrar_auditoria(session, persona_id=user.persona_id, accion="INVITAR",
                                  estudio_id=org.id, contribuyente_id=contrib.id,
                                  objeto_tipo="invitacion", objeto_id=inv.id,
                                  datos={"tipo": "cliente", "ruc": ruc, "rol": "EMPRESARIO_LECTURA"})
        await session.commit()
        token = inv.token
    link = _link(token)
    wa = _wa(emp_wa, f"Le doy acceso gratuito a la información tributaria de su RUC {ruc} "
                     f"en alerta.pe. Active su acceso (DNI + su Clave SOL) aquí: {link}")
    return JSONResponse({"ok": True, "link": link, "wa_url": wa, "ruc": ruc})


# ═════════════════════ ACEPTAR (pantalla + POST) ═════════════════════
async def _cargar_invitacion(session, token: str):
    """Devuelve (inv, motivo_invalida). motivo None si es aceptable."""
    if not token:
        return None, "Link inválido."
    inv = await session.scalar(select(Invitacion).where(Invitacion.token == token))
    if not inv:
        return None, "Link inválido o no existe."
    if inv.estado == EstadoInvitacion.ACEPTADA:
        return inv, "Esta invitación ya fue usada."
    if inv.estado == EstadoInvitacion.REVOCADA:
        return inv, "Esta invitación fue revocada."
    if inv.caduca_at and inv.caduca_at < ahora_lima():
        return inv, "Esta invitación caducó."
    return inv, None


@router.get("/invitacion", response_class=HTMLResponse)
async def invitacion_form(request: Request, t: str = ""):
    t = (t or "").strip()
    ya_sesion = bool(leer_sesion(request.cookies.get(COOKIE_NOMBRE)))
    ctx = {"token": t, "valido": False, "motivo": None, "org_nombre": "",
           "rol_label": "", "es_cliente": False, "ya_sesion": ya_sesion,
           "whatsapp_soporte": WHATSAPP_SOPORTE, "dni_esperado": ""}
    async with get_session() as session:
        inv, motivo = await _cargar_invitacion(session, t)
        if inv and not motivo:
            org = await session.get(EstudioContable, inv.estudio_id)
            ctx.update(valido=True, org_nombre=(org.razon_social if org else ""),
                       rol_label=_ROL_LABEL.get(inv.rol_destino, "acceso"),
                       es_cliente=(inv.rol_destino == RolUsuario.EMPRESARIO_LECTURA),
                       dni_esperado=(inv.dni_esperado or ""))
        else:
            ctx["motivo"] = motivo
    return templates.TemplateResponse(request, "invitacion.html", ctx)


@router.post("/api/invitacion/aceptar")
async def invitacion_aceptar(request: Request):
    data = await request.json()
    t = (data.get("token") or "").strip()
    dni = re.sub(r"\D", "", data.get("dni") or "")
    nombres = (data.get("nombres") or "").strip()
    clave = data.get("clave") or ""
    clave_rep = data.get("clave_repetir") or ""
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""

    async with get_session() as session:
        inv, motivo = await _cargar_invitacion(session, t)
        if motivo:
            return JSONResponse({"ok": False, "error": motivo}, status_code=409)

        # Identidad: (1) si ya hay sesión → sumar a ESA persona; (2) si el DNI ya es
        # Persona → sumar a ella; (3) si no → crear identidad nueva (DNI + clave).
        ya_sesion = leer_sesion(request.cookies.get(COOKIE_NOMBRE))
        persona = None
        if ya_sesion and ya_sesion.get("pid"):
            persona = await session.get(Persona, uuid.UUID(ya_sesion["pid"]))
        if persona is None and dni.isdigit() and len(dni) == 8:
            persona = await session.scalar(select(Persona).where(Persona.dni == dni))

        if persona is None:
            # Crear identidad nueva: exige DNI + clave elegida (≠ DNI).
            if not (dni.isdigit() and len(dni) == 8):
                return JSONResponse({"ok": False, "error": "Ingresa tu DNI (8 dígitos)."}, status_code=400)
            if len(clave) < 6:
                return JSONResponse({"ok": False, "error": "Crea una clave de al menos 6 caracteres."}, status_code=400)
            if clave != clave_rep:
                return JSONResponse({"ok": False, "error": "Las claves no coinciden."}, status_code=400)
            if clave == dni:
                return JSONResponse({"ok": False, "error": "La clave no puede ser igual a tu DNI."}, status_code=400)
            persona = Persona(dni=dni, nombre_completo=(nombres or None),
                              clave_hash=hash_clave(clave), debe_cambiar_clave=False,
                              rol_sistema=None)
            session.add(persona)
            await session.flush()

        # Crear el Acceso (idempotente: si ya existe uno a ese estudio, no duplica).
        existente = await session.scalar(select(Acceso.id).where(
            Acceso.persona_id == persona.id, Acceso.estudio_id == inv.estudio_id))
        if not existente:
            session.add(Acceso(
                persona_id=persona.id, estudio_id=inv.estudio_id,
                rol=inv.rol_destino, cargo=None,
                vigencia_inicio=ahora_lima().date(),
                es_solo_lectura=(inv.rol_destino in (
                    RolUsuario.EMPRESARIO_LECTURA, RolUsuario.SOCIO,
                    RolUsuario.ASISTENTE, RolUsuario.EMPRESARIO_ASISTENTE))))

        # CLIENTE (empresario): puede poner su Clave SOL aquí (nunca pasa por el
        # contador). Sin ella, el RUC queda 'sin credencial = no vigilado'.
        if inv.rol_destino == RolUsuario.EMPRESARIO_LECTURA and usuario_sol and clave_sol:
            contrib = await session.scalar(select(Contribuyente).where(
                Contribuyente.cuenta_empresario_id == inv.estudio_id)
                .order_by(Contribuyente.creado_at).limit(1))
            if contrib and not await session.scalar(select(CredencialSol.id).where(
                    CredencialSol.contribuyente_id == contrib.id)):
                session.add(CredencialSol(
                    contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
                    usuario_sol=usuario_sol, clave_sol_cifrada=cifrar_clave_sol(clave_sol),
                    tipo_usuario=2, valida=True, quien_cargo_persona_id=persona.id))
                contrib.actualizar_solicitado = True
                contrib.actualizar_solicitado_at = ahora_lima()

        inv.estado = EstadoInvitacion.ACEPTADA
        inv.aceptada_at = ahora_lima()
        inv.aceptada_por_persona_id = persona.id
        await registrar_auditoria(session, persona_id=persona.id, accion="ACEPTAR_INVITACION",
                                  estudio_id=inv.estudio_id, objeto_tipo="invitacion",
                                  objeto_id=inv.id, datos={"rol": inv.rol_destino.name})
        await session.commit()

        # Sesión: si ya estaba logueado, no la tocamos (ya tiene su cuenta); si no,
        # lo dejamos dentro con la identidad recién creada/aceptada.
        if not ya_sesion:
            ctx = await _resolver_para_login(session, persona)
            token = crear_token_persona(*ctx) if ctx else None
        else:
            token = None

    resp = JSONResponse({"ok": True, "redirect": "/"})
    if token:
        set_cookie_sesion(resp, token)
    return resp


async def _resolver_para_login(session, persona):
    """(persona, estudio, acceso, tiene_usuario, multi) para crear_token_persona."""
    from ..auth import _resolver_contexto_persona
    ctx = await _resolver_contexto_persona(session, persona)
    if not ctx:
        return None
    estudio, acceso, multi, tiene_usuario = ctx
    return (persona, estudio, acceso, tiene_usuario, multi)
