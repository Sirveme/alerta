"""
routers/comprobantes.py — CRUD de comprobantes con filtros y descarga desde GCS.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user, get_empresa_activa
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.comprobantes import Comprobante, DetalleComprobante, EstadoComprobante, TipoComprobante

router = APIRouter(prefix="/comprobantes", tags=["comprobantes"])


@router.get("/")
def listar_comprobantes(
    request: Request,
    tipo: Optional[str] = None,
    estado: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    ruc_emisor: Optional[str] = None,
    buscar: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Lista paginada de comprobantes con filtros."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    query = select(Comprobante).where(
        Comprobante.empresa_id == empresa.id,
        Comprobante.deleted_at == None,
    )

    if tipo:
        query = query.where(Comprobante.tipo == tipo)
    if estado:
        query = query.where(Comprobante.estado == estado)
    if fecha_desde:
        query = query.where(Comprobante.fecha_emision >= fecha_desde)
    if fecha_hasta:
        query = query.where(Comprobante.fecha_emision <= fecha_hasta)
    if ruc_emisor:
        query = query.where(Comprobante.ruc_emisor == ruc_emisor)

    # Conteo total
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Paginación
    query = query.order_by(Comprobante.fecha_emision.desc())
    query = query.offset((page - 1) * size).limit(size)
    comprobantes = db.execute(query).scalars().all()

    return {
        "items": [
            {
                "id": c.id,
                "tipo": c.tipo.value,
                "serie": c.serie,
                "correlativo": c.correlativo,
                "ruc_emisor": c.ruc_emisor,
                "razon_social_emisor": c.razon_social_emisor,
                "total": float(c.total),
                "fecha_emision": str(c.fecha_emision),
                "estado": c.estado.value,
            }
            for c in comprobantes
        ],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/pendientes")
def comprobantes_pendientes(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Comprobantes pendientes de cruce con pago."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    comprobantes = db.execute(
        select(Comprobante).where(
            Comprobante.empresa_id == empresa.id,
            Comprobante.estado == EstadoComprobante.PENDIENTE,
            Comprobante.deleted_at == None,
        ).order_by(Comprobante.fecha_emision.desc())
    ).scalars().all()

    return {
        "items": [
            {
                "id": c.id, "serie": c.serie, "correlativo": c.correlativo,
                "ruc_emisor": c.ruc_emisor, "total": float(c.total),
                "fecha_emision": str(c.fecha_emision),
            }
            for c in comprobantes
        ],
        "total": len(comprobantes),
    }


@router.get("/duplicados")
def comprobantes_duplicados(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Listado de duplicados para revisión."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    comprobantes = db.execute(
        select(Comprobante).where(
            Comprobante.empresa_id == empresa.id,
            Comprobante.estado == EstadoComprobante.DUPLICADO,
            Comprobante.deleted_at == None,
        ).order_by(Comprobante.created_at.desc())
    ).scalars().all()

    return {
        "items": [
            {
                "id": c.id, "serie": c.serie, "correlativo": c.correlativo,
                "ruc_emisor": c.ruc_emisor, "total": float(c.total),
                "fecha_emision": str(c.fecha_emision),
            }
            for c in comprobantes
        ],
        "total": len(comprobantes),
    }


@router.get("/{comprobante_id}")
def detalle_comprobante(
    comprobante_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detalle completo de un comprobante con líneas."""
    comprobante = db.execute(
        select(Comprobante).where(Comprobante.id == comprobante_id, Comprobante.deleted_at == None)
    ).scalar_one_or_none()

    if not comprobante:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    detalles = db.execute(
        select(DetalleComprobante).where(
            DetalleComprobante.comprobante_id == comprobante_id
        ).order_by(DetalleComprobante.numero_linea)
    ).scalars().all()

    return {
        "id": comprobante.id,
        "tipo": comprobante.tipo.value,
        "serie": comprobante.serie,
        "correlativo": comprobante.correlativo,
        "ruc_emisor": comprobante.ruc_emisor,
        "razon_social_emisor": comprobante.razon_social_emisor,
        "ruc_receptor": comprobante.ruc_receptor,
        "razon_social_receptor": comprobante.razon_social_receptor,
        "moneda": comprobante.moneda,
        "subtotal": float(comprobante.subtotal),
        "igv": float(comprobante.igv),
        "total": float(comprobante.total),
        "fecha_emision": str(comprobante.fecha_emision),
        "fecha_vencimiento": str(comprobante.fecha_vencimiento) if comprobante.fecha_vencimiento else None,
        "estado": comprobante.estado.value,
        "hash_cpe": comprobante.hash_cpe,
        "detalles": [
            {
                "numero_linea": d.numero_linea,
                "descripcion": d.descripcion,
                "cantidad": float(d.cantidad),
                "precio_unitario": float(d.precio_unitario),
                "igv_monto": float(d.igv_monto),
                "total_linea": float(d.total_linea),
                "categoria_ia": d.categoria_ia,
                "es_deducible": d.es_deducible,
            }
            for d in detalles
        ],
    }


@router.get("/{comprobante_id}/xml")
def descargar_xml(
    comprobante_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera URL firmada para descargar XML original desde GCS."""
    from app.services.gcs_service import obtener_url_firmada

    comprobante = db.execute(
        select(Comprobante).where(Comprobante.id == comprobante_id)
    ).scalar_one_or_none()

    if not comprobante:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == comprobante.empresa_id)
    ).scalar_one_or_none()

    gcs_path = (
        f"gs://alertape-docs/docs/{empresa.ruc if empresa else 'unknown'}/"
        f"{comprobante.fecha_emision.year}/{comprobante.fecha_emision.month:02d}/"
        f"{comprobante.tipo.value}/{comprobante.serie}-{comprobante.correlativo}.xml"
    )

    url = obtener_url_firmada(gcs_path)
    if not url:
        raise HTTPException(status_code=404, detail="Archivo XML no disponible")

    return {"url": url}


@router.get("/{comprobante_id}/pdf")
def descargar_pdf(
    comprobante_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera URL firmada para descargar PDF desde GCS."""
    from app.services.gcs_service import obtener_url_firmada

    comprobante = db.execute(
        select(Comprobante).where(Comprobante.id == comprobante_id)
    ).scalar_one_or_none()

    if not comprobante:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == comprobante.empresa_id)
    ).scalar_one_or_none()

    gcs_path = (
        f"gs://alertape-docs/docs/{empresa.ruc if empresa else 'unknown'}/"
        f"{comprobante.fecha_emision.year}/{comprobante.fecha_emision.month:02d}/"
        f"{comprobante.tipo.value}/{comprobante.serie}-{comprobante.correlativo}.pdf"
    )

    url = obtener_url_firmada(gcs_path)
    if not url:
        raise HTTPException(status_code=404, detail="Archivo PDF no disponible")

    return {"url": url}


@router.put("/{comprobante_id}/estado")
def cambiar_estado(
    comprobante_id: int,
    body: dict,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cambiar estado de un comprobante manualmente."""
    comprobante = db.execute(
        select(Comprobante).where(Comprobante.id == comprobante_id, Comprobante.deleted_at == None)
    ).scalar_one_or_none()

    if not comprobante:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    nuevo_estado = body.get("estado")
    try:
        comprobante.estado = EstadoComprobante(nuevo_estado)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Estado inválido: {nuevo_estado}")

    db.commit()
    return {"detail": "Estado actualizado", "estado": comprobante.estado.value}
