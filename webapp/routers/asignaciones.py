"""
webapp/routers/asignaciones.py — alerta.pe (Capa 1 · Fase C3)
═══════════════════════════════════════════════════════════════════════
Pantalla del CONTADOR_DUENO para asignar clientes a sus ASISTENTES:
  - por GRUPO completo (AsignacionGrupo, EN VIVO) — el gancho de la demo,
  - por RUC individual (Asignacion) — casos sueltos.
Cada asignación/desasignación escribe `auditoria` (quién asignó qué a quién).

ALCANCE C3: SOLO CREA/QUITA asignaciones. NO cambia todavía qué ve el asistente
(el scope se cablea en C4). Solo destinos estudio_id (nada de contribuyente_id).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, func, delete

from db import get_session
from models import (
    Acceso, Persona, EstudioContable, Contribuyente, Grupo, ContribuyenteGrupo,
    Asignacion, AsignacionGrupo, RolUsuario, ahora_lima,
)
from ..core import templates
from ..deps import usuario_actual, UsuarioActual
from ..auditoria import registrar_auditoria

router = APIRouter(tags=["asignaciones"])


def _solo_dueno(user: UsuarioActual):
    """La pantalla y sus acciones son SOLO del contador dueño."""
    return user.rol == RolUsuario.CONTADOR_DUENO


async def _asistentes_del_estudio(session, estudio_id) -> list:
    """Personas con acceso ASISTENTE vigente a este estudio."""
    hoy = ahora_lima().date()
    rows = (await session.execute(
        select(Persona.id, Persona.nombre_completo)
        .join(Acceso, Acceso.persona_id == Persona.id)
        .where(Acceso.estudio_id == estudio_id,
               Acceso.rol == RolUsuario.ASISTENTE,
               (Acceso.vigencia_fin.is_(None)) | (Acceso.vigencia_fin >= hoy))
        .distinct())).all()
    return [{"id": str(pid), "nombre": nom or "Asistente"} for pid, nom in rows]


async def _asistente_valido(session, estudio_id, asistente_id) -> bool:
    return bool(await session.scalar(
        select(Acceso.id).where(
            Acceso.persona_id == asistente_id, Acceso.estudio_id == estudio_id,
            Acceso.rol == RolUsuario.ASISTENTE)))


async def _asignaciones_de(session, estudio_id, asistente_id) -> dict:
    """Grupos y RUCs actualmente asignados a un asistente (ids como str)."""
    grupos = list(await session.scalars(
        select(AsignacionGrupo.grupo_id).where(
            AsignacionGrupo.estudio_id == estudio_id,
            AsignacionGrupo.persona_asistente_id == asistente_id)))
    rucs = list(await session.scalars(
        select(Asignacion.contribuyente_id).where(
            Asignacion.estudio_id == estudio_id,
            Asignacion.persona_asistente_id == asistente_id)))
    return {"grupos": [str(g) for g in grupos], "rucs": [str(r) for r in rucs]}


@router.get("/asignaciones", response_class=HTMLResponse)
async def asignaciones_page(request: Request,
                            user: UsuarioActual = Depends(usuario_actual)):
    if not _solo_dueno(user):
        return templates.TemplateResponse(
            request, "asignaciones.html",
            {"user": user, "autorizado": False,
             "asistentes": [], "grupos": [], "contribs": [], "asignaciones": {}})
    async with get_session() as session:
        asistentes = await _asistentes_del_estudio(session, user.estudio_id)
        # Grupos del estudio + conteo de RUCs (en vivo).
        conteos = dict((gid, n) for gid, n in (await session.execute(
            select(ContribuyenteGrupo.grupo_id, func.count())
            .where(ContribuyenteGrupo.estudio_id == user.estudio_id)
            .group_by(ContribuyenteGrupo.grupo_id))).all())
        grupos = [{"id": str(g.id), "nombre": g.nombre, "color": g.color or "#5EEA8C",
                   "n": conteos.get(g.id, 0)}
                  for g in await session.scalars(
                      select(Grupo).where(Grupo.estudio_id == user.estudio_id)
                      .order_by(Grupo.orden, Grupo.nombre))]
        contribs = [{"id": str(c.id), "ruc": c.ruc, "razon": c.razon_social or c.ruc}
                    for c in await session.scalars(
                        select(Contribuyente).where(
                            Contribuyente.estudio_id == user.estudio_id)
                        .order_by(Contribuyente.razon_social).limit(300))]
        asignaciones = {}
        for a in asistentes:
            asignaciones[a["id"]] = await _asignaciones_de(
                session, user.estudio_id, uuid.UUID(a["id"]))
    return templates.TemplateResponse(request, "asignaciones.html", {
        "user": user, "autorizado": True, "asistentes": asistentes,
        "grupos": grupos, "contribs": contribs, "asignaciones": asignaciones})


@router.get("/api/asignaciones/{asistente_id}")
async def api_asignaciones_de(asistente_id: uuid.UUID,
                              user: UsuarioActual = Depends(usuario_actual)):
    if not _solo_dueno(user):
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    async with get_session() as session:
        if not await _asistente_valido(session, user.estudio_id, asistente_id):
            return JSONResponse({"ok": False, "error": "Asistente no es de tu estudio."},
                                status_code=404)
        data = await _asignaciones_de(session, user.estudio_id, asistente_id)
    return JSONResponse({"ok": True, **data})


@router.post("/api/asignar/grupo")
async def asignar_grupo(request: Request,
                        user: UsuarioActual = Depends(usuario_actual)):
    if not _solo_dueno(user):
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    data = await request.json()
    accion = (data.get("accion") or "asignar").strip()
    try:
        aid = uuid.UUID(data.get("asistente_id") or "")
        gid = uuid.UUID(data.get("grupo_id") or "")
    except ValueError:
        return JSONResponse({"ok": False, "error": "Datos inválidos."}, status_code=400)
    async with get_session() as session:
        if not await _asistente_valido(session, user.estudio_id, aid):
            return JSONResponse({"ok": False, "error": "Asistente no es de tu estudio."}, status_code=404)
        # El grupo debe ser del estudio (multi-tenant).
        if not await session.scalar(select(Grupo.id).where(
                Grupo.id == gid, Grupo.estudio_id == user.estudio_id)):
            return JSONResponse({"ok": False, "error": "Grupo no es de tu estudio."}, status_code=404)
        if accion == "quitar":
            await session.execute(delete(AsignacionGrupo).where(
                AsignacionGrupo.estudio_id == user.estudio_id,
                AsignacionGrupo.persona_asistente_id == aid,
                AsignacionGrupo.grupo_id == gid))
            await registrar_auditoria(session, persona_id=user.persona_id,
                                      accion="DESASIGNAR_GRUPO", estudio_id=user.estudio_id,
                                      objeto_tipo="grupo", objeto_id=gid,
                                      datos={"asistente_persona_id": str(aid)})
        else:
            # Idempotente: si ya existe, no duplica (unique lo respalda).
            existe = await session.scalar(select(AsignacionGrupo.id).where(
                AsignacionGrupo.persona_asistente_id == aid, AsignacionGrupo.grupo_id == gid))
            if not existe:
                session.add(AsignacionGrupo(
                    estudio_id=user.estudio_id, persona_asistente_id=aid, grupo_id=gid))
                await registrar_auditoria(session, persona_id=user.persona_id,
                                          accion="ASIGNAR_GRUPO", estudio_id=user.estudio_id,
                                          objeto_tipo="grupo", objeto_id=gid,
                                          datos={"asistente_persona_id": str(aid)})
        await session.commit()
        data_out = await _asignaciones_de(session, user.estudio_id, aid)
    return JSONResponse({"ok": True, **data_out})


@router.post("/api/asignar/ruc")
async def asignar_ruc(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    if not _solo_dueno(user):
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    data = await request.json()
    accion = (data.get("accion") or "asignar").strip()
    try:
        aid = uuid.UUID(data.get("asistente_id") or "")
        cid = uuid.UUID(data.get("contribuyente_id") or "")
    except ValueError:
        return JSONResponse({"ok": False, "error": "Datos inválidos."}, status_code=400)
    async with get_session() as session:
        if not await _asistente_valido(session, user.estudio_id, aid):
            return JSONResponse({"ok": False, "error": "Asistente no es de tu estudio."}, status_code=404)
        if not await session.scalar(select(Contribuyente.id).where(
                Contribuyente.id == cid, Contribuyente.estudio_id == user.estudio_id)):
            return JSONResponse({"ok": False, "error": "RUC no es de tu estudio."}, status_code=404)
        if accion == "quitar":
            await session.execute(delete(Asignacion).where(
                Asignacion.estudio_id == user.estudio_id,
                Asignacion.persona_asistente_id == aid,
                Asignacion.contribuyente_id == cid))
            await registrar_auditoria(session, persona_id=user.persona_id,
                                      accion="DESASIGNAR_RUC", estudio_id=user.estudio_id,
                                      contribuyente_id=cid, objeto_tipo="contribuyente",
                                      objeto_id=cid, datos={"asistente_persona_id": str(aid)})
        else:
            existe = await session.scalar(select(Asignacion.id).where(
                Asignacion.persona_asistente_id == aid, Asignacion.contribuyente_id == cid))
            if not existe:
                session.add(Asignacion(
                    estudio_id=user.estudio_id, persona_asistente_id=aid,
                    contribuyente_id=cid, es_principal=True))
                await registrar_auditoria(session, persona_id=user.persona_id,
                                          accion="ASIGNAR_RUC", estudio_id=user.estudio_id,
                                          contribuyente_id=cid, objeto_tipo="contribuyente",
                                          objeto_id=cid, datos={"asistente_persona_id": str(aid)})
        await session.commit()
        data_out = await _asignaciones_de(session, user.estudio_id, aid)
    return JSONResponse({"ok": True, **data_out})
