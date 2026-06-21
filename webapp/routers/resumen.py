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

import uuid

from db import get_session
from models import (
    Contribuyente, CredencialSol, Notificacion, EstadoContribuyente,
    ETIQUETA_TIPO_DOCUMENTO, ahora_lima, Usuario,
)
from ..core import templates, fecha_lima
from ..deps import UsuarioActual, usuario_actual

router = APIRouter(tags=["resumen"])


def _periodo_de(asunto: str | None) -> str:
    """Heurística suave: si el asunto trae un periodo evidente, mostrarlo; si no,
    '—' (no inventar). Mantener simple y prudente."""
    return "—"


@router.get("/resumen", response_class=HTMLResponse)
async def resumen_page(request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    return templates.TemplateResponse(request, "resumen.html", {"user": user})


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
            .where(Notificacion.contribuyente_id.in_(sub))
            .order_by(Notificacion.fecha_publica_sunat.desc().nullslast(),
                      Notificacion.creado_at.desc())
            .limit(40))).all()

    filas = []
    for n, ruc, razon in rows:
        tipo_enum = (n.tipo_documento_enum.value
                     if n.tipo_documento_enum is not None else "otro")
        documento = (n.tipo_documento
                     or ETIQUETA_TIPO_DOCUMENTO.get(tipo_enum, "Aviso"))
        filas.append({
            "id": str(n.id),
            "documento": documento,
            "tipo": tipo_enum,
            "periodo": _periodo_de(n.asunto),
            "detalle": (n.asunto or "—")[:160],
            "vence": fecha_lima(n.plazo_vencimiento) if n.plazo_vencimiento else "—",
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
