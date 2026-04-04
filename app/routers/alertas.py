"""
routers/alertas.py — CRUD de alertas, marcar leídas, conteo para badge.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user, get_empresa_activa
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.alertas import Alerta, EstadoAlerta

router = APIRouter(prefix="/alertas", tags=["alertas"])


@router.get("/")
def listar_alertas(
    request: Request,
    nivel: Optional[str] = None,
    estado: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Lista de alertas con filtros."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    query = select(Alerta).where(
        Alerta.empresa_id == empresa.id,
        Alerta.deleted_at == None,
    )

    if estado:
        query = query.where(Alerta.estado == estado)

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    query = query.order_by(Alerta.created_at.desc())
    query = query.offset((page - 1) * size).limit(size)
    alertas = db.execute(query).scalars().all()

    return {
        "items": [
            {
                "id": a.id,
                "origen": a.origen.value,
                "estado": a.estado.value,
                "titulo": a.titulo,
                "descripcion": a.descripcion,
                "codigo_entidad": a.codigo_entidad,
                "comprobante_id": a.comprobante_id,
                "pago_id": a.pago_id,
                "created_at": str(a.created_at),
            }
            for a in alertas
        ],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/no-leidas/count")
def count_no_leidas(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Conteo de alertas activas (no leídas) para el badge de la cabecera."""
    if not empresa:
        return {"count": 0}

    count = db.execute(
        select(func.count(Alerta.id)).where(
            Alerta.empresa_id == empresa.id,
            Alerta.estado == EstadoAlerta.ACTIVA,
            Alerta.deleted_at == None,
        )
    ).scalar() or 0

    return {"count": count}


@router.put("/{alerta_id}/leer")
def marcar_leida(
    alerta_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marca una alerta como en_revision (leída)."""
    alerta = db.execute(
        select(Alerta).where(Alerta.id == alerta_id, Alerta.deleted_at == None)
    ).scalar_one_or_none()

    if not alerta:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")

    if alerta.estado == EstadoAlerta.ACTIVA:
        alerta.estado = EstadoAlerta.EN_REVISION
        db.commit()

    return {"detail": "Alerta marcada como leída"}


@router.put("/leer-todas")
def marcar_todas_leidas(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    empresa: Optional[EmpresaCliente] = Depends(get_empresa_activa),
    db: Session = Depends(get_db),
):
    """Marca todas las alertas activas como leídas."""
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    db.execute(
        update(Alerta).where(
            Alerta.empresa_id == empresa.id,
            Alerta.estado == EstadoAlerta.ACTIVA,
            Alerta.deleted_at == None,
        ).values(estado=EstadoAlerta.EN_REVISION)
    )
    db.commit()

    return {"detail": "Todas las alertas marcadas como leídas"}
