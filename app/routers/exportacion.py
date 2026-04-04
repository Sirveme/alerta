"""
routers/exportacion.py — Endpoints para empresas exportadoras.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario

router = APIRouter(prefix="/exportacion", tags=["exportación"])


@router.get("/{empresa_id}/drawback/{anio}")
def reporte_drawback(
    empresa_id: int, anio: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reporte drawback anual."""
    from app.services.exportacion_service import generar_reporte_exportaciones
    return generar_reporte_exportaciones(db, empresa_id, anio)


@router.get("/{empresa_id}/saldo-favor/{periodo}")
def saldo_favor_exportador(
    empresa_id: int, periodo: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Saldo a favor del exportador para el período."""
    from app.services.exportacion_service import calcular_saldo_favor_exportador
    return calcular_saldo_favor_exportador(db, empresa_id, periodo)
