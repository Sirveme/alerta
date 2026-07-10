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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from db import get_session
import uuid

from sqlalchemy import select

from models import (
    EstudioContable, Usuario, Contribuyente, CredencialSol,
    EstadoContribuyente, RolUsuario, TipoCuenta, PlanComercial,
    EstadoSuscripcion, SolicitudValidacionCredencial, EstadoValidacion,
    LeadActivacion, limites_de, ahora_lima,
)
from cifrado import cifrar_clave_sol
from datetime import timedelta
from ..core import templates, WHATSAPP_SOPORTE
from ..auth import (
    hash_clave, crear_token_usuario, set_cookie_sesion,
    leer_sesion, COOKIE_NOMBRE,
)
from ..deps import usuario_actual, UsuarioActual
from ..estados import estado_conexion
from precios import precio_para_fecha
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


def _norm_whatsapp(raw: str | None) -> str | None:
    """Normaliza el WhatsApp del empresario (zAlerta-11bb B): el usuario escribe
    SOLO su número (sin código país); internamente anteponemos 51 (Perú).
    Devuelve None si no parece un número válido."""
    d = "".join(c for c in (raw or "") if c.isdigit())
    if not d:
        return None
    if d.startswith("51") and len(d) >= 11:
        return d[:15]
    if 8 <= len(d) <= 9:           # número local peruano (9 dígitos, móvil)
        return "51" + d
    if 9 <= len(d) <= 15:          # ya traía código país u otro formato largo
        return d
    return None


async def _upsert_lead(session, ruc: str, whatsapp: str | None,
                       razon_social: str | None = None,
                       estado: str = "lead") -> None:
    """Guarda/actualiza el lead (RUC+WhatsApp) sin duplicar. Nunca rompe el
    flujo: si falla, se ignora (un lead perdido no debe tumbar el alta)."""
    try:
        ahora = ahora_lima()
        lead = await session.scalar(
            select(LeadActivacion).where(LeadActivacion.ruc == ruc))
        if lead:
            if whatsapp:
                lead.whatsapp = whatsapp
            if razon_social:
                lead.razon_social = razon_social
            if estado:
                lead.estado = estado
            # Candado: si AÚN no tiene precio congelado, fijarlo ahora; si ya lo
            # tiene, NO sobreescribir (respeta el primer precio capturado).
            if lead.precio_congelado is None:
                lead.precio_congelado = precio_para_fecha(ahora)
                lead.precio_congelado_at = ahora
        else:
            session.add(LeadActivacion(
                ruc=ruc, whatsapp=whatsapp, razon_social=razon_social,
                estado=estado, precio_congelado=precio_para_fecha(ahora),
                precio_congelado_at=ahora))
        await session.commit()
    except Exception:
        await session.rollback()


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


@router.get("/api/activar/ruc/{ruc}")
async def api_ruc_publico(ruc: str):
    """Lookup de RUC PÚBLICO para /activar (sin sesión).

    El `/api/ruc/{ruc}` del panel exige login, así que la página pública de alta
    no podía usarlo. Este endpoint anónimo trae la razón social con un timeout
    CORTO (BUG 2 zAlerta-11b): si la API RUC está caída, devuelve razon_social
    None SIN bloquear — el front deja escribirla a mano. El DNI (RUC 10) se
    calcula offline, no depende de la API.
    """
    ruc = (ruc or "").strip()
    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse(
            {"ok": False, "error": "RUC inválido (11 dígitos)."},
            status_code=400)
    razon_social = None
    try:
        async with get_session() as session:
            ficha = await consultar_ruc_api(session, ruc, timeout=4.0)
            razon_social = ficha.get("razon_social")
    except Exception:
        # Nunca bloquear el alta por un fallo de la API/caché.
        razon_social = None
    return JSONResponse({"ok": True, "ruc": ruc,
                         "razon_social": razon_social,
                         "dni": _dni_desde_ruc(ruc)})


@router.post("/api/activar/lead")
async def api_lead(request: Request):
    """Captura temprana (zAlerta-11bb B): guarda RUC + WhatsApp apenas el
    usuario los escribe, aunque NO termine el alta (lead recuperable)."""
    data = await request.json()
    ruc = (data.get("ruc") or "").strip()
    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse({"ok": False}, status_code=400)
    whatsapp = _norm_whatsapp(data.get("whatsapp"))
    razon_social = (data.get("razon_social") or "").strip() or None
    async with get_session() as session:
        await _upsert_lead(session, ruc, whatsapp, razon_social, estado="lead")
    return JSONResponse({"ok": True})


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
    # BUG 3: la comprobación de conexión es AYUDA, no bloqueo. Si el usuario no
    # la pasó (o no la hizo), igual guardamos: la credencial queda "pendiente de
    # verificar" (sin sello ultimo_login_ok_at) y el worker la confirma en la
    # primera consulta real.
    conexion_verificada = bool(data.get("conexion_verificada"))
    # WhatsApp capturado temprano (zAlerta-11bb B): el usuario escribe SOLO su
    # número; aquí se normaliza anteponiendo 51.
    whatsapp = _norm_whatsapp(data.get("whatsapp"))
    # P3 (zAlerta-12): declaración de responsabilidad OBLIGATORIA.
    responsabilidad = bool(data.get("responsabilidad"))

    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse({"ok": False, "error": "RUC inválido (11 dígitos)."},
                            status_code=400)
    # C (zAlerta-11c): el WhatsApp es obligatorio (es el activo para no perder
    # el lead y avisarle). El front también lo valida; aquí se asegura.
    if not whatsapp:
        return JSONResponse(
            {"ok": False, "error": "Necesitamos tu WhatsApp para avisarte."},
            status_code=400)
    if not responsabilidad:
        return JSONResponse(
            {"ok": False, "error": "Debes aceptar la declaración de "
                                   "responsabilidad para activar."}, status_code=400)
    if tiene_clave and (not usuario_sol or not clave_sol):
        return JSONResponse(
            {"ok": False, "error": "Ingresa tu usuario y clave SOL, o elige "
                                   "«Pido al contador»."}, status_code=400)

    async with get_session() as session:
        # P2 (zAlerta-12): el WhatsApp es el acceso UNIVERSAL → único por cuenta.
        ya = await session.scalar(
            select(Usuario.id).where(Usuario.whatsapp == whatsapp))
        if ya:
            return JSONResponse(
                {"ok": False, "error": "Ese WhatsApp ya tiene una cuenta. "
                                       "Inicia sesión."}, status_code=409)
        # Razón social: completar con la API RUC si no vino del front.
        if not razon_social:
            ficha = await consultar_ruc_api(session, ruc)
            razon_social = ficha.get("razon_social")
        nombre = razon_social or f"Empresario · RUC {ruc}"

        lim = limites_de(PlanComercial.EMPRESARIO.value)
        ahora = ahora_lima()
        # Candado de precio (zAlerta-24): el precio viaja del lead a la cuenta. Si
        # el lead lo tenía congelado (lo más antiguo/barato), se respeta; si no,
        # se fija con el precio del mes de hoy.
        lead = await session.scalar(
            select(LeadActivacion).where(LeadActivacion.ruc == ruc))
        precio_cong = (lead.precio_congelado if lead and lead.precio_congelado
                       else precio_para_fecha(ahora))
        precio_cong_at = (lead.precio_congelado_at
                          if lead and lead.precio_congelado_at else ahora)
        # D (zAlerta-11c): los 7 días de prueba SOLO arrancan cuando hay conexión
        # confirmada. Si no conectó (o pidió al contador), vence_at queda NULL y
        # el worker lo fija al primer login exitoso (prueba diferida = justo).
        conecto_ya = tiene_clave and conexion_verificada
        estudio = EstudioContable(
            razon_social=nombre,
            tipo_cuenta=TipoCuenta.EMPRESARIO.value,
            plan=PlanComercial.EMPRESARIO.value,
            max_contribuyentes=lim["max_contribuyentes"],
            max_usuarios=lim["max_usuarios"],
            estado_suscripcion=EstadoSuscripcion.PRUEBA.value,
            suscripcion_vence_at=(ahora + timedelta(days=DIAS_PRUEBA)
                                  if conecto_ya else None),
            precio_congelado=precio_cong, precio_congelado_at=precio_cong_at,
        )
        session.add(estudio)
        await session.flush()

        # Usuario del empresario. Acceso UNIVERSAL por WhatsApp (P2). Clave
        # inicial de baja fricción = últimos 6 dígitos de su WhatsApp (algo que
        # recuerda); debe_cambiar_clave la cambia en el primer login manual.
        clave_inicial = (whatsapp or "000000")[-6:]
        usuario = Usuario(
            estudio_id=estudio.id, nombre=nombre,
            dni=_dni_desde_ruc(ruc), whatsapp=whatsapp,
            access_code=hash_clave(clave_inicial),
            rol=RolUsuario.ADMIN, cargo=cargo,
            debe_cambiar_clave=True, clave_pendiente=False,
            # P3: evidencia de la declaración de responsabilidad.
            responsabilidad_aceptada_at=ahora, responsabilidad_ruc=ruc)
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
                tipo_usuario=2, quien_cargo=usuario.id,
                # valida=True para que el worker SÍ la scrapee y la verifique en
                # la 1ª consulta (BUG 3). El "verificada vs pendiente" lo marca
                # ultimo_login_ok_at: con sello solo si la comprobación pasó.
                valida=True,
                ultimo_login_ok_at=(ahora if conexion_verificada else None)))
            # Primera lectura inmediata (zAlerta-27): encolar al alta con
            # credencial válida reusando la cola diurna del botón "Actualizar
            # ahora". El worker la procesa siempre (de día, forzar=True) y limpia
            # el flag tras el intento (one-shot). NO toca el motor ni la ventana.
            contrib.actualizar_solicitado = True
            contrib.actualizar_solicitado_at = ahora

        await session.commit()
        await session.refresh(usuario)

        # Cerrar el lead: completó el alta (zAlerta-11bb B).
        await _upsert_lead(session, ruc, whatsapp, razon_social,
                           estado="activado")

    # D (zAlerta-11c): redirigir SIEMPRE a la pantalla de confirmación (no a la
    # landing). La propia /bienvenida decide D.1/D.2/D.3 según el estado real.
    resp = JSONResponse({"ok": True, "redirect": "/bienvenida"})
    set_cookie_sesion(resp, crear_token_usuario(usuario, TipoCuenta.EMPRESARIO.value))
    return resp


# ── D (zAlerta-11c) · Pantalla de bienvenida post-alta (D.1/D.2/D.3) ──
# Horarios fijos de la prueba (el plan Fundadores permitirá variarlos).
HORARIOS_PRUEBA = "8:00 a.m., 12:00 m. y 5:00 p.m."


async def _contrib_empresario(session, user: UsuarioActual):
    """El contribuyente (RUC propio) del empresario logueado, o None."""
    return await session.scalar(
        select(Contribuyente).where(
            Contribuyente.cuenta_empresario_id == user.estudio_id)
        .order_by(Contribuyente.creado_at).limit(1))


@router.get("/bienvenida", response_class=HTMLResponse)
async def bienvenida(request: Request,
                     user: UsuarioActual = Depends(usuario_actual)):
    """Confirmación tras el alta. Decide D.1/D.2/D.3 según el estado real:
      - sin credencial            → contador (D.3)
      - credencial verificada     → conectó (D.1)
      - credencial sin verificar  → pendiente (D.2)
    """
    contrib, cred = None, None
    async with get_session() as session:
        contrib = await _contrib_empresario(session, user)
        if contrib:
            cred = await session.scalar(
                select(CredencialSol).where(
                    CredencialSol.contribuyente_id == contrib.id))
    cx = estado_conexion(contrib, cred)
    # Mapeo a las pantallas existentes (zAlerta-18 alinea D.1/D.2/D.3 con los 3
    # estados honestos): vigilado→conectó, verificando/error→corregir, pendiente→contador.
    estado = {"vigilado": "conecto", "verificando": "pendiente",
              "error": "pendiente", "pendiente": "contador"}[cx["clave"]]
    return templates.TemplateResponse(request, "bienvenida.html", {
        "user": user,
        "estado": estado,
        "conexion": cx,
        "ruc": contrib.ruc if contrib else "",
        "razon_social": (contrib.razon_social if contrib else "") or "",
        "usuario_sol": cred.usuario_sol if cred else "",
        "horarios": HORARIOS_PRUEBA,
        "whatsapp_soporte": WHATSAPP_SOPORTE,
    })


@router.post("/api/bienvenida/credenciales")
async def bienvenida_credenciales(
    request: Request, user: UsuarioActual = Depends(usuario_actual)):
    """D.2: el empresario corrige SUS PROPIAS credenciales y re-dispara la
    validación (login real vía worker). Scoped a su propio RUC (no multi-tenant
    cruzado). El worker, al confirmar, fija ultimo_login_ok_at y arranca la
    prueba (vence_at). La clave nunca se devuelve."""
    data = await request.json()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Ingresa usuario y clave SOL."},
                            status_code=400)
    async with get_session() as session:
        contrib = await _contrib_empresario(session, user)
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        cred = await session.scalar(
            select(CredencialSol).where(
                CredencialSol.contribuyente_id == contrib.id))
        if cred:
            cred.usuario_sol = usuario_sol
            cred.clave_sol_cifrada = cifrar_clave_sol(clave_sol)
            cred.valida = True
            cred.ultimo_login_ok_at = None     # vuelve a "pendiente de verificar"
        else:
            session.add(CredencialSol(
                contribuyente_id=contrib.id, estudio_id=user.estudio_id,
                usuario_sol=usuario_sol,
                clave_sol_cifrada=cifrar_clave_sol(clave_sol),
                tipo_usuario=2, quien_cargo=user.id, valida=True))
        # Reconectar reactiva el RUC (zAlerta-51): venga de ERROR_CREDENCIAL o de
        # INACTIVO, para que vuelva al ciclo automático de vigilancia.
        if contrib.estado in (EstadoContribuyente.ERROR_CREDENCIAL,
                              EstadoContribuyente.INACTIVO):
            contrib.estado = EstadoContribuyente.ACTIVO
        # Primera lectura inmediata (zAlerta-27): reconexión/corrección de clave
        # encola una lectura fresca en la cola diurna existente (one-shot).
        contrib.actualizar_solicitado = True
        contrib.actualizar_solicitado_at = ahora_lima()
        # Encolar la validación (con tenant) para feedback inmediato en la UI.
        sol = SolicitudValidacionCredencial(
            estudio_id=user.estudio_id, ruc=contrib.ruc, usuario_sol=usuario_sol,
            clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            estado=EstadoValidacion.PENDIENTE)
        session.add(sol)
        await session.commit()
        sol_id = str(sol.id)
    return JSONResponse({"ok": True, "id": sol_id})


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
