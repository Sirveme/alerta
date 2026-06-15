"""
webapp/routers/notificaciones.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Lista de notificaciones de un contribuyente (con filtro por tipo de
documento), detalle, reacciones (útil/no útil/destacar) y servido de PDF.

zAlerta-01 B.4 / B.5 (reacciones). Multi-tenant en cada query.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from db import get_session
from models import (
    Contribuyente, Notificacion, Adjunto, Reaccion, TipoReaccion,
    TipoDocumento, ETIQUETA_TIPO_DOCUMENTO, ahora_lima,
)
from ..core import templates
from ..deps import (
    UsuarioActual, usuario_actual, requiere_escritura, contribuyente_accesible,
)

router = APIRouter(tags=["notificaciones"])


def _puede_ver_notif(user: UsuarioActual, notif) -> bool:
    """True si el usuario puede ver esta notificación (multi-tenant + empresario)."""
    if notif.estudio_id == user.estudio_id:
        return True
    if user.es_empresario and notif.contribuyente is not None:
        return notif.contribuyente.cuenta_empresario_id == user.estudio_id
    return False


@router.get("/contribuyentes/{contribuyente_id}/notificaciones",
            response_class=HTMLResponse)
async def lista_notificaciones(
    request: Request, contribuyente_id: uuid.UUID,
    tipo: str | None = None,
    user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        # Acceso multi-tenant + empresario (solo su RUC vía cuenta_empresario_id).
        contrib = await contribuyente_accesible(session, user, contribuyente_id)
        if not contrib:
            return RedirectResponse("/", status_code=303)

        # Filtramos por el estudio REAL del RUC (para el empresario es el del
        # contador; para el estudio es el suyo). El acceso ya fue validado.
        q = (select(Notificacion).where(
                Notificacion.contribuyente_id == contribuyente_id,
                Notificacion.estudio_id == contrib.estudio_id))

        tipo_sel = None
        if tipo and tipo in ETIQUETA_TIPO_DOCUMENTO:
            tipo_sel = tipo
            q = q.where(Notificacion.tipo_documento_enum == TipoDocumento(tipo))

        notifs = (await session.scalars(
            q.order_by(desc(Notificacion.fecha_publica_sunat),
                       desc(Notificacion.creado_at)).limit(200))).all()

    return templates.TemplateResponse(request, "notificaciones.html", {
        "user": user, "contrib": contrib, "notifs": notifs,
        "tipos": ETIQUETA_TIPO_DOCUMENTO, "tipo_sel": tipo_sel,
    })


@router.get("/notificaciones/{notif_id}", response_class=HTMLResponse)
async def detalle_notificacion(
    request: Request, notif_id: uuid.UUID,
    user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        notif = await session.scalar(
            select(Notificacion)
            .options(selectinload(Notificacion.adjuntos),
                     selectinload(Notificacion.contribuyente),
                     selectinload(Notificacion.reacciones))
            .where(Notificacion.id == notif_id))
        if not notif or not _puede_ver_notif(user, notif):
            return RedirectResponse("/", status_code=303)

        # Marcar como leída (no es escritura sensible; lo hace cualquier rol)
        if not notif.leida:
            notif.leida = True
            notif.leida_at = ahora_lima()
            await session.commit()

        mi_reaccion = next(
            (r.tipo.value for r in notif.reacciones if str(r.usuario_id) == str(user.id)),
            None)

    return templates.TemplateResponse(request, "notificacion.html", {
        "user": user, "notif": notif, "mi_reaccion": mi_reaccion,
    })


@router.post("/notificaciones/{notif_id}/reaccion")
async def reaccionar(
    request: Request, notif_id: uuid.UUID,
    user: UsuarioActual = Depends(requiere_escritura)):
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else await request.form()
    tipo_raw = (data.get("tipo") or "").strip()
    try:
        tipo = TipoReaccion(tipo_raw)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Tipo inválido."}, status_code=400)

    async with get_session() as session:
        notif = await session.scalar(
            select(Notificacion.id).where(
                Notificacion.id == notif_id,
                Notificacion.estudio_id == user.estudio_id))
        if not notif:
            return JSONResponse({"ok": False, "error": "No encontrada."}, status_code=404)

        existente = await session.scalar(
            select(Reaccion).where(
                Reaccion.usuario_id == user.id,
                Reaccion.notificacion_id == notif_id))
        quitada = False
        if existente:
            if existente.tipo == tipo:
                # Toggle off: misma reacción → quitar
                await session.delete(existente)
                quitada = True
            else:
                existente.tipo = tipo
        else:
            session.add(Reaccion(
                estudio_id=user.estudio_id, usuario_id=user.id,
                notificacion_id=notif_id, tipo=tipo))
        await session.commit()
    return JSONResponse({"ok": True, "tipo": None if quitada else tipo.value})


@router.get("/notificaciones/{notif_id}/adjunto/{adjunto_id}")
async def ver_adjunto(
    notif_id: uuid.UUID, adjunto_id: uuid.UUID, descargar: bool = False,
    user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        adj = await session.scalar(
            select(Adjunto)
            .options(selectinload(Adjunto.notificacion)
                     .selectinload(Notificacion.contribuyente))
            .where(Adjunto.id == adjunto_id,
                   Adjunto.notificacion_id == notif_id))
        # Acceso: estudio dueño, o empresario vinculado al RUC de la notificación.
        if not adj or adj.notificacion is None or not _puede_ver_notif(user, adj.notificacion):
            return Response("Adjunto no encontrado.", status_code=404)

        if adj.bytea_temporal:
            disp = "attachment" if descargar else "inline"
            return Response(
                content=bytes(adj.bytea_temporal),
                media_type="application/pdf",
                headers={"Content-Disposition":
                         f'{disp}; filename="{adj.nombre_archivo}"'})
        if adj.gcs_key:
            # En producción: redirigir a una URL firmada de GCS.
            return JSONResponse(
                {"error": "PDF en almacenamiento externo (pendiente de URL firmada).",
                 "gcs_key": adj.gcs_key}, status_code=501)
    return Response("El PDF aún no fue descargado.", status_code=404)
