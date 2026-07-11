"""
webapp/routers/seleccion.py — alerta.pe (zAlerta-60, Fase 3)
═══════════════════════════════════════════════════════════════════════
Selector de contexto ("¿Qué buzón quieres ver?") para personas con >1
acceso o SOPORTE_GLOBAL. El contexto activo se fija en la sesión (eid/tc/
rol), de modo que las vistas existentes siguen filtrando por estudio_id.

SOPORTE_GLOBAL: ve todos los buzones activos; al abrir uno que NO es suyo
por acceso nominal, se registra en `auditoria_soporte`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from db import get_session
from models import EstudioContable, Persona, AuditoriaSoporte
from ..core import templates
from ..deps import usuario_actual, UsuarioActual
from ..auth import accesos_vigentes, crear_token_persona, set_cookie_sesion

router = APIRouter(tags=["seleccion"])


@router.get("/seleccionar-buzon")
async def seleccionar_buzon_form(
        request: Request, user: UsuarioActual = Depends(usuario_actual)):
    # Login viejo (sin persona) → no hay selector.
    if not user.persona_id:
        return RedirectResponse("/", status_code=303)
    async with get_session() as session:
        accesos = await accesos_vigentes(session, user.persona_id)
        propios, ids_propios = [], set()
        for a in accesos:
            e = await session.get(EstudioContable, a.estudio_id)
            if not e:
                continue
            ids_propios.add(e.id)
            propios.append({
                "eid": str(e.id),
                "razon": e.razon_social,
                "cargo": (a.cargo.name.capitalize() if a.cargo else None),
                "activo": str(e.id) == str(user.estudio_id),
            })
        otros = []
        if user.es_soporte_global:
            todos = (await session.scalars(
                select(EstudioContable).where(EstudioContable.activo == True)  # noqa: E712
                .order_by(EstudioContable.razon_social))).all()
            for e in todos:
                if e.id in ids_propios:
                    continue
                otros.append({
                    "eid": str(e.id),
                    "razon": e.razon_social,
                    "activo": str(e.id) == str(user.estudio_id),
                })
    return templates.TemplateResponse(request, "seleccionar_buzon.html", {
        "user": user, "propios": propios, "otros": otros,
        "soporte": user.es_soporte_global,
    })


@router.post("/seleccionar-buzon")
async def seleccionar_buzon_post(
        request: Request, estudio_id: str = Form(...),
        user: UsuarioActual = Depends(usuario_actual)):
    if not user.persona_id:
        return RedirectResponse("/", status_code=303)
    try:
        eid = uuid.UUID(estudio_id)
    except (ValueError, TypeError):
        return RedirectResponse("/seleccionar-buzon", status_code=303)

    async with get_session() as session:
        persona = await session.get(Persona, user.persona_id)
        estudio = await session.get(EstudioContable, eid)
        if not persona or not estudio:
            return RedirectResponse("/seleccionar-buzon", status_code=303)

        accesos = await accesos_vigentes(session, persona.id)
        acceso = next((a for a in accesos if a.estudio_id == eid), None)

        if acceso is None:
            # Sin acceso nominal: solo SOPORTE_GLOBAL puede; se audita.
            if not user.es_soporte_global:
                return RedirectResponse("/seleccionar-buzon", status_code=303)
            session.add(AuditoriaSoporte(
                persona_id=persona.id, estudio_id=eid, accion="VER"))
            await session.commit()

        token = crear_token_persona(
            persona, estudio, acceso, user.tiene_usuario, user.multi_contexto)

    resp = RedirectResponse("/", status_code=303)
    set_cookie_sesion(resp, token)
    return resp
