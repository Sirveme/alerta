"""
routers/comparativo_page.py — Comparativo mensual ventas vs SIRE vs pagos.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user, get_empresa_activa
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.comprobantes import Comprobante
from app.models.pagos import Pago

router = APIRouter(tags=["comparativo"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/comparativo", response_class=HTMLResponse)
def comparativo_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("comparativo/index.html", {
        "request": request, "user": request.state.user,
    })


@router.get("/comparativo/datos/{empresa_id}/{periodo}")
def comparativo_datos(
    empresa_id: int,
    periodo: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """JSON con datos para el comparativo (HTMX). Periodo formato: 2026-04."""
    # Comprobantes del periodo en el sistema
    comprobantes = db.execute(
        select(
            func.count(Comprobante.id).label("cantidad"),
            func.coalesce(func.sum(Comprobante.subtotal), 0).label("subtotal"),
            func.coalesce(func.sum(Comprobante.igv), 0).label("igv"),
            func.coalesce(func.sum(Comprobante.total), 0).label("total"),
        ).where(
            Comprobante.empresa_id == empresa_id,
            func.to_char(Comprobante.fecha_emision, 'YYYY-MM') == periodo,
            Comprobante.deleted_at == None,
        )
    ).first()

    # Pagos del periodo
    pagos = db.execute(
        select(
            func.coalesce(func.sum(Pago.monto), 0).label("total_pagos"),
        ).where(
            Pago.empresa_id == empresa_id,
            func.to_char(Pago.fecha_pago, 'YYYY-MM') == periodo,
            Pago.deleted_at == None,
        )
    ).first()

    return {
        "periodo": periodo,
        "sistema": {
            "comprobantes": comprobantes.cantidad if comprobantes else 0,
            "subtotal": float(comprobantes.subtotal) if comprobantes else 0,
            "igv": float(comprobantes.igv) if comprobantes else 0,
            "total": float(comprobantes.total) if comprobantes else 0,
        },
        "pagos": {
            "total": float(pagos.total_pagos) if pagos else 0,
        },
        "sire": {
            "comprobantes": 0,
            "total": 0,
            "igv": 0,
            "nota": "Sincronizacion SIRE pendiente",
        },
    }
