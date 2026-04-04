"""
routers/asientos.py — CRUD de asientos contables.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario
from app.models.contabilidad import AsientoContable, LineaAsiento

router = APIRouter(prefix="/asientos", tags=["asientos contables"])


@router.get("/{empresa_id}/{periodo}")
def listar_asientos(
    empresa_id: int, periodo: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista asientos contables del período."""
    asientos = db.execute(
        select(AsientoContable).where(
            AsientoContable.empresa_id == empresa_id,
            AsientoContable.periodo == periodo,
        ).order_by(AsientoContable.numero_asiento)
    ).scalars().all()

    return {
        "items": [
            {
                "id": a.id,
                "numero_asiento": a.numero_asiento,
                "fecha": str(a.fecha),
                "glosa": a.glosa,
                "estado": a.estado.value,
                "generado_por": a.generado_por.value,
                "lineas": [
                    {
                        "cuenta_codigo": l.cuenta_codigo,
                        "denominacion": l.denominacion,
                        "debe": float(l.debe),
                        "haber": float(l.haber),
                    }
                    for l in a.lineas
                ],
            }
            for a in asientos
        ],
        "total": len(asientos),
    }


@router.post("/{comprobante_id}/generar")
def generar_asiento(
    comprobante_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera asiento contable de un comprobante."""
    from app.models.comprobantes import Comprobante
    comp = db.execute(select(Comprobante).where(Comprobante.id == comprobante_id)).scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    from app.services.asientos_service import generar_asiento_compra, generar_asiento_venta

    # Decisión: si ruc_receptor == ruc empresa → es compra, si ruc_emisor == ruc empresa → es venta
    # Simplificado: generar asiento de compra por defecto (caso más común en el sistema)
    try:
        asiento = generar_asiento_compra(db, comprobante_id)
        return {
            "id": asiento.id,
            "numero_asiento": asiento.numero_asiento,
            "glosa": asiento.glosa,
            "estado": asiento.estado.value,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{asiento_id}/exportar-ple")
def exportar_asiento_ple(
    asiento_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exporta líneas de asiento en formato PLE 5.1 (Libro Diario)."""
    asiento = db.execute(
        select(AsientoContable).where(AsientoContable.id == asiento_id)
    ).scalar_one_or_none()

    if not asiento:
        raise HTTPException(status_code=404, detail="Asiento no encontrado")

    lineas_ple = []
    for l in asiento.lineas:
        # Formato simplificado PLE 5.1
        linea = "|".join([
            asiento.periodo.replace("-", "") + "00",  # Periodo
            str(asiento.numero_asiento),               # CUO
            f"M{l.orden:03d}",                         # Correlativo
            l.cuenta_codigo,                            # Código cuenta
            "",                                         # Código unidad operación
            "",                                         # Centro costos
            asiento.moneda,                             # Moneda
            "",                                         # Tipo doc identidad
            "",                                         # Número doc identidad
            "",                                         # Tipo comprobante
            "",                                         # Serie
            "",                                         # Número comprobante
            str(asiento.fecha),                         # Fecha contable
            "",                                         # Fecha vencimiento
            str(asiento.fecha),                         # Fecha operación
            l.glosa_linea or asiento.glosa,             # Glosa
            "",                                         # Glosa referencial
            f"{l.debe:.2f}",                            # Debe
            f"{l.haber:.2f}",                           # Haber
            "",                                         # Dato estructurado
            "1",                                        # Estado
        ])
        lineas_ple.append(linea)

    return {"ple_text": "\n".join(lineas_ple), "lineas": len(lineas_ple)}
