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

import csv
import io
import logging
import os
import secrets
import uuid
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, func

from db import get_session
from models import (
    Contribuyente, CredencialSol, Grupo, ContribuyenteGrupo, EstadoContribuyente,
    EstudioContable, Usuario, RolUsuario, TipoCuenta, PlanComercial,
    EstadoSuscripcion, RucCache, SolicitudValidacionCredencial, EstadoValidacion,
    ahora_lima, limites_de,
)
from cifrado import cifrar_clave_sol
from ..auth import hash_clave
from ..core import WHATSAPP_SOPORTE, templates
from ..deps import UsuarioActual, usuario_actual, requiere_escritura

logger = logging.getLogger("alertape.clientes")

router = APIRouter(tags=["clientes"])

# API RUC pública del ecosistema (apis.net.pe). Token por env (no en el repo).
APIS_NET_PE_TOKEN = os.getenv("APIS_NET_PE_TOKEN", "")
APIS_NET_PE_URL = "https://api.apis.net.pe/v2/sunat/ruc"


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


async def consultar_ruc_api(session, ruc: str, timeout: float = 8.0) -> dict:
    """Trae {ruc, razon_social, estado} para un RUC (zAlerta-10 D).

    1) Mira la caché local (tabla ruc_cache, padrón incremental propio).
    2) Si no está, consulta la API pública (apis.net.pe) con el token de env
       y guarda el resultado en la caché para no repetir llamadas.

    Devuelve siempre un dict {ruc, razon_social, estado, origen}. Si la API no
    devuelve datos, razon_social = None (la UI deja editar a mano: estado ⚠️).
    Nunca lanza: ante error de red devuelve razon_social None (no bloquea).

    `timeout` permite un corte corto (BUG 2 zAlerta-11b): en /activar usamos
    3-4s para no hacer esperar al empresario si la API RUC está caída.
    """
    ruc = (ruc or "").strip()
    # 1) Caché
    cache = await session.get(RucCache, ruc)
    if cache:
        return {"ruc": ruc, "razon_social": cache.razon_social,
                "estado": cache.estado_sunat, "origen": "cache"}

    # 2) API externa
    razon_social = estado = None
    if APIS_NET_PE_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=timeout) as cli:
                r = await cli.get(
                    APIS_NET_PE_URL,
                    params={"numero": ruc},
                    headers={"Authorization": f"Bearer {APIS_NET_PE_TOKEN}",
                             "Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                razon_social = (data.get("razonSocial")
                                or data.get("nombre") or None)
                estado = data.get("estado") or None
        except Exception as e:
            logger.warning("API RUC falló para %s (sigo): %s", ruc, e)

    # Guardar en caché SOLO si la API devolvió razón social (no cachear vacíos:
    # un fallo de red transitorio no debe envenenar la caché).
    if razon_social:
        try:
            session.add(RucCache(ruc=ruc, razon_social=razon_social,
                                 estado_sunat=estado))
            await session.commit()
        except Exception:
            await session.rollback()
    return {"ruc": ruc, "razon_social": razon_social,
            "estado": estado, "origen": "api"}


@router.get("/api/ruc/{ruc}")
async def api_ruc(ruc: str, user: UsuarioActual = Depends(usuario_actual)):
    """Proxy interno a la API RUC (no expone el token al front). Además avisa
    si el RUC YA está en el estudio (dedup de Fase 1, zAlerta-10 B)."""
    ruc = (ruc or "").strip()
    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse(
            {"ok": False, "error": "RUC inválido (deben ser 11 dígitos)."},
            status_code=400)
    async with get_session() as session:
        ya_existe = await session.scalar(
            select(Contribuyente.id).where(
                Contribuyente.estudio_id == user.estudio_id,
                Contribuyente.ruc == ruc))
        ficha = await consultar_ruc_api(session, ruc)
    return JSONResponse({
        "ok": True,
        "ruc": ruc,
        "razon_social": ficha["razon_social"],
        "estado": ficha["estado"],
        "ya_registrado": bool(ya_existe),
    })


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


# ═════════════════════════════════════════════════════════════════════
# zAlerta-10 — Alta en DOS FASES
# ═════════════════════════════════════════════════════════════════════
@router.get("/clientes/nuevo", response_class=HTMLResponse)
async def alta_clientes(
    request: Request, grupo: str = "",
    user: UsuarioActual = Depends(usuario_actual)):
    """Asistente de alta en 2 fases (Parte A: acceso OBVIO y propio)."""
    if user.solo_lectura:
        return RedirectResponse("/", status_code=303)
    async with get_session() as session:
        grupos = (await session.scalars(
            select(Grupo).where(Grupo.estudio_id == user.estudio_id)
            .order_by(Grupo.orden, Grupo.nombre))).all()
        estudio = await session.get(EstudioContable, user.estudio_id)
        n_actual = await session.scalar(
            select(func.count(Contribuyente.id)).where(
                Contribuyente.estudio_id == user.estudio_id)) or 0
    grupo_pre = grupo if grupo else None
    return templates.TemplateResponse(request, "alta_clientes.html", {
        "user": user,
        "grupos": [{"id": str(g.id), "nombre": g.nombre,
                    "color": g.color or "#5B8DEF"} for g in grupos],
        "grupo_pre": grupo_pre,
        "max_contribuyentes": estudio.max_contribuyentes if estudio else 0,
        "n_actual": n_actual,
        "restantes": max(0, (estudio.max_contribuyentes if estudio else 0) - n_actual),
    })


async def _crear_uno(session, user, estudio, fila: dict, restantes: int) -> dict:
    """Crea UN contribuyente desde una fila del lote. Devuelve su resultado.

    `restantes` = cupos del plan que aún quedan ANTES de esta fila. No commitea:
    el llamador hace un commit por fila (partial success seguro).
    """
    ruc = (fila.get("ruc") or "").strip()
    usuario_sol = (fila.get("usuario_sol") or "").strip()
    clave_sol = fila.get("clave_sol") or ""
    razon_social = (fila.get("razon_social") or "").strip() or None
    grupos_ids = fila.get("grupos") or []
    emp_nombre = (fila.get("empresario_nombre") or "").strip()
    emp_whatsapp = _solo_digitos(fila.get("empresario_whatsapp") or "")

    base = {"ruc": ruc, "razon_social": razon_social}

    if not (ruc.isdigit() and len(ruc) == 11):
        return {**base, "ok": False, "estado": "error",
                "error": "RUC inválido (11 dígitos)."}
    if not usuario_sol or not clave_sol:
        return {**base, "ok": False, "estado": "incompleto",
                "error": "Falta usuario o clave SOL."}
    if restantes <= 0:
        return {**base, "ok": False, "estado": "limite",
                "error": "Alcanzaste el límite de tu plan."}

    # Dedup dentro del estudio
    existe = await session.scalar(
        select(Contribuyente.id).where(
            Contribuyente.estudio_id == user.estudio_id,
            Contribuyente.ruc == ruc))
    if existe:
        return {**base, "ok": False, "estado": "duplicado",
                "error": "Ese RUC ya está registrado."}

    # Razón social: si no vino, intentar la API RUC (no bloquea si falla).
    if not razon_social:
        ficha = await consultar_ruc_api(session, ruc)
        razon_social = ficha.get("razon_social")

    contrib = Contribuyente(
        estudio_id=user.estudio_id, ruc=ruc, razon_social=razon_social,
        estado=EstadoContribuyente.ACTIVO)
    session.add(contrib)
    await session.flush()

    session.add(CredencialSol(
        contribuyente_id=contrib.id, estudio_id=user.estudio_id,
        usuario_sol=usuario_sol, clave_sol_cifrada=cifrar_clave_sol(clave_sol),
        tipo_usuario=2, quien_cargo=user.id, valida=True))

    # Cuenta-empresario gratis (onboarding viral) SOLO si dieron WhatsApp válido.
    invitacion = None
    if emp_nombre and emp_whatsapp.isdigit() and 9 <= len(emp_whatsapp) <= 15:
        cuenta_emp = await _crear_cuenta_empresario(
            session, estudio, emp_nombre, emp_whatsapp)
        contrib.cuenta_empresario_id = cuenta_emp.id
        invitacion = construir_invitacion_whatsapp(
            nombre_empresario=emp_nombre,
            nombre_estudio=(estudio.razon_social if estudio else "tu contador"),
            ruc=ruc, whatsapp_empresario=emp_whatsapp)

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

    return {**base, "ok": True, "estado": "creado", "id": str(contrib.id),
            "razon_social": razon_social,
            "empresario": ({"nombre": emp_nombre, "whatsapp": emp_whatsapp}
                           if invitacion else None),
            **({"wa_url": invitacion["wa_url"]} if invitacion else {})}


@router.post("/clientes/alta")
async def guardar_alta(
    request: Request, user: UsuarioActual = Depends(requiere_escritura)):
    """Guarda el LOTE de la Fase 2: crea contribuyentes + credenciales cifradas
    + cuentas-empresario gratis, respetando el límite del plan. Partial success:
    las filas buenas se crean aunque otras fallen (zAlerta-10 filosofía)."""
    data = await request.json()
    filas = data.get("clientes") or []
    if not isinstance(filas, list) or not filas:
        return JSONResponse({"ok": False, "error": "No hay clientes que guardar."},
                            status_code=400)

    resultados, creados = [], 0
    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)
        n_actual = await session.scalar(
            select(func.count(Contribuyente.id)).where(
                Contribuyente.estudio_id == user.estudio_id)) or 0
        tope = estudio.max_contribuyentes if estudio else 0

        for fila in filas:
            restantes = tope - (n_actual + creados)
            try:
                res = await _crear_uno(session, user, estudio, fila, restantes)
                if res["ok"]:
                    await session.commit()
                    creados += 1
                else:
                    await session.rollback()
            except Exception as e:
                await session.rollback()
                logger.exception("Error creando %s", fila.get("ruc"))
                res = {"ruc": (fila.get("ruc") or "").strip(), "ok": False,
                       "estado": "error", "error": "Error al guardar."}
            resultados.append(res)

    limite_alcanzado = any(r["estado"] == "limite" for r in resultados)
    return JSONResponse({
        "ok": True, "creados": creados, "resultados": resultados,
        "limite_alcanzado": limite_alcanzado,
        "mensaje_limite": (
            f"Solo se pudieron crear {creados}: alcanzaste el límite de tu plan "
            f"({tope} RUCs). Sube de plan para agregar más."
            if limite_alcanzado else None),
    })


# ── "Comprobar conexión" (login real vía worker, zAlerta-10 B/NOTA) ──
@router.post("/contribuyentes/validar-credenciales")
async def validar_credenciales(
    request: Request, user: UsuarioActual = Depends(requiere_escritura)):
    """Encola una validación de credenciales SOL (login real). La WEB no tiene
    Playwright: cifra la clave, crea la solicitud y el WORKER la procesa
    (login-only). El front hace polling por el id. Multi-tenant; la clave NUNCA
    se loguea ni se devuelve."""
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
            estudio_id=user.estudio_id, ruc=ruc, usuario_sol=usuario_sol,
            clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            estado=EstadoValidacion.PENDIENTE)
        session.add(sol)
        await session.commit()
        sol_id = str(sol.id)

    return JSONResponse({"ok": True, "id": sol_id,
                         "estado": EstadoValidacion.PENDIENTE.value})


@router.get("/contribuyentes/validar-credenciales/{solicitud_id}")
async def estado_validacion(
    solicitud_id: uuid.UUID, user: UsuarioActual = Depends(usuario_actual)):
    """Polling del resultado de 'Comprobar conexión'. Multi-tenant."""
    async with get_session() as session:
        sol = await session.scalar(
            select(SolicitudValidacionCredencial).where(
                SolicitudValidacionCredencial.id == solicitud_id,
                SolicitudValidacionCredencial.estudio_id == user.estudio_id))
        if not sol:
            return JSONResponse({"ok": False}, status_code=404)
        estado = sol.estado.value if hasattr(sol.estado, "value") else sol.estado
    listo = estado in ("conecta", "no_conecta", "error")
    return JSONResponse({"ok": True, "estado": estado,
                         "conecta": estado == "conecta", "listo": listo})


# ── Carga por archivo (Parte C): Excel / TXT / CSV → filas para la Fase 1 ──
def _norm_encabezado(s: str) -> str:
    s = (s or "").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(a, b)
    return s.replace(" ", "_")


# Mapas de encabezado flexible → campo interno.
_COLS_RUC = {"ruc", "numero", "documento", "nro_ruc"}
_COLS_RS = {"razon_social", "razon", "razonsocial", "nombre", "nombre_o_razon_social"}
_COLS_USER = {"usuario", "usuario_sol", "user", "sol_usuario", "clave_sol_usuario"}
_COLS_CLAVE = {"clave", "clave_sol", "password", "contrasena", "sol_clave"}


def _fila_desde_celdas(celdas: list[str], mapa: dict | None) -> dict | None:
    """Construye {ruc, razon_social, usuario_sol, clave_sol} desde una fila.

    Si `mapa` (encabezado→índice) viene, usa columnas nombradas; si no, asume
    el orden RUC[,razón social][,usuario][,clave]. Devuelve None si no hay RUC.
    """
    def _val(idx):
        return (celdas[idx].strip() if idx is not None and idx < len(celdas)
                and celdas[idx] is not None else "")
    if mapa:
        ruc = _solo_digitos(_val(mapa.get("ruc")))
        fila = {"ruc": ruc, "razon_social": _val(mapa.get("razon_social")),
                "usuario_sol": _val(mapa.get("usuario_sol")),
                "clave_sol": _val(mapa.get("clave_sol"))}
    else:
        c = [x.strip() if x is not None else "" for x in celdas]
        ruc = _solo_digitos(c[0]) if c else ""
        fila = {"ruc": ruc,
                "razon_social": c[1] if len(c) > 1 else "",
                "usuario_sol": c[2] if len(c) > 2 else "",
                "clave_sol": c[3] if len(c) > 3 else ""}
    return fila if fila["ruc"] else None


def _detectar_mapa(encabezados: list[str]) -> dict | None:
    """Si la primera fila parece encabezado (tiene 'ruc' o similar), devuelve
    {campo: indice}. Si no parece encabezado, devuelve None (orden posicional)."""
    norm = [_norm_encabezado(h) for h in encabezados]
    if not any(n in _COLS_RUC for n in norm):
        return None
    mapa = {}
    for i, n in enumerate(norm):
        if n in _COLS_RUC and "ruc" not in mapa:
            mapa["ruc"] = i
        elif n in _COLS_RS and "razon_social" not in mapa:
            mapa["razon_social"] = i
        elif n in _COLS_USER and "usuario_sol" not in mapa:
            mapa["usuario_sol"] = i
        elif n in _COLS_CLAVE and "clave_sol" not in mapa:
            mapa["clave_sol"] = i
    return mapa


def _parsear_txt_csv(contenido: bytes) -> list[list[str]]:
    texto = contenido.decode("utf-8-sig", errors="replace")
    # Delimitador flexible: coma, punto y coma o tab.
    muestra = texto[:2000]
    delim = ";" if muestra.count(";") > muestra.count(",") else ","
    if muestra.count("\t") > muestra.count(delim):
        delim = "\t"
    filas = []
    for fila in csv.reader(io.StringIO(texto), delimiter=delim):
        if any((c or "").strip() for c in fila):
            filas.append(fila)
    return filas


def _parsear_xlsx(contenido: bytes) -> list[list[str]]:
    try:
        import openpyxl  # lazy: solo si suben un .xlsx
    except ImportError:
        raise RuntimeError(
            "Para leer Excel (.xlsx) falta la librería openpyxl. "
            "Sube el archivo como TXT/CSV o instala openpyxl.")
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    ws = wb.active
    filas = []
    for fila in ws.iter_rows(values_only=True):
        celdas = ["" if v is None else str(v) for v in fila]
        if any(c.strip() for c in celdas):
            filas.append(celdas)
    wb.close()
    return filas


@router.post("/clientes/importar-archivo")
async def importar_archivo(
    archivo: UploadFile = File(...),
    user: UsuarioActual = Depends(requiere_escritura)):
    """Parsea Excel/TXT/CSV y devuelve filas para PRECARGAR la tabla de Fase 1
    (Parte C). NO crea nada: el usuario revisa y corrige antes de continuar.
    Archivos mal formados no rompen: se reportan filas problemáticas."""
    contenido = await archivo.read()
    nombre = (archivo.filename or "").lower()
    try:
        if nombre.endswith((".xlsx", ".xls")):
            crudas = _parsear_xlsx(contenido)
        else:
            crudas = _parsear_txt_csv(contenido)
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.warning("Archivo ilegible (%s): %s", archivo.filename, e)
        return JSONResponse(
            {"ok": False, "error": "No se pudo leer el archivo. Revisa el formato."},
            status_code=400)

    if not crudas:
        return JSONResponse({"ok": False, "error": "El archivo está vacío."},
                            status_code=400)

    mapa = _detectar_mapa(crudas[0])
    cuerpo = crudas[1:] if mapa else crudas

    filas, problematicas, vistos = [], 0, set()
    for celdas in cuerpo:
        f = _fila_desde_celdas(celdas, mapa)
        if not f:
            problematicas += 1
            continue
        ruc_ok = f["ruc"].isdigit() and len(f["ruc"]) == 11
        if f["ruc"] in vistos:
            continue  # dedup dentro del archivo
        vistos.add(f["ruc"])
        filas.append({**f, "ruc_valido": ruc_ok})

    return JSONResponse({
        "ok": True,
        "filas": filas,
        "total": len(filas),
        "problematicas": problematicas,
        "mensaje": (f"{len(filas)} RUC(s) leído(s)"
                    + (f", {problematicas} fila(s) sin RUC ignorada(s)"
                       if problematicas else "") + "."),
    })
