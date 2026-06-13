"""Dashboard del contador con cards de clientes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_authenticated
from app.core.templates import templates
from app.models import ClienteRuc, MensajeBuzon, Usuario
from app.services.cliente_ruc_service import (
    listar_clientes_del_contador,
    obtener_plan_del_contador,
)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def vista_dashboard(
    request: Request,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    clientes = await listar_clientes_del_contador(db, usuario.id)
    plan = await obtener_plan_del_contador(db, usuario.id)

    # Contar mensajes nuevos por cliente
    mensajes_nuevos_por_cliente = {}
    if clientes:
        ids = [c.id for c in clientes]
        result = await db.execute(
            select(
                MensajeBuzon.cliente_ruc_id,
                func.count(MensajeBuzon.id).label("nuevos"),
            )
            .where(MensajeBuzon.cliente_ruc_id.in_(ids))
            .where(MensajeBuzon.visto == False)
            .where(MensajeBuzon.archivado == False)
            .group_by(MensajeBuzon.cliente_ruc_id)
        )
        for cid, nuevos in result.all():
            mensajes_nuevos_por_cliente[cid] = nuevos

    # Total mensajes nuevos
    total_nuevos = sum(mensajes_nuevos_por_cliente.values())

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "usuario": usuario,
            "plan": plan,
            "clientes": clientes,
            "mensajes_nuevos_por_cliente": mensajes_nuevos_por_cliente,
            "total_nuevos": total_nuevos,
        },
    )
