"""
routers/ple.py — Descarga de Libros Electrónicos TXT SUNAT.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario

router = APIRouter(prefix="/ple", tags=["PLE"])


@router.get("/{empresa_id}/{periodo}/ventas")
def descargar_ple_ventas(
    empresa_id: int, periodo: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Descarga TXT Registro de Ventas (LE140100)."""
    from app.services.ple_service import generar_ple_ventas
    contenido, nombre = generar_ple_ventas(db, empresa_id, periodo)
    return PlainTextResponse(
        content=contenido,
        media_type="text/plain; charset=iso-8859-1",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/{empresa_id}/{periodo}/compras")
def descargar_ple_compras(
    empresa_id: int, periodo: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Descarga TXT Registro de Compras (LE080100)."""
    from app.services.ple_service import generar_ple_compras
    contenido, nombre = generar_ple_compras(db, empresa_id, periodo)
    return PlainTextResponse(
        content=contenido,
        media_type="text/plain; charset=iso-8859-1",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/{empresa_id}/{periodo}/validar")
def validar_ple(
    empresa_id: int, periodo: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Valida PLE sin descargar. Retorna errores si los hay."""
    from app.services.ple_service import generar_ple_ventas, generar_ple_compras

    errores = []
    try:
        generar_ple_ventas(db, empresa_id, periodo)
    except Exception as e:
        errores.append({"libro": "ventas", "error": str(e)})

    try:
        generar_ple_compras(db, empresa_id, periodo)
    except Exception as e:
        errores.append({"libro": "compras", "error": str(e)})

    return {"valido": len(errores) == 0, "errores": errores}
