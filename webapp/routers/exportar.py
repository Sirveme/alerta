"""
webapp/routers/exportar.py — alerta.pe · Exportar a Excel (A: buzón, B: cartera)
═══════════════════════════════════════════════════════════════════════
Dos descargas .xlsx que RESPETAN EXACTAMENTE el scope de las vistas — no
reimplementan el filtrado, reusan el mismo:

  A. GET /cliente/{id}/exportar.xlsx  → una fila por notificación del cliente.
     Scope = `cliente._puede_ver` (el mismo gate que /cliente/{id}): estudio
     dueño/supervisor todo su estudio; ASISTENTE solo si está asignado a ese RUC;
     empresario solo su propio RUC; SOPORTE_GLOBAL su contexto.

  B. GET /cartera/exportar.xlsx       → una fila por cliente + hoja resumen.
     Scope = `cartera.cartera_datos` (la MISMA función que arma /cartera): el
     ASISTENTE exporta solo sus clientes asignados; el dueño/supervisor todo el
     estudio; el empresario no tiene cartera (redirige).

Regla de oro: si la persona no lo ve en pantalla, no sale en el Excel.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import get_session
from models import (
    Notificacion, DocumentoValorado, LecturaNotificacion, Urgencia, RolUsuario,
)
from ..deps import UsuarioActual, usuario_actual
from ..deuda import monto_de_valorado
from ..core import fecha_lima
from ..excel_export import (
    libro_buzon_cliente, libro_cartera, libro_a_bytes, fecha_excel, nombre_archivo,
)
from .cliente import _puede_ver, _tipo_legible, _tributo_de, _periodo_de
from .cartera import cartera_datos

router = APIRouter(tags=["exportar"])

_MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_URG_LABEL = {
    Urgencia.SIN_CLASIFICAR: "—",
    Urgencia.INFORMATIVA: "Informativa",
    Urgencia.IMPORTANTE: "Importante",
    Urgencia.URGENTE: "Urgente",
    Urgencia.CRITICA: "Crítica",
}


def _alcance(user: UsuarioActual) -> str:
    """Texto humano del alcance con que se generó (queda impreso en el libro)."""
    if user.es_soporte_global:
        return "Soporte global — acceso completo (solo lectura)"
    if user.es_empresario:
        return "Tu buzón (tu propio RUC)"
    if user.rol == RolUsuario.ASISTENTE:
        return "Solo tus clientes asignados"
    return "Todo el estudio"


def _descarga(wb, nombre: str) -> Response:
    return Response(
        content=libro_a_bytes(wb), media_type=_MEDIA_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ═══════════════════════ A. BUZÓN DE UN CLIENTE ═══════════════════════
@router.get("/cliente/{contribuyente_id}/exportar.xlsx")
async def exportar_buzon(contribuyente_id: uuid.UUID,
                         user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        contrib = await _puede_ver(session, user, contribuyente_id)   # MISMO scope que la vista
        if not contrib:
            return RedirectResponse("/cartera", status_code=303)

        notifs = list(await session.scalars(
            select(Notificacion)
            .options(selectinload(Notificacion.adjuntos))
            .where(Notificacion.contribuyente_id == contribuyente_id)
            .order_by(Notificacion.fecha_publica_sunat.desc().nullslast(),
                      Notificacion.creado_at.desc())))
        vals = {v.notificacion_id: v for v in await session.scalars(
            select(DocumentoValorado).where(
                DocumentoValorado.contribuyente_id == contribuyente_id))}
        # Estado leído/no POR PERSONA (fuente de verdad: LecturaNotificacion;
        # fallback al flag global `leida` para el login viejo sin persona).
        leidas_persona: set = set()
        if user.persona_id and notifs:
            leidas_persona = set(await session.scalars(
                select(LecturaNotificacion.notificacion_id).where(
                    LecturaNotificacion.persona_id == user.persona_id,
                    LecturaNotificacion.notificacion_id.in_([n.id for n in notifs]))))

        filas = []
        for n in notifs:
            v = vals.get(n.id)
            monto = monto_de_valorado(v) if v else None
            leido = (n.id in leidas_persona) or bool(n.leida)
            filas.append({
                "fecha": fecha_excel(n.fecha_publica_sunat),
                "tipo": _tipo_legible(n.tipo_documento_enum, n.subtipo_coactivo),
                "tributo": _tributo_de(n.asunto),
                "periodo": _periodo_de(n.asunto) or "—",
                "asunto": n.asunto or "—",
                "monto": monto,
                "estado": "Leído" if leido else "No leído",
                "urgencia": _URG_LABEL.get(n.urgencia, "—"),
                "adjuntos": sum(1 for a in n.adjuntos if a.gcs_key),
            })
        razon = contrib.razon_social or contrib.ruc

    wb = libro_buzon_cliente(razon, contrib.ruc, filas,
                             generado_por=user.nombre, alcance=_alcance(user))
    return _descarga(wb, nombre_archivo("Buzon", razon))


# ═══════════════════════ B. CARTERA DEL ESTUDIO ═══════════════════════
@router.get("/cartera/exportar.xlsx")
async def exportar_cartera(user: UsuarioActual = Depends(usuario_actual)):
    if user.es_empresario:                       # el empresario no tiene cartera
        return RedirectResponse("/mi-cuenta", status_code=303)
    async with get_session() as session:
        estudio, clientes = await cartera_datos(session, user)   # MISMO scope que la vista
        nombre_estudio = estudio.razon_social if estudio else "Estudio contable"

    wb = libro_cartera(nombre_estudio, clientes,
                       generado_por=user.nombre, alcance=_alcance(user))
    return _descarga(wb, nombre_archivo("Cartera", nombre_estudio))
