"""
webapp/routers/clientes.py — alerta.pe (zAlerta-02 · acciones rápidas)
═══════════════════════════════════════════════════════════════════════
Alta de contribuyentes desde el botón "+":
  - GET  /api/grupos          → grupos del estudio (para el form, JSON).
  - POST /contribuyentes       → crea contribuyente + credencial SOL CIFRADA
                                 (Fernet) + asigna grupo(s). Multi-tenant.
  - POST /contribuyentes/importar → recibe .xlsx (MVP: UI + endpoint listos;
                                 el parseo masivo es fase aparte).

NUNCA se expone la clave SOL: se cifra al entrar y no se devuelve jamás.
"""

from __future__ import annotations

import secrets
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy import select, func

from db import get_session
from models import (
    Contribuyente, CredencialSol, Grupo, ContribuyenteGrupo, EstadoContribuyente,
    EstudioContable, Usuario, RolUsuario, TipoCuenta, PlanComercial,
    EstadoSuscripcion, limites_de,
)
from cifrado import cifrar_clave_sol
from ..auth import hash_clave
from ..core import WHATSAPP_SOPORTE
from ..deps import UsuarioActual, usuario_actual, requiere_escritura

router = APIRouter(tags=["clientes"])


async def autocompletar_ficha(ruc: str) -> dict | None:
    """HOOK: ficha RUC vía API externa (Facturalo, etc.). MVP: sin red → None."""
    return None


def _solo_digitos(valor: str) -> str:
    return "".join(ch for ch in (valor or "") if ch.isdigit())


def construir_invitacion_whatsapp(
    nombre_empresario: str, nombre_estudio: str, ruc: str,
    whatsapp_empresario: str) -> dict:
    """Arma el mensaje de invitación del onboarding viral (zAlerta-06 C.3).

    Devuelve:
      - wa_url: link wa.me AL EMPRESARIO con el texto de invitación pre-armado
        (lo abre el contador para enviárselo).
      - El texto de invitación CONTIENE el link wa.me al SOPORTE, donde el
        empresario pide su clave (invierte el sentido del contacto).
    """
    solicitud = (f"Hola, a indicación de {nombre_estudio} solicito mi clave de "
                 f"acceso a alerta.pe. Mi nombre es {nombre_empresario} y mi "
                 f"RUC {ruc}.")
    soporte_link = f"https://wa.me/{WHATSAPP_SOPORTE}?text={quote(solicitud)}"

    invitacion = (
        f"Estimado/a {nombre_empresario}:\n"
        f"Como parte de nuestros servicios profesionales, le otorgamos acceso "
        f"exclusivo a su información tributaria, las 24 horas, sin costo.\n"
        f"Para activar su clave personal, escríbanos por WhatsApp aquí:\n"
        f"{soporte_link}")

    wa_url = f"https://wa.me/{whatsapp_empresario}?text={quote(invitacion)}"
    return {"wa_url": wa_url, "mensaje_invitacion": invitacion,
            "soporte_link": soporte_link}


@router.get("/api/grupos")
async def api_grupos(user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        grupos = (await session.scalars(
            select(Grupo).where(Grupo.estudio_id == user.estudio_id)
            .order_by(Grupo.orden, Grupo.nombre))).all()
    return JSONResponse([
        {"id": str(g.id), "nombre": g.nombre, "color": g.color or "#5B8DEF"}
        for g in grupos])


async def _crear_cuenta_empresario(
    session, estudio_actual: EstudioContable, nombre_empresario: str,
    whatsapp: str) -> EstudioContable:
    """Crea (o reutiliza) la cuenta-empresario GRATIS dueña del RUC.

    Si ya existe un usuario con ese WhatsApp, reutiliza su organización
    (evita cuentas duplicadas y login ambiguo). Si no, crea una organización
    tipo empresario (plan gratis) + su usuario (login por WhatsApp, clave
    pendiente de entregar por Soporte).
    """
    usuario_existente = await session.scalar(
        select(Usuario).where(Usuario.whatsapp == whatsapp))
    if usuario_existente:
        return await session.get(EstudioContable, usuario_existente.estudio_id)

    lim = limites_de(PlanComercial.CLIENTE_DE_ESTUDIO.value)
    cuenta_emp = EstudioContable(
        razon_social=nombre_empresario,
        tipo_cuenta=TipoCuenta.EMPRESARIO.value,
        plan=PlanComercial.CLIENTE_DE_ESTUDIO.value,
        max_contribuyentes=lim["max_contribuyentes"],
        max_usuarios=lim["max_usuarios"],
        # Gratis mientras sea cliente del estudio: activa, no expira.
        estado_suscripcion=EstadoSuscripcion.ACTIVA.value,
        whatsapp=whatsapp,
        creado_por_estudio_id=estudio_actual.id,
    )
    session.add(cuenta_emp)
    await session.flush()

    # Usuario del empresario: login por WhatsApp, clave aleatoria pendiente
    # (Soporte la entrega manualmente, zAlerta-06 C.4). Rol asistente = solo
    # lectura; el scope al RUC lo da cuenta_empresario_id.
    session.add(Usuario(
        estudio_id=cuenta_emp.id, nombre=nombre_empresario, dni=None,
        whatsapp=whatsapp, access_code=hash_clave(secrets.token_urlsafe(24)),
        rol=RolUsuario.ASISTENTE, debe_cambiar_clave=True, clave_pendiente=True))
    return cuenta_emp


@router.post("/contribuyentes")
async def crear_contribuyente(
    request: Request, user: UsuarioActual = Depends(requiere_escritura)):
    data = await request.json()
    ruc = (data.get("ruc") or "").strip()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    razon_social = (data.get("razon_social") or "").strip() or None
    grupos_ids = data.get("grupos") or []
    # Datos del EMPRESARIO dueño (obligatorios: la cuenta del empresario lo es).
    emp_nombre = (data.get("empresario_nombre") or "").strip()
    emp_whatsapp = _solo_digitos(data.get("empresario_whatsapp") or "")

    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse({"ok": False, "error": "RUC inválido (11 dígitos)."}, status_code=400)
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Usuario y clave SOL son obligatorios."}, status_code=400)
    if not emp_nombre:
        return JSONResponse({"ok": False, "error": "El nombre del empresario es obligatorio."}, status_code=400)
    if not (emp_whatsapp.isdigit() and 9 <= len(emp_whatsapp) <= 15):
        return JSONResponse({"ok": False,
            "error": "WhatsApp del empresario inválido (incluye código país, ej. 51XXXXXXXXX)."},
            status_code=400)

    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)

        # ── Límite de plan (zAlerta-06 B.2) ──
        n_actual = await session.scalar(
            select(func.count(Contribuyente.id)).where(
                Contribuyente.estudio_id == user.estudio_id)) or 0
        if estudio and n_actual >= estudio.max_contribuyentes:
            return JSONResponse({
                "ok": False, "limite": True,
                "error": (f"Alcanzaste el límite de tu plan "
                          f"({estudio.max_contribuyentes} RUCs). Sube de plan "
                          f"para agregar más."),
            }, status_code=409)

        # Unicidad de RUC dentro del estudio
        existe = await session.scalar(
            select(Contribuyente.id).where(
                Contribuyente.estudio_id == user.estudio_id,
                Contribuyente.ruc == ruc))
        if existe:
            return JSONResponse({"ok": False, "error": "Ese RUC ya está registrado."}, status_code=409)

        # Autocompletar ficha si hay hook disponible
        ficha = await autocompletar_ficha(ruc)
        if ficha and not razon_social:
            razon_social = ficha.get("razon_social")

        contrib = Contribuyente(
            estudio_id=user.estudio_id, ruc=ruc, razon_social=razon_social,
            estado=EstadoContribuyente.ACTIVO)
        if ficha:
            contrib.estado_sunat = ficha.get("estado")
            contrib.condicion_sunat = ficha.get("condicion")
            contrib.domicilio_fiscal = ficha.get("domicilio")
        session.add(contrib)
        await session.flush()

        # Credencial SOL — clave CIFRADA (Fernet)
        session.add(CredencialSol(
            contribuyente_id=contrib.id, estudio_id=user.estudio_id,
            usuario_sol=usuario_sol, clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            tipo_usuario=2, quien_cargo=user.id, valida=True))

        # ── Cuenta del empresario (onboarding viral, zAlerta-06 C.2) ──
        cuenta_emp = await _crear_cuenta_empresario(
            session, estudio, emp_nombre, emp_whatsapp)
        contrib.cuenta_empresario_id = cuenta_emp.id

        # Asignar a grupo(s) válidos del estudio
        for gid in grupos_ids:
            try:
                gid_u = uuid.UUID(str(gid))
            except ValueError:
                continue
            g = await session.scalar(
                select(Grupo.id).where(Grupo.id == gid_u,
                                       Grupo.estudio_id == user.estudio_id))
            if g:
                session.add(ContribuyenteGrupo(
                    contribuyente_id=contrib.id, grupo_id=gid_u,
                    estudio_id=user.estudio_id))

        await session.commit()

        # Armar la invitación de WhatsApp (lo muestra el front como botón).
        invitacion = construir_invitacion_whatsapp(
            nombre_empresario=emp_nombre,
            nombre_estudio=(estudio.razon_social if estudio else "tu contador"),
            ruc=ruc, whatsapp_empresario=emp_whatsapp)

        return JSONResponse({
            "ok": True, "id": str(contrib.id),
            "empresario": {"nombre": emp_nombre, "whatsapp": emp_whatsapp},
            **invitacion,
        })


@router.post("/contribuyentes/importar")
async def importar_excel(
    archivo: UploadFile = File(...),
    user: UsuarioActual = Depends(requiere_escritura)):
    """MVP: confirma recepción. El parseo masivo .xlsx es una fase aparte."""
    contenido = await archivo.read()
    # HOOK: parsear con openpyxl columnas RUC/usuario/clave/grupo y crear en lote.
    return JSONResponse({
        "ok": True,
        "mensaje": (f"Archivo «{archivo.filename}» recibido "
                    f"({len(contenido)} bytes). El procesamiento masivo se "
                    f"habilitará en la siguiente fase."),
    })
