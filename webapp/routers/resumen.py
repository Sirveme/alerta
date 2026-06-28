"""
webapp/routers/resumen.py — alerta.pe (zAlerta-12 P1)
═══════════════════════════════════════════════════════════════════════
Resumen del Buzón SUNAT del usuario logueado, pensado para el flujo de push:

  - GET /resumen          → página con la TABLA resumen (offline tras 1ª entrada).
  - GET /api/resumen      → JSON multi-tenant del resumen (lo cachea IndexedDB).
  - POST /api/alerta/vista → registra la lectura (botón GRACIAS del push, métrica).

Multi-tenant SIEMPRE: el estudio ve sus contribuyentes; el empresario, solo el(los)
RUC vinculado(s) por cuenta_empresario_id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import uuid

from db import get_session
from models import (
    Contribuyente, CredencialSol, Notificacion, EstadoContribuyente,
    ETIQUETA_TIPO_DOCUMENTO, ahora_lima, Usuario,
    Recordatorio, ModoRecordatorio,
    SolicitudValidacionCredencial, EstadoValidacion,
)
from cifrado import cifrar_clave_sol
from ..core import templates, fecha_lima
from ..deps import UsuarioActual, usuario_actual
from ..estados import estado_conexion

router = APIRouter(tags=["resumen"])


def _periodo_de(asunto: str | None) -> str:
    """Heurística suave: si el asunto trae un periodo evidente, mostrarlo; si no,
    '—' (no inventar). Mantener simple y prudente."""
    return "—"


@router.get("/resumen", response_class=HTMLResponse)
async def resumen_page(request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    # Estado de conexión HONESTO por RUC (zAlerta-18), visible siempre.
    conexiones = []
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        contribs = list(await session.scalars(
            select(Contribuyente).where(cond)
            .order_by(Contribuyente.razon_social).limit(20)))
        for ct in contribs:
            cred = await session.scalar(
                select(CredencialSol).where(
                    CredencialSol.contribuyente_id == ct.id))
            cx = estado_conexion(ct, cred)
            cx["ruc"] = ct.ruc
            cx["razon_social"] = ct.razon_social or ct.ruc
            # zAlerta-27: primera lectura en curso — credencial válida, conexión
            # sana y aún sin scrapeo (ultimo_scrapeo_at NULL). El worker la trae
            # en su próxima pasada; mostramos un aviso suave mientras tanto.
            cx["primera_lectura"] = bool(
                ct.ultimo_scrapeo_at is None and cred and cred.valida
                and cx.get("clave") not in ("error", "pendiente"))
            conexiones.append(cx)
    return templates.TemplateResponse(request, "resumen.html", {
        "user": user, "conexiones": conexiones})


@router.get("/api/resumen")
async def api_resumen(user: UsuarioActual = Depends(usuario_actual)):
    """Resumen JSON del buzón del usuario (lo que la tabla offline cachea)."""
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        sub = select(Contribuyente.id).where(cond)
        rows = (await session.execute(
            select(Notificacion, Contribuyente.ruc, Contribuyente.razon_social)
            .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
            .options(selectinload(Notificacion.adjuntos))
            .where(Notificacion.contribuyente_id.in_(sub))
            .order_by(Notificacion.fecha_publica_sunat.desc().nullslast(),
                      Notificacion.creado_at.desc())
            .limit(40))).all()
        # Recordatorios activos del usuario (notif_id → modo) para pintar estado.
        recs = {str(nid): (modo.value if hasattr(modo, "value") else modo)
                for nid, modo in (await session.execute(
                    select(Recordatorio.notificacion_id, Recordatorio.modo)
                    .where(Recordatorio.usuario_id == user.id,
                           Recordatorio.activo.is_(True)))).all()}

    filas = []
    for n, ruc, razon in rows:
        tipo_enum = (n.tipo_documento_enum.value
                     if n.tipo_documento_enum is not None else "otro")
        documento = (n.tipo_documento
                     or ETIQUETA_TIPO_DOCUMENTO.get(tipo_enum, "Aviso"))
        # Adjuntos PDF disponibles (solo los que tienen archivo servible en BD).
        # Se mandan id+nombre; el PDF se sirve bajo demanda por /adjuntos/{id}/ver.
        adjuntos = [{"id": str(a.id), "nombre": a.nombre_archivo}
                    for a in (n.adjuntos or [])
                    if a.bytea_temporal is not None or a.gcs_key]
        filas.append({
            "id": str(n.id),
            "documento": documento,
            "tipo": tipo_enum,
            "periodo": _periodo_de(n.asunto),
            "detalle": (n.asunto or "—")[:160],
            "asunto": n.asunto or "",            # asunto completo para el modal
            "cod_mensaje": n.cod_mensaje_sunat,  # referencia SUNAT
            "adjuntos": adjuntos,                # [{id, nombre}] PDF servibles
            "vence": fecha_lima(n.plazo_vencimiento) if n.plazo_vencimiento else "—",
            "vence_iso": (n.plazo_vencimiento.isoformat()
                          if n.plazo_vencimiento else None),
            "recordatorio": recs.get(str(n.id)),   # modo activo o None
            "urgencia": n.urgencia.value if hasattr(n.urgencia, "value") else "sin_clasificar",
            "ruc": ruc,
            "razon_social": razon or ruc,
            "leida": bool(n.leida),
        })

    return JSONResponse({
        "ok": True,
        "generado_at": ahora_lima().isoformat(),
        "total": len(filas),
        "filas": filas,
    })


@router.post("/contribuyentes/{contribuyente_id}/desconectar")
async def desconectar_ruc(
    contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(usuario_actual)):
    """Derecho de corte (zAlerta-12 P3): el dueño desconecta SU RUC. Pausa el
    monitoreo (estado INACTIVO) y borra la credencial SOL cifrada — alerta.pe
    deja de acceder. Scoped: el empresario solo su propio RUC; el estudio los
    suyos. El empresario es solo-lectura para todo lo demás, pero SIEMPRE puede
    cortar el acceso a su información."""
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        contrib = await session.scalar(
            select(Contribuyente).where(
                Contribuyente.id == contribuyente_id, cond))
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        contrib.estado = EstadoContribuyente.INACTIVO
        # Borrar la credencial SOL cifrada: cortar el acceso de raíz.
        cred = await session.scalar(
            select(CredencialSol).where(
                CredencialSol.contribuyente_id == contrib.id))
        if cred:
            await session.delete(cred)
        await session.commit()
    return JSONResponse({"ok": True})


_MODOS_VALIDOS = {m.value for m in ModoRecordatorio}


@router.post("/api/recordatorio")
async def api_recordatorio(request: Request,
                           user: UsuarioActual = Depends(usuario_actual)):
    """"Recuérdame esto" (zAlerta-13 P1): activa/cambia/desactiva el recordatorio
    de una notificación para el usuario. modo=None → desactiva. Solo notificaciones
    de los contribuyentes accesibles por el usuario (multi-tenant)."""
    data = await request.json()
    try:
        notif_id = uuid.UUID(str(data.get("notificacion_id")))
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "Notificación inválida."}, status_code=400)
    modo = (data.get("modo") or "").strip() or None
    if modo and modo not in _MODOS_VALIDOS:
        return JSONResponse({"ok": False, "error": "Modo inválido."}, status_code=400)

    async with get_session() as session:
        # Verificar que la notificación es accesible por el usuario.
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        notif = await session.scalar(
            select(Notificacion)
            .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
            .where(Notificacion.id == notif_id, cond))
        if not notif:
            return JSONResponse({"ok": False}, status_code=404)

        rec = await session.scalar(
            select(Recordatorio).where(
                Recordatorio.notificacion_id == notif_id,
                Recordatorio.usuario_id == user.id))
        if modo is None:
            if rec:
                rec.activo = False
                await session.commit()
            return JSONResponse({"ok": True, "recordatorio": None})
        if rec:
            rec.modo = ModoRecordatorio(modo)
            rec.activo = True
            rec.fecha_vencimiento = notif.plazo_vencimiento
        else:
            session.add(Recordatorio(
                estudio_id=notif.estudio_id, notificacion_id=notif_id,
                usuario_id=user.id, modo=ModoRecordatorio(modo), activo=True,
                fecha_vencimiento=notif.plazo_vencimiento))
        await session.commit()
    return JSONResponse({"ok": True, "recordatorio": modo})


async def _contrib_propio(session, user: UsuarioActual, contribuyente_id):
    """Devuelve el contribuyente si el usuario es su dueño/vigilante, o None."""
    if user.es_empresario:
        cond = Contribuyente.cuenta_empresario_id == user.estudio_id
    else:
        cond = Contribuyente.estudio_id == user.estudio_id
    return await session.scalar(
        select(Contribuyente).where(Contribuyente.id == contribuyente_id, cond))


@router.post("/contribuyentes/{contribuyente_id}/cred/validar")
async def cred_validar(contribuyente_id: uuid.UUID, request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    """Actualizar credenciales SOL (zAlerta-13 P2) · paso 1: VALIDAR sin guardar.
    Encola un login-only (flag→worker→resultado) con las credenciales NUEVAS. NO
    toca la credencial actual: así no dejamos al usuario sin nada si fallan."""
    data = await request.json()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Ingresa usuario y clave SOL."},
                            status_code=400)
    async with get_session() as session:
        contrib = await _contrib_propio(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        sol = SolicitudValidacionCredencial(
            estudio_id=user.estudio_id, ruc=contrib.ruc, usuario_sol=usuario_sol,
            clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            estado=EstadoValidacion.PENDIENTE)
        session.add(sol)
        await session.commit()
        sol_id = str(sol.id)
    return JSONResponse({"ok": True, "id": sol_id})


@router.post("/contribuyentes/{contribuyente_id}/cred/guardar")
async def cred_guardar(contribuyente_id: uuid.UUID, request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    """Actualizar credenciales SOL · paso 2: GUARDAR (tras confirmar que conecta).
    Reemplaza la credencial (Fernet), reactiva el contribuyente si estaba en
    ERROR_CREDENCIAL y limpia el aviso. La clave nunca se devuelve ni se loguea."""
    data = await request.json()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Ingresa usuario y clave SOL."},
                            status_code=400)
    async with get_session() as session:
        contrib = await _contrib_propio(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        cred = await session.scalar(
            select(CredencialSol).where(
                CredencialSol.contribuyente_id == contrib.id))
        if cred:
            cred.usuario_sol = usuario_sol
            cred.clave_sol_cifrada = cifrar_clave_sol(clave_sol)
            cred.valida = True
            cred.ultimo_login_ok_at = ahora_lima()
        else:
            session.add(CredencialSol(
                contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
                usuario_sol=usuario_sol,
                clave_sol_cifrada=cifrar_clave_sol(clave_sol),
                tipo_usuario=2, quien_cargo=user.id, valida=True,
                ultimo_login_ok_at=ahora_lima()))
        if contrib.estado == EstadoContribuyente.ERROR_CREDENCIAL:
            contrib.estado = EstadoContribuyente.ACTIVO
        contrib.credencial_error_avisada = False
        # Primera lectura inmediata (zAlerta-27): guardar una credencial válida
        # (alta o reconexión) encola una lectura fresca en la cola diurna del
        # botón "Actualizar ahora"; el worker la procesa y limpia el flag.
        contrib.actualizar_solicitado = True
        contrib.actualizar_solicitado_at = ahora_lima()
        await session.commit()
    return JSONResponse({"ok": True})


@router.post("/api/alerta/vista")
async def api_alerta_vista(user: UsuarioActual = Depends(usuario_actual)):
    """Registra que el usuario confirmó la lectura del push (botón GRACIAS).
    Métrica sutil; no obligatorio. Solo sella la fecha en el usuario."""
    async with get_session() as session:
        u = await session.get(Usuario, user.id)
        if u:
            u.ultima_alerta_vista_at = ahora_lima()
            await session.commit()
    return JSONResponse({"ok": True})
