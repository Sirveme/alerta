"""
routers/tipo_cambio.py — Consulta de tipo de cambio BCRP.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario

router = APIRouter(prefix="/tipo-cambio", tags=["tipo de cambio"])


@router.get("/{fecha}")
async def obtener_tipo_cambio(
    fecha: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """TC oficial BCRP de una fecha (YYYY-MM-DD)."""
    try:
        fecha_date = date.fromisoformat(fecha)
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha: YYYY-MM-DD")

    from app.services.tipo_cambio_service import obtener_tc_fecha
    compra, venta = await obtener_tc_fecha(fecha_date, db)

    return {
        "fecha": str(fecha_date),
        "compra": float(compra),
        "venta": float(venta),
        "fuente": "BCRP/SBS",
    }
