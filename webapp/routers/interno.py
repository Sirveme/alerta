"""
webapp/routers/interno.py — alerta.pe · Páginas INTERNAS (solo SOPORTE_GLOBAL).
═══════════════════════════════════════════════════════════════════════
`/interno/mapa` — "Mapa Global de alerta.pe": referencia privada para Duilio
(SOPORTE_GLOBAL). Contenido editable en `webapp/contenido/mapa_global.md`.

Privada de verdad: cualquier usuario que NO sea SOPORTE_GLOBAL recibe 404
(no 403) → la página es invisible para el resto. `noindex` en el <meta> y
en la cabecera X-Robots-Tag.
"""

from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func

from db import get_session
from models import (
    WorkerEstado, Contribuyente, PushSuscripcion, Notificacion,
    EstadoContribuyente, CronogramaVencimiento, PadronBuenContribuyente,
    ahora_lima,
)
from ..core import templates, TZ_LIMA
from ..deps import UsuarioActual, usuario_actual
from ..mapa_render import cargar_mapa

router = APIRouter(tags=["interno"])

# Sin ciclo completo hace más de esto → worker DETENIDO (igual que el vigilante).
_UMBRAL_HORAS = float(os.getenv("VIGILANTE_UMBRAL_HORAS", "2"))

_MOTIVO_LABEL = {
    "login": "Clave SOL rechazada", "timeout": "Se colgó (timeout)",
    "red": "SUNAT/red (técnico)", "otro": "Otro fallo",
}


@router.get("/interno/mapa", response_class=HTMLResponse)
async def mapa_global(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    # Invisible para no-soporte: 404, no 403.
    if not user.es_soporte_global:
        raise HTTPException(status_code=404)
    mapa = cargar_mapa()
    resp = templates.TemplateResponse(request, "mapa.html", {
        "user": user, "mapa": mapa,
        "generado": datetime.now(TZ_LIMA).strftime("%d/%m/%Y %H:%M"),
    })
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@router.get("/interno/salud", response_class=HTMLResponse)
async def salud(request: Request,
                user: UsuarioActual = Depends(usuario_actual)):
    """Panel de salud (solo SOPORTE_GLOBAL). Distingue TRES situaciones:
    worker detenido · sin novedades · push no entregado."""
    if not user.es_soporte_global:
        raise HTTPException(status_code=404)

    ahora = ahora_lima()
    def _fmt(dt):
        if not dt:
            return "—"
        v = dt.astimezone(TZ_LIMA) if dt.tzinfo else dt.replace(tzinfo=TZ_LIMA)
        return v.strftime("%d/%m/%Y %H:%M")
    def _edad_h(dt):
        if not dt:
            return None
        v = dt.astimezone(TZ_LIMA) if dt.tzinfo else dt.replace(tzinfo=TZ_LIMA)
        return (ahora - v).total_seconds() / 3600.0

    async with get_session() as session:
        est = await session.get(WorkerEstado, "singleton")
        ciclo_ok_at = est.ultimo_ciclo_ok_at if est else None
        edad = _edad_h(ciclo_ok_at)
        worker_detenido = (edad is None) or (edad > _UMBRAL_HORAS)

        # Por RUC (solo los que se vigilan: con credencial). Estado de lectura.
        contribs = list(await session.scalars(
            select(Contribuyente)
            .options()
            .order_by(Contribuyente.razon_social)))
        rucs = []
        ultima_notif = None
        for c in contribs:
            un = await session.scalar(
                select(func.max(Notificacion.creado_at)).where(
                    Notificacion.contribuyente_id == c.id))
            if un and (ultima_notif is None or un > ultima_notif):
                ultima_notif = un
            leido_ok = (c.ultimo_scrapeo_ok is True)
            rucs.append({
                "ruc": c.ruc, "razon": c.razon_social or c.ruc,
                "ultimo": _fmt(c.ultimo_scrapeo_at),
                "ok": leido_ok,
                "no_leido": (c.ultimo_scrapeo_ok is False),
                "motivo": _MOTIVO_LABEL.get(c.ultimo_error_motivo or "", "—"),
                "error_at": _fmt(c.ultimo_error_at),
                "estado": c.estado.value if hasattr(c.estado, "value") else str(c.estado),
            })
        no_leidos = sum(1 for r in rucs if r["no_leido"])

        # Push: VAPID + suscripciones + backlog pendiente de entrega.
        vapid_ok = bool(os.getenv("VAPID_PRIVATE_KEY"))
        subs_activas = await session.scalar(
            select(func.count()).select_from(PushSuscripcion).where(
                PushSuscripcion.activa.is_(True))) or 0
        subs_muertas = await session.scalar(
            select(func.count()).select_from(PushSuscripcion).where(
                PushSuscripcion.activa.is_(False))) or 0
        # "pendientes de push": notifs sin notificado_push (backlog de entrega).
        push_pendientes = await session.scalar(
            select(func.count()).select_from(Notificacion).where(
                Notificacion.notificado_push.is_(False))) or 0
        push_problema = (not vapid_ok) or (subs_activas == 0) or (subs_muertas > 0)

    ctx = {
        "user": user,
        "generado": ahora.strftime("%d/%m/%Y %H:%M"),
        "umbral_horas": _UMBRAL_HORAS,
        # 3 situaciones:
        "worker": {
            "detenido": worker_detenido,
            "ciclo_ok_at": _fmt(ciclo_ok_at),
            "edad_h": (f"{edad:.1f}" if edad is not None else None),
            "rucs_ok": (est.rucs_ok if est else 0),
            "rucs_fallo": (est.rucs_fallo if est else 0),
            "rucs_timeout": (est.rucs_timeout if est else 0),
        },
        "novedades": {
            "ultima_notif": _fmt(ultima_notif),
            "no_leidos": no_leidos,
        },
        "push": {
            "problema": push_problema,
            "vapid_ok": vapid_ok,
            "subs_activas": subs_activas,
            "subs_muertas": subs_muertas,
            "pendientes": push_pendientes,
        },
        "rucs": rucs,
    }
    resp = templates.TemplateResponse(request, "salud.html", ctx)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ═══════════════════════════════════════════════════════════════════════
# /interno/cronograma — cargar el cronograma de vencimientos + padrón BC
# (estado "pagó fuera de plazo"). Flujo PREVIEW → CONFIRMAR: nunca guarda sin
# que el soporte confirme. Solo SOPORTE_GLOBAL (404 al resto). Ver
# decl_diag_PAGO_FUERA_DE_PLAZO.md y cronograma_loader.py.
# ═══════════════════════════════════════════════════════════════════════
def _guard_soporte(user: UsuarioActual):
    if not user.es_soporte_global:
        raise HTTPException(status_code=404)


async def _estado_cronograma() -> dict:
    """Resumen de lo cargado (tolera que la migración no esté aplicada)."""
    out = {"anios": [], "padron": None, "sin_tablas": False}
    try:
        async with get_session() as session:
            filas = (await session.execute(
                select(CronogramaVencimiento.anio,
                       func.count().label("n"))
                .group_by(CronogramaVencimiento.anio)
                .order_by(CronogramaVencimiento.anio.desc()))).all()
            out["anios"] = [{"anio": a, "n": n} for a, n in filas]
            out["padron"] = await session.scalar(
                select(func.count()).select_from(PadronBuenContribuyente))
    except Exception as e:  # tabla inexistente u otro
        out["sin_tablas"] = True
        out["error"] = str(e)[:140]
    return out


def _ctx_base(request, user, estado, **extra):
    ctx = {"user": user, "estado": estado,
           "generado": datetime.now(TZ_LIMA).strftime("%d/%m/%Y %H:%M")}
    ctx.update(extra)
    return ctx


@router.get("/interno/cronograma", response_class=HTMLResponse)
async def cronograma_panel(request: Request,
                           user: UsuarioActual = Depends(usuario_actual)):
    _guard_soporte(user)
    estado = await _estado_cronograma()
    resp = templates.TemplateResponse(
        request, "interno_cronograma.html", _ctx_base(request, user, estado))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@router.post("/interno/cronograma/preview", response_class=HTMLResponse)
async def cronograma_preview(
    request: Request,
    fuente: str = Form(...),              # 'cronograma' | 'padron' | 'prorrogas'
    url: str = Form(""),
    anio: str = Form(""),
    num_resolucion: str = Form(""),
    user: UsuarioActual = Depends(usuario_actual),
):
    """Baja + parsea y MUESTRA lo que se guardaría. NO escribe nada."""
    _guard_soporte(user)
    import cronograma_loader as L
    estado = await _estado_cronograma()
    preview = {"fuente": fuente, "url": url, "anio": anio,
               "num_resolucion": num_resolucion, "filas": [], "total": 0,
               "bloques": [], "error": None}
    try:
        if fuente == "cronograma":
            anio_i = int(anio) if anio.strip().isdigit() else 0
            html = L.fetch_html(url)
            filas = L.parse_cronograma(html, anio_i,
                                       num_resolucion=num_resolucion or None,
                                       fuente=url)
            preview["total"] = len(filas)
            preview["filas"] = filas[:60]
        elif fuente == "padron":
            txt = L.descargar_padron_bc_txt(url)
            filas = L.parse_padron_bc_txt(txt, fuente=url)
            preview["total"] = len(filas)
            preview["filas"] = filas[:60]
        elif fuente == "prorrogas":
            html = L.fetch_html(url)
            preview["bloques"] = L.parse_prorrogas(html)
            preview["total"] = sum(len(b["filas"]) for b in preview["bloques"])
        else:
            preview["error"] = "Fuente no reconocida."
    except Exception as e:
        preview["error"] = f"{type(e).__name__}: {e}"[:200]
    resp = templates.TemplateResponse(
        request, "interno_cronograma.html",
        _ctx_base(request, user, estado, preview=preview))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@router.post("/interno/cronograma/guardar", response_class=HTMLResponse)
async def cronograma_guardar(
    request: Request,
    fuente: str = Form(...),
    url: str = Form(""),
    anio: str = Form(""),
    num_resolucion: str = Form(""),
    user: UsuarioActual = Depends(usuario_actual),
):
    """CONFIRMADO: vuelve a parsear (determinista) y GUARDA (upsert). Prórrogas
    NO se guardan aquí (necesitan mapeo manual de período/ámbito)."""
    _guard_soporte(user)
    import cronograma_loader as L
    msg = None
    try:
        if fuente == "cronograma":
            anio_i = int(anio) if anio.strip().isdigit() else 0
            filas = L.parse_cronograma(L.fetch_html(url), anio_i,
                                       num_resolucion=num_resolucion or None,
                                       fuente=url)
            n = await L.guardar_cronograma(filas)
            msg = f"Cronograma {anio_i}: {n} fila(s) guardada(s)."
        elif fuente == "padron":
            filas = L.parse_padron_bc_txt(L.descargar_padron_bc_txt(url), fuente=url)
            n = await L.guardar_padron_bc(filas)
            msg = f"Padrón BC: {n} RUC(s) guardado(s)."
        elif fuente == "prorrogas":
            msg = ("Las prórrogas requieren mapeo manual de período/ámbito "
                   "(pendiente de UI); no se guardaron.")
        else:
            msg = "Fuente no reconocida."
    except Exception as e:
        msg = f"Error al guardar: {type(e).__name__}: {e}"[:200]
    from urllib.parse import quote
    return RedirectResponse(f"/interno/cronograma?msg={quote(msg or '')}",
                            status_code=303)
