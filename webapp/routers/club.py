# -*- coding: utf-8 -*-
"""
webapp/routers/club.py — alerta.pe · Vistas "Club de Contadores" (proyecto vistas-club)
═══════════════════════════════════════════════════════════════════════
Vistas para mostrar/grabar, con el **Club de Contadores** como protagonista y
"con alerta.pe" en pequeño. Tema oscuro (nunca fondo #fff).

  · GET /club/compras/{id}            → Compras del período: una fila por ÍTEM,
                                        filtros por proveedor y texto, total,
                                        botón "Descargar Excel".
  · GET /club/compras/{id}/export.xlsx → el mismo scope+filtros, a Excel.
  · GET /club/demo/toggle | /montos    → interruptores del Modo demostración (cookie).

Scope: reusa `_puede_ver` de la vista de cliente (multi-tenant). Modo demostración:
server-side (webapp/demo_mask), enmascara identidades (y opcional montos) igual en la
pantalla y en el Excel.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, func

from db import get_session
from models import Contribuyente, ComprobanteCompra, ComprobanteItem
from ..deps import UsuarioActual, usuario_actual
from ..core import templates
from .. import demo_mask as dm
from ..excel_export import libro_compras, libro_a_bytes, nombre_archivo
from .cliente import _puede_ver

router = APIRouter(tags=["club"])

_MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_COOKIE_KW = dict(max_age=60 * 60 * 8, path="/", httponly=True, samesite="lax")


def _f(v):
    """Decimal/None → float|None (para sumar y formatear)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fecha_txt(d):
    try:
        return d.strftime("%d/%m/%Y") if d else ""
    except Exception:
        return ""


async def _periodos_de(session, contrib_id) -> list[str]:
    rows = await session.scalars(
        select(ComprobanteCompra.periodo)
        .where(ComprobanteCompra.contribuyente_id == contrib_id)
        .distinct().order_by(ComprobanteCompra.periodo.desc()))
    return [p for p in rows if p]


async def _proveedores_de(session, contrib_id, periodo) -> list[dict]:
    rows = await session.execute(
        select(ComprobanteCompra.ruc_proveedor,
               func.max(ComprobanteCompra.razon_social_proveedor))
        .where(ComprobanteCompra.contribuyente_id == contrib_id,
               ComprobanteCompra.periodo == periodo)
        .group_by(ComprobanteCompra.ruc_proveedor)
        .order_by(func.max(ComprobanteCompra.razon_social_proveedor)))
    return [{"ruc": r[0], "razon": r[1] or r[0]} for r in rows.all()]


async def _total_periodo(session, contrib_id, periodo, proveedor) -> float:
    """Total OFICIAL del período = suma de los importes de los COMPROBANTES
    (mtoTotalCp), no de los ítems (los ítems traen impuestos/descuentos distintos y
    no siempre suman el total del CPE). Respeta el filtro de proveedor; el filtro de
    texto solo acota las filas de detalle, no este total."""
    stmt = select(func.coalesce(func.sum(ComprobanteCompra.importe_total), 0)).where(
        ComprobanteCompra.contribuyente_id == contrib_id,
        ComprobanteCompra.periodo == periodo)
    if proveedor:
        stmt = stmt.where(ComprobanteCompra.ruc_proveedor == proveedor)
    val = await session.scalar(stmt)
    return round(float(val or 0), 2)


async def _conteo_comprobantes(session, contrib_id, periodo, proveedor) -> int:
    stmt = select(func.count()).select_from(ComprobanteCompra).where(
        ComprobanteCompra.contribuyente_id == contrib_id,
        ComprobanteCompra.periodo == periodo)
    if proveedor:
        stmt = stmt.where(ComprobanteCompra.ruc_proveedor == proveedor)
    return int(await session.scalar(stmt) or 0)


async def _filas_raw(session, contrib_id, periodo, proveedor, q) -> list[dict]:
    """Una fila por ítem (sin enmascarar). Join item↔comprobante + filtros."""
    stmt = (
        select(ComprobanteItem, ComprobanteCompra)
        .join(ComprobanteCompra,
              ComprobanteItem.comprobante_id == ComprobanteCompra.id)
        .where(ComprobanteCompra.contribuyente_id == contrib_id,
               ComprobanteCompra.periodo == periodo)
        .order_by(ComprobanteCompra.fecha_emision.nullslast(),
                  ComprobanteCompra.serie, ComprobanteCompra.numero,
                  ComprobanteItem.linea))
    if proveedor:
        stmt = stmt.where(ComprobanteCompra.ruc_proveedor == proveedor)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(ComprobanteItem.descripcion.ilike(like))
    filas = []
    for it, c in (await session.execute(stmt)).all():
        comp_txt = f"{(c.desc_tipo_cdp or c.cod_tipo_cdp or '').strip()} " \
                   f"{c.serie or ''}-{c.numero or ''}".strip()
        filas.append({
            "fecha": c.fecha_emision,
            "fecha_txt": _fecha_txt(c.fecha_emision),
            "proveedor_ruc": c.ruc_proveedor or "",
            "proveedor": c.razon_social_proveedor or "",
            "comprobante": comp_txt,
            "descripcion": it.descripcion or "",
            "unidad": it.desc_unidad or it.unidad or "",
            "cantidad": _f(it.cantidad),
            "valor_unitario": _f(it.valor_unitario),
            "importe": _f(it.importe_total),
        })
    return filas


def _enmascarar(filas: list[dict], demo: bool, montos: bool) -> list[dict]:
    if not demo:
        return filas
    out = []
    for f in filas:
        g = dict(f)
        g["proveedor_ruc"] = dm.mask_ruc(f["proveedor_ruc"], True)
        g["proveedor"] = dm.mask_nombre(f["proveedor"], True)
        if montos:
            for k in ("valor_unitario", "importe"):
                g[k] = dm._MASK_MONTO
        out.append(g)
    return out


@router.get("/club/compras/{contribuyente_id}", response_class=HTMLResponse)
async def vista_compras(contribuyente_id: uuid.UUID, request: Request,
                        periodo: str = "", proveedor: str = "", q: str = "",
                        user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        contrib = await _puede_ver(session, user, contribuyente_id)
        if not contrib:
            return RedirectResponse("/cartera", status_code=303)

        periodos = await _periodos_de(session, contribuyente_id)
        if periodo not in periodos:
            periodo = periodos[0] if periodos else ""

        demo = dm.demo_activo(request)
        montos = dm.demo_montos(request)

        filas_raw = (await _filas_raw(session, contribuyente_id, periodo,
                                      proveedor, q)) if periodo else []
        total = (await _total_periodo(session, contribuyente_id, periodo,
                                      proveedor)) if periodo else 0.0
        n_comprobantes = (await _conteo_comprobantes(
            session, contribuyente_id, periodo, proveedor)) if periodo else 0
        proveedores = (await _proveedores_de(session, contribuyente_id, periodo)) \
            if periodo else []
        filas = _enmascarar(filas_raw, demo, montos)

        ctx = {
            "user": user,
            "contrib": contrib,
            "cliente_nombre": dm.mask_nombre(contrib.razon_social or contrib.ruc,
                                             demo, etiqueta="Cliente demostración"),
            "cliente_ruc": dm.mask_ruc(contrib.ruc, demo),
            "contrib_id": str(contribuyente_id),
            "periodo": periodo, "periodos": periodos,
            "periodo_label": (f"{periodo[4:6]}/{periodo[:4]}"
                              if len(periodo) == 6 else periodo),
            "proveedor": proveedor, "proveedores": proveedores, "q": q,
            "filas": filas, "n_items": len(filas),
            "n_comprobantes": n_comprobantes,
            "total": (dm._MASK_MONTO if montos else total),
            "demo": demo, "demo_montos": montos,
            "volver_url": request.url.path + ("?" + request.url.query
                                              if request.url.query else ""),
        }
        return templates.TemplateResponse(request, "club_compras.html", ctx)


@router.get("/club/compras/{contribuyente_id}/export.xlsx")
async def exportar_compras(contribuyente_id: uuid.UUID, request: Request,
                           periodo: str = "", proveedor: str = "", q: str = "",
                           user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        contrib = await _puede_ver(session, user, contribuyente_id)
        if not contrib:
            return RedirectResponse("/cartera", status_code=303)
        periodos = await _periodos_de(session, contribuyente_id)
        if periodo not in periodos:
            periodo = periodos[0] if periodos else ""

        demo = dm.demo_activo(request)
        montos = dm.demo_montos(request)
        filas_raw = (await _filas_raw(session, contribuyente_id, periodo,
                                      proveedor, q)) if periodo else []
        filas = _enmascarar(filas_raw, demo, montos)
        total = (await _total_periodo(session, contribuyente_id, periodo,
                                      proveedor)) if periodo else 0.0
        total_txt = dm._MASK_MONTO if montos else f"S/ {total:,.2f}"

        razon = dm.mask_nombre(contrib.razon_social or contrib.ruc, demo,
                               etiqueta="Cliente demostración")
        ruc = dm.mask_ruc(contrib.ruc, demo)
        wb = libro_compras(razon, ruc, periodo, filas,
                           generado_por=(getattr(user, "nombre", "") or ""),
                           alcance="Club de Contadores", demo=demo,
                           total_txt=total_txt)
        datos = libro_a_bytes(wb)
    nombre = nombre_archivo("compras", f"{ruc}_{periodo}")
    return Response(content=datos, media_type=_MEDIA_XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


def _volver(request: Request) -> str:
    ref = request.headers.get("referer")
    return ref if ref else "/cartera"


@router.get("/club/demo/toggle")
async def demo_toggle(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    activo = dm.demo_activo(request)
    resp = RedirectResponse(_volver(request), status_code=303)
    if activo:
        resp.delete_cookie(dm.COOKIE_DEMO, path="/")
        resp.delete_cookie(dm.COOKIE_DEMO_MONTOS, path="/")
    else:
        resp.set_cookie(dm.COOKIE_DEMO, "1", **_COOKIE_KW)
    return resp


@router.get("/club/demo/montos")
async def demo_montos_toggle(request: Request,
                             user: UsuarioActual = Depends(usuario_actual)):
    """Solo tiene efecto si el modo demostración está activo (casilla aparte)."""
    resp = RedirectResponse(_volver(request), status_code=303)
    if dm.demo_activo(request):
        if request.cookies.get(dm.COOKIE_DEMO_MONTOS) == "1":
            resp.delete_cookie(dm.COOKIE_DEMO_MONTOS, path="/")
        else:
            resp.set_cookie(dm.COOKIE_DEMO_MONTOS, "1", **_COOKIE_KW)
    return resp
