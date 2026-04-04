"""
routers/correccion.py — Endpoints de seguimiento de corrección de comprobantes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user, get_empresa_activa
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.contabilidad import SeguimientoCorreccion, EstadoCorreccion

router = APIRouter(prefix="/correccion", tags=["corrección comprobantes"])


@router.post("/{comprobante_id}/iniciar")
def iniciar_correccion(
    comprobante_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Inicia proceso de corrección para un comprobante bloqueado."""
    from app.services.correccion_service import iniciar_proceso_correccion
    try:
        seguimiento = iniciar_proceso_correccion(db, comprobante_id)
        return {"id": seguimiento.id, "detail": "Proceso de corrección iniciado", "nivel": 1}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{seguimiento_id}/escalar")
def escalar_manualmente(
    seguimiento_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Escalar manualmente al siguiente nivel."""
    from app.services.correccion_service import escalar_nivel
    try:
        escalar_nivel(db, seguimiento_id)
        seg = db.execute(select(SeguimientoCorreccion).where(SeguimientoCorreccion.id == seguimiento_id)).scalar_one()
        return {"detail": f"Escalado a nivel {seg.nivel_actual}", "nivel": seg.nivel_actual}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{seguimiento_id}/nc-recibida")
def nc_recibida(
    seguimiento_id: int,
    body: dict,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registra que se recibió la Nota de Crédito del proveedor."""
    nc_comprobante_id = body.get("nc_comprobante_id")
    if not nc_comprobante_id:
        raise HTTPException(status_code=400, detail="nc_comprobante_id requerido")
    from app.services.correccion_service import registrar_nc_recibida
    registrar_nc_recibida(db, seguimiento_id, nc_comprobante_id)
    return {"detail": "NC registrada, estado cambiado a corregido"}


@router.get("/pendientes")
def seguimientos_pendientes(
    current_user: Usuario = Depends(get_current_user),
    empresa: EmpresaCliente = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Seguimientos activos de corrección."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    items = db.execute(
        select(SeguimientoCorreccion).where(
            SeguimientoCorreccion.empresa_id == empresa.id,
            SeguimientoCorreccion.estado.in_([
                EstadoCorreccion.PENDIENTE, EstadoCorreccion.CONTACTADO, EstadoCorreccion.EN_PROCESO
            ]),
        ).order_by(SeguimientoCorreccion.created_at.desc())
    ).scalars().all()

    return {
        "items": [
            {
                "id": s.id, "comprobante_id": s.comprobante_id,
                "ruc_proveedor": s.ruc_proveedor, "nombre_proveedor": s.nombre_proveedor,
                "nivel_actual": s.nivel_actual, "estado": s.estado.value,
                "fecha_ultimo_contacto": str(s.fecha_ultimo_contacto) if s.fecha_ultimo_contacto else None,
            }
            for s in items
        ],
        "total": len(items),
    }


@router.get("/proveedores-reincidentes")
def proveedores_reincidentes(
    current_user: Usuario = Depends(get_current_user),
    empresa: EmpresaCliente = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Proveedores con errores frecuentes (3+ en 60 días)."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    from app.services.correccion_service import detectar_proveedor_reincidente
    from sqlalchemy import func, distinct

    # Obtener RUCs con más de 2 seguimientos
    rucs = db.execute(
        select(SeguimientoCorreccion.ruc_proveedor, func.count(SeguimientoCorreccion.id).label("cnt")).where(
            SeguimientoCorreccion.empresa_id == empresa.id,
        ).group_by(SeguimientoCorreccion.ruc_proveedor).having(func.count(SeguimientoCorreccion.id) >= 3)
    ).all()

    return {
        "items": [
            {"ruc": r[0], "cantidad_errores": r[1]}
            for r in rucs
        ],
    }
