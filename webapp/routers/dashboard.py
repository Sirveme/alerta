"""
webapp/routers/dashboard.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Dashboard de GRUPOS (pantalla principal en celular) y vista de un grupo.

Patrón de uso (zAlerta-01 B.3):
  - Celular → navegar por GRUPOS chicos → 1-2 clientes puntuales.
  - PC/tablet → vista masiva (lista de todos los contribuyentes).

Multi-tenant SIEMPRE: cada query filtra por user.estudio_id.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func

from db import get_session
from models import (
    Grupo, Contribuyente, ContribuyenteGrupo, Notificacion, Urgencia,
)
from ..core import templates
from ..deps import UsuarioActual, usuario_actual, requiere_escritura

router = APIRouter(tags=["dashboard"])

# Urgencias que cuentan como "necesita atención" para los badges.
_URGENTES = (Urgencia.URGENTE, Urgencia.CRITICA)

# Colores plantilla para grupos libres nuevos (rotación).
_COLORES_LIBRES = ["#7C3AED", "#0891B2", "#BE185D", "#15803D", "#B45309"]


async def _resumen_grupos(session, estudio_id: uuid.UUID) -> list[dict]:
    """Devuelve cada grupo con nº de clientes y nº de urgentes."""
    grupos = (await session.scalars(
        select(Grupo).where(Grupo.estudio_id == estudio_id)
        .order_by(Grupo.orden, Grupo.nombre))).all()

    resumen = []
    for g in grupos:
        n_clientes = await session.scalar(
            select(func.count(ContribuyenteGrupo.id)).where(
                ContribuyenteGrupo.grupo_id == g.id,
                ContribuyenteGrupo.estudio_id == estudio_id))
        # Contribuyentes del grupo con notificaciones urgentes/críticas no leídas
        n_urgentes = await session.scalar(
            select(func.count(func.distinct(Notificacion.contribuyente_id)))
            .join(ContribuyenteGrupo,
                  ContribuyenteGrupo.contribuyente_id == Notificacion.contribuyente_id)
            .where(ContribuyenteGrupo.grupo_id == g.id,
                   Notificacion.estudio_id == estudio_id,
                   Notificacion.urgencia.in_(_URGENTES),
                   Notificacion.leida == False))  # noqa: E712
        resumen.append({
            "id": str(g.id),
            "nombre": g.nombre,
            "color": g.color or "#64748B",
            "icono": g.icono or "ti-folder",
            "n_clientes": n_clientes or 0,
            "n_urgentes": n_urgentes or 0,
        })
    return resumen


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        grupos = await _resumen_grupos(session, user.estudio_id)
        total_clientes = await session.scalar(
            select(func.count(Contribuyente.id)).where(
                Contribuyente.estudio_id == user.estudio_id))
        # Contribuyentes sin ningún grupo (para no perderlos de vista)
        sin_grupo = await session.scalar(
            select(func.count(Contribuyente.id)).where(
                Contribuyente.estudio_id == user.estudio_id,
                ~Contribuyente.id.in_(
                    select(ContribuyenteGrupo.contribuyente_id).where(
                        ContribuyenteGrupo.estudio_id == user.estudio_id))))
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "grupos": grupos,
        "total_clientes": total_clientes or 0,
        "sin_grupo": sin_grupo or 0,
    })


@router.post("/grupos")
async def crear_grupo(
    request: Request,
    nombre: str = Form(...),
    color: str = Form("#7C3AED"),
    icono: str = Form("ti-folder"),
    user: UsuarioActual = Depends(requiere_escritura),
):
    nombre = (nombre or "").strip()
    if not nombre:
        return RedirectResponse("/", status_code=303)
    async with get_session() as session:
        existe = await session.scalar(
            select(Grupo.id).where(Grupo.estudio_id == user.estudio_id,
                                   Grupo.nombre == nombre))
        if not existe:
            # Si no mandaron color, rotar uno de la paleta libre
            n = await session.scalar(
                select(func.count(Grupo.id)).where(Grupo.estudio_id == user.estudio_id))
            col = color or _COLORES_LIBRES[(n or 0) % len(_COLORES_LIBRES)]
            session.add(Grupo(estudio_id=user.estudio_id, nombre=nombre,
                              color=col, icono=icono or "ti-folder",
                              orden=(n or 0) + 10))
            await session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/grupos/plantilla")
async def crear_grupos_plantilla(
    request: Request, user: UsuarioActual = Depends(requiere_escritura)):
    """Crea los grupos por régimen (NRUS·Bodegas, RER, RMT, Régimen General)."""
    from migrar_grupos_zAlerta01 import crear_grupos_plantilla as _crear
    await _crear(user.estudio_id)
    return RedirectResponse("/", status_code=303)


@router.get("/grupos/{grupo_id}", response_class=HTMLResponse)
async def ver_grupo(
    request: Request, grupo_id: uuid.UUID,
    user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        grupo = await session.scalar(
            select(Grupo).where(Grupo.id == grupo_id,
                                Grupo.estudio_id == user.estudio_id))
        if not grupo:
            return RedirectResponse("/", status_code=303)

        contribs = (await session.scalars(
            select(Contribuyente)
            .join(ContribuyenteGrupo,
                  ContribuyenteGrupo.contribuyente_id == Contribuyente.id)
            .where(ContribuyenteGrupo.grupo_id == grupo_id,
                   Contribuyente.estudio_id == user.estudio_id)
            .order_by(Contribuyente.razon_social))).all()

        # Urgencia máxima por contribuyente (para el badge de su tarjeta)
        tarjetas = []
        for c in contribs:
            urgencias = (await session.scalars(
                select(Notificacion.urgencia).where(
                    Notificacion.contribuyente_id == c.id,
                    Notificacion.estudio_id == user.estudio_id))).all()
            no_leidas = await session.scalar(
                select(func.count(Notificacion.id)).where(
                    Notificacion.contribuyente_id == c.id,
                    Notificacion.estudio_id == user.estudio_id,
                    Notificacion.leida == False))  # noqa: E712
            tarjetas.append({
                "id": str(c.id),
                "ruc": c.ruc,
                "razon_social": c.razon_social or c.ruc,
                "urgencia": _urgencia_max(urgencias),
                "no_leidas": no_leidas or 0,
                "estado": c.estado.value,
            })

        # Contribuyentes que NO están en este grupo (para agregar)
        disponibles = (await session.scalars(
            select(Contribuyente).where(
                Contribuyente.estudio_id == user.estudio_id,
                ~Contribuyente.id.in_(
                    select(ContribuyenteGrupo.contribuyente_id).where(
                        ContribuyenteGrupo.grupo_id == grupo_id)))
            .order_by(Contribuyente.razon_social))).all()

    return templates.TemplateResponse(request, "grupo.html", {
        "user": user, "grupo": grupo, "tarjetas": tarjetas,
        "color": grupo.color or "#64748B",
        "disponibles": [{"id": str(c.id), "ruc": c.ruc,
                         "razon_social": c.razon_social or c.ruc}
                        for c in disponibles],
        "sugerir_dividir": len(tarjetas) > 20,
    })


@router.post("/grupos/{grupo_id}/contribuyentes")
async def agregar_contribuyente(
    request: Request, grupo_id: uuid.UUID,
    contribuyente_id: uuid.UUID = Form(...),
    user: UsuarioActual = Depends(requiere_escritura)):
    async with get_session() as session:
        # Verificar pertenencia al tenant
        grupo = await session.scalar(
            select(Grupo.id).where(Grupo.id == grupo_id,
                                   Grupo.estudio_id == user.estudio_id))
        contrib = await session.scalar(
            select(Contribuyente.id).where(
                Contribuyente.id == contribuyente_id,
                Contribuyente.estudio_id == user.estudio_id))
        if grupo and contrib:
            existe = await session.scalar(
                select(ContribuyenteGrupo.id).where(
                    ContribuyenteGrupo.grupo_id == grupo_id,
                    ContribuyenteGrupo.contribuyente_id == contribuyente_id))
            if not existe:
                session.add(ContribuyenteGrupo(
                    contribuyente_id=contribuyente_id, grupo_id=grupo_id,
                    estudio_id=user.estudio_id))
                await session.commit()
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


@router.post("/grupos/{grupo_id}/quitar/{contribuyente_id}")
async def quitar_contribuyente(
    request: Request, grupo_id: uuid.UUID, contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(requiere_escritura)):
    async with get_session() as session:
        link = await session.scalar(
            select(ContribuyenteGrupo).where(
                ContribuyenteGrupo.grupo_id == grupo_id,
                ContribuyenteGrupo.contribuyente_id == contribuyente_id,
                ContribuyenteGrupo.estudio_id == user.estudio_id))
        if link:
            await session.delete(link)
            await session.commit()
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


@router.get("/contribuyentes", response_class=HTMLResponse)
async def lista_contribuyentes(
    request: Request, user: UsuarioActual = Depends(usuario_actual)):
    """Vista masiva (PC/tablet): todos los contribuyentes del estudio."""
    async with get_session() as session:
        contribs = (await session.scalars(
            select(Contribuyente).where(
                Contribuyente.estudio_id == user.estudio_id)
            .order_by(Contribuyente.razon_social))).all()
        filas = []
        for c in contribs:
            urgencias = (await session.scalars(
                select(Notificacion.urgencia).where(
                    Notificacion.contribuyente_id == c.id,
                    Notificacion.estudio_id == user.estudio_id))).all()
            filas.append({
                "id": str(c.id), "ruc": c.ruc,
                "razon_social": c.razon_social or c.ruc,
                "urgencia": _urgencia_max(urgencias),
                "estado": c.estado.value,
                "ultimo_scrapeo_at": c.ultimo_scrapeo_at,
            })
    return templates.TemplateResponse(request, "contribuyentes.html", {
        "user": user, "filas": filas})


# ─────────────────────────────────────────────────────────────────────
_ORDEN_URGENCIA = {
    "critica": 5, "urgente": 4, "importante": 3,
    "informativa": 2, "sin_clasificar": 1,
}


def _urgencia_max(urgencias) -> str:
    """Devuelve la urgencia más alta de una lista (o 'al_dia' si vacía)."""
    if not urgencias:
        return "al_dia"
    valores = [u.value if hasattr(u, "value") else u for u in urgencias]
    return max(valores, key=lambda v: _ORDEN_URGENCIA.get(v, 0))
