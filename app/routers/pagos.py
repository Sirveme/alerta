"""
routers/pagos.py — CRUD de pagos con filtros, cruce manual, resumen mensual.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user, get_empresa_activa
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.pagos import Pago, EstadoPago, CanalPago
from app.models.comprobantes import Comprobante, EstadoComprobante

router = APIRouter(prefix="/pagos", tags=["pagos"])


@router.get("/")
def listar_pagos(
    request: Request,
    canal: Optional[str] = None,
    estado: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Lista paginada de pagos con filtros."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    query = select(Pago).where(
        Pago.empresa_id == empresa.id,
        Pago.deleted_at == None,
    )

    if canal:
        query = query.where(Pago.canal == canal)
    if estado:
        query = query.where(Pago.estado == estado)
    if fecha_desde:
        query = query.where(Pago.fecha_pago >= fecha_desde)
    if fecha_hasta:
        query = query.where(Pago.fecha_pago <= fecha_hasta)

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    query = query.order_by(Pago.fecha_pago.desc())
    query = query.offset((page - 1) * size).limit(size)
    pagos = db.execute(query).scalars().all()

    return {
        "items": [
            {
                "id": p.id,
                "monto": float(p.monto),
                "moneda": p.moneda,
                "canal": p.canal.value,
                "estado": p.estado.value,
                "fecha_pago": str(p.fecha_pago),
                "pagador_nombre": p.pagador_nombre,
                "numero_operacion": p.numero_operacion,
                "comprobante_id": p.comprobante_id,
            }
            for p in pagos
        ],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/sin-comprobante")
def pagos_sin_comprobante(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Pagos no cruzados con comprobante."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    pagos = db.execute(
        select(Pago).where(
            Pago.empresa_id == empresa.id,
            Pago.estado == EstadoPago.SIN_COMPROBANTE,
            Pago.deleted_at == None,
        ).order_by(Pago.fecha_pago.desc())
    ).scalars().all()

    return {
        "items": [
            {
                "id": p.id, "monto": float(p.monto), "canal": p.canal.value,
                "fecha_pago": str(p.fecha_pago), "pagador_nombre": p.pagador_nombre,
            }
            for p in pagos
        ],
        "total": len(pagos),
    }


@router.get("/resumen-mes")
def resumen_mes(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Totales por canal del mes activo."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    resultados = db.execute(
        select(
            Pago.canal,
            func.sum(Pago.monto).label("total"),
            func.count(Pago.id).label("cantidad"),
        ).where(
            Pago.empresa_id == empresa.id,
            Pago.deleted_at == None,
            func.extract("month", Pago.fecha_pago) == now.month,
            func.extract("year", Pago.fecha_pago) == now.year,
        ).group_by(Pago.canal)
    ).all()

    return {
        "mes": now.month,
        "anio": now.year,
        "canales": [
            {"canal": r[0].value, "total": float(r[1] or 0), "cantidad": r[2]}
            for r in resultados
        ],
    }


@router.get("/{pago_id}")
def detalle_pago(
    pago_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detalle de un pago."""
    pago = db.execute(
        select(Pago).where(Pago.id == pago_id, Pago.deleted_at == None)
    ).scalar_one_or_none()

    if not pago:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    return {
        "id": pago.id,
        "empresa_id": pago.empresa_id,
        "monto": float(pago.monto),
        "moneda": pago.moneda,
        "canal": pago.canal.value,
        "estado": pago.estado.value,
        "fecha_pago": str(pago.fecha_pago),
        "pagador_nombre": pago.pagador_nombre,
        "pagador_documento": pago.pagador_documento,
        "pagador_telefono": pago.pagador_telefono,
        "numero_operacion": pago.numero_operacion,
        "referencia_banco": pago.referencia_banco,
        "comprobante_id": pago.comprobante_id,
        "notas": pago.notas,
    }


@router.put("/{pago_id}/cruzar")
def cruzar_manual(
    pago_id: int,
    body: dict,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cruzar un pago manualmente con un comprobante específico."""
    comprobante_id = body.get("comprobante_id")
    if not comprobante_id:
        raise HTTPException(status_code=400, detail="comprobante_id requerido")

    pago = db.execute(
        select(Pago).where(Pago.id == pago_id, Pago.deleted_at == None)
    ).scalar_one_or_none()
    if not pago:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    comprobante = db.execute(
        select(Comprobante).where(Comprobante.id == comprobante_id, Comprobante.deleted_at == None)
    ).scalar_one_or_none()
    if not comprobante:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    pago.estado = EstadoPago.CRUZADO
    pago.comprobante_id = comprobante_id
    comprobante.estado = EstadoComprobante.VALIDADO
    db.commit()

    return {"detail": "Cruce realizado", "pago_id": pago_id, "comprobante_id": comprobante_id}
