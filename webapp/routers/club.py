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
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, func

from db import get_session
from models import (Contribuyente, ComprobanteCompra, ComprobanteItem,
                    DeclaracionJurada, PagoTributario, CronogramaVencimiento,
                    PadronBuenContribuyente)
from ..deps import UsuarioActual, usuario_actual
from ..core import templates
from .. import demo_mask as dm
from ..excel_export import libro_compras, libro_a_bytes, nombre_archivo
from .cliente import _puede_ver
import cronograma as cg

router = APIRouter(tags=["club"])

_MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Cookie de SESIÓN (sin max_age/expires): se borra al cerrar el navegador, así cada
# sesión nueva arranca en modo reservado (enmascarada).
_COOKIE_KW = dict(path="/", httponly=True, samesite="lax")


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


def _enmascarar(filas: list[dict], oculto: bool, montos_ocultos: bool) -> list[dict]:
    if not oculto and not montos_ocultos:
        return filas
    out = []
    for f in filas:
        g = dict(f)
        if oculto:
            g["proveedor_ruc"] = dm.mask_ruc(f["proveedor_ruc"], True)
            g["proveedor"] = dm.mask_nombre(f["proveedor"], True)
        if montos_ocultos:
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

        oculto = dm.identidad_oculta(request)
        montos_ocultos = dm.montos_ocultos(request)

        filas_raw = (await _filas_raw(session, contribuyente_id, periodo,
                                      proveedor, q)) if periodo else []
        total = (await _total_periodo(session, contribuyente_id, periodo,
                                      proveedor)) if periodo else 0.0
        n_comprobantes = (await _conteo_comprobantes(
            session, contribuyente_id, periodo, proveedor)) if periodo else 0
        proveedores = (await _proveedores_de(session, contribuyente_id, periodo)) \
            if periodo else []
        filas = _enmascarar(filas_raw, oculto, montos_ocultos)

        ctx = {
            "user": user,
            "contrib": contrib,
            "cliente_nombre": dm.mask_nombre(contrib.razon_social or contrib.ruc,
                                             oculto, etiqueta=dm._PLACEHOLDER_NOMBRE),
            "cliente_ruc": dm.mask_ruc(contrib.ruc, oculto),
            "contrib_id": str(contribuyente_id),
            "periodo": periodo, "periodos": periodos,
            "periodo_label": (f"{periodo[4:6]}/{periodo[:4]}"
                              if len(periodo) == 6 else periodo),
            "proveedor": proveedor, "proveedores": proveedores, "q": q,
            "filas": filas, "n_items": len(filas),
            "n_comprobantes": n_comprobantes,
            "total": (dm._MASK_MONTO if montos_ocultos else total),
            "oculto": oculto, "montos_ocultos": montos_ocultos,
            "tooltip_reserva": dm.TOOLTIP_RESERVA,
            "nota": ("Aquí ves las compras de tu cliente con la descripción de cada "
                     "producto, tal como las reporta SUNAT (Registro de Compras "
                     "Electrónico). Filtra por proveedor o texto y descárgalas a Excel."),
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

        oculto = dm.identidad_oculta(request)
        montos_ocultos = dm.montos_ocultos(request)
        filas_raw = (await _filas_raw(session, contribuyente_id, periodo,
                                      proveedor, q)) if periodo else []
        filas = _enmascarar(filas_raw, oculto, montos_ocultos)
        total = (await _total_periodo(session, contribuyente_id, periodo,
                                      proveedor)) if periodo else 0.0
        total_txt = dm._MASK_MONTO if montos_ocultos else f"S/ {total:,.2f}"

        razon = dm.mask_nombre(contrib.razon_social or contrib.ruc, oculto,
                               etiqueta=dm._PLACEHOLDER_NOMBRE)
        ruc = dm.mask_ruc(contrib.ruc, oculto)
        wb = libro_compras(razon, ruc, periodo, filas,
                           generado_por=(getattr(user, "nombre", "") or ""),
                           alcance="Club de Contadores", demo=(oculto or montos_ocultos),
                           total_txt=total_txt)
        datos = libro_a_bytes(wb)
    nombre = nombre_archivo("compras", f"{ruc}_{periodo}")
    return Response(content=datos, media_type=_MEDIA_XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


def _volver(request: Request) -> str:
    ref = request.headers.get("referer")
    return ref if ref else "/cartera"


# ───────────────────────── Rejilla 12 meses ─────────────────────────
def _ultimos_periodos(n: int = 12) -> list[str]:
    """Últimos `n` períodos AAAAMM, del más reciente al más antiguo."""
    hoy = date.today()
    y, m = hoy.year, hoy.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


_CHIP = {
    cg.PAGO_DENTRO:   ("chip--ok",    "Al día",     "Declaró y pagó dentro del plazo."),
    cg.PAGO_FUERA:    ("chip--tarde", "Pagó tarde", "Pagó después del vencimiento."),
    cg.SIN_PAGO:      ("chip--decl",  "Declaró",    "Declaró; sin pago registrado en SUNAT."),
    cg.SIN_VENCIMIENTO: ("chip--decl", "Declaró",   "Declaró; sin cronograma cargado para evaluar el plazo."),
}
_CHIP_SINDJ = ("chip--sindj", "Sin DJ", "No hay declaración jurada vigente para el período.")


async def _es_bc(session, ruc) -> bool | None:
    n = await session.scalar(select(func.count()).select_from(PadronBuenContribuyente))
    if not n:
        return None                      # padrón no cargado → conservador
    hit = await session.scalar(select(PadronBuenContribuyente.id).where(
        PadronBuenContribuyente.ruc == str(ruc),
        PadronBuenContribuyente.vigente.is_(True)))
    return bool(hit)


async def _rejilla_datos(session, contrib, periodos: list[str]) -> list[dict]:
    """Una fila por período: declaró / pagó / estado (clasificar_pago) / importe."""
    cid, ruc = contrib.id, contrib.ruc
    digito = cg.digito_ruc(ruc)
    es_bc = await _es_bc(session, ruc)

    # Declaraciones vigentes (declaró + fecha de presentación).
    decl = {}
    for p, f in (await session.execute(
            select(DeclaracionJurada.periodo,
                   func.min(DeclaracionJurada.fecha_presentacion))
            .where(DeclaracionJurada.contribuyente_id == cid,
                   DeclaracionJurada.periodo.in_(periodos),
                   DeclaracionJurada.es_vigente.is_(True))
            .group_by(DeclaracionJurada.periodo))).all():
        decl[p] = f

    # Pagos (fecha representativa = la más temprana; importe = suma).
    pago = {}
    for p, fp, imp in (await session.execute(
            select(PagoTributario.periodo,
                   func.min(PagoTributario.fecha_pago),
                   func.coalesce(func.sum(PagoTributario.importe), 0))
            .where(PagoTributario.contribuyente_id == cid,
                   PagoTributario.periodo.in_(periodos))
            .group_by(PagoTributario.periodo))).all():
        pago[p] = (fp, float(imp or 0))

    # Cronograma por período (base + prórrogas) para el último dígito del RUC.
    cronos: dict[str, list] = {}
    if digito is not None:
        for row in (await session.scalars(
                select(CronogramaVencimiento).where(
                    CronogramaVencimiento.periodo.in_(periodos),
                    CronogramaVencimiento.digito_ruc == digito))):
            cronos.setdefault(row.periodo, []).append(row)

    filas = []
    for p in periodos:
        declaro = p in decl
        fp, importe = pago.get(p, (None, 0.0))
        pago_hay = p in pago
        if not declaro:
            chip, label, tip = _CHIP_SINDJ
            venc = cg.vencimiento_aplicable(cronos.get(p, []), es_bc)
        else:
            clasif = cg.clasificar_pago(ruc, fp, cronos.get(p, []), es_bc)
            chip, label, tip = _CHIP.get(clasif["estado"], _CHIP[cg.SIN_VENCIMIENTO])
            venc = clasif["vencimiento"]
        filas.append({
            "periodo": p, "periodo_label": f"{p[4:6]}/{p[:4]}",
            "declaro": declaro,
            "fecha_pres": decl.get(p),
            "pago": pago_hay, "fecha_pago": fp,
            "importe": importe if pago_hay else None,
            "vencimiento": venc,
            "chip": chip, "estado_label": label, "estado_tip": tip,
        })
    return filas


@router.get("/club/rejilla/{contribuyente_id}", response_class=HTMLResponse)
async def vista_rejilla(contribuyente_id: uuid.UUID, request: Request,
                        user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        contrib = await _puede_ver(session, user, contribuyente_id)
        if not contrib:
            return RedirectResponse("/cartera", status_code=303)

        oculto = dm.identidad_oculta(request)
        montos_ocultos = dm.montos_ocultos(request)
        periodos = _ultimos_periodos(12)
        filas = await _rejilla_datos(session, contrib, periodos)

        def _fmt_fecha(d):
            return d.strftime("%d/%m/%Y") if d else ""
        vista = []
        for f in filas:
            g = dict(f)
            g["fecha_pres_txt"] = _fmt_fecha(f["fecha_pres"])
            g["fecha_pago_txt"] = _fmt_fecha(f["fecha_pago"])
            g["venc_txt"] = _fmt_fecha(f["vencimiento"])
            if montos_ocultos and f["importe"] is not None:
                g["importe"] = dm._MASK_MONTO
            vista.append(g)

        ctx = {
            "user": user, "contrib": contrib,
            "cliente_nombre": dm.mask_nombre(contrib.razon_social or contrib.ruc,
                                             oculto, etiqueta=dm._PLACEHOLDER_NOMBRE),
            "cliente_ruc": dm.mask_ruc(contrib.ruc, oculto),
            "contrib_id": str(contribuyente_id),
            "filas": vista,
            "oculto": oculto, "montos_ocultos": montos_ocultos,
            "tooltip_reserva": dm.TOOLTIP_RESERVA,
            "nota": ("Aquí ves, mes a mes, si tu cliente declaró y pagó a tiempo. "
                     "El estado sale de SUNAT (Declaraciones y Pagos) comparado con "
                     "el cronograma oficial de vencimientos."),
        }
        return templates.TemplateResponse(request, "club_rejilla.html", ctx)


@router.get("/club/reservado/mostrar")
async def reservado_mostrar(request: Request,
                            user: UsuarioActual = Depends(usuario_actual)):
    """Casilla 'Mostrar datos reservados': revela/oculta RUC y razón social. Cookie
    de SESIÓN → al cerrar el navegador vuelve a ocultarse."""
    resp = RedirectResponse(_volver(request), status_code=303)
    if dm.mostrar_reservados(request):
        resp.delete_cookie(dm.COOKIE_MOSTRAR, path="/")      # volver a ocultar
    else:
        resp.set_cookie(dm.COOKIE_MOSTRAR, "1", **_COOKIE_KW)
    return resp


@router.get("/club/reservado/montos")
async def reservado_montos(request: Request,
                           user: UsuarioActual = Depends(usuario_actual)):
    """Casilla 'Mostrar montos' (aparte). Por defecto los montos se ven; marcarla
    como oculta los esconde."""
    resp = RedirectResponse(_volver(request), status_code=303)
    if dm.montos_ocultos(request):
        resp.delete_cookie(dm.COOKIE_OCULTAR_MONTOS, path="/")  # volver a mostrar
    else:
        resp.set_cookie(dm.COOKIE_OCULTAR_MONTOS, "1", **_COOKIE_KW)
    return resp
