"""Configuración del contador: horarios + notificaciones."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_authenticated
from app.core.templates import templates
from app.models import ConfiguracionPolling, Usuario
from app.services.cliente_ruc_service import obtener_plan_del_contador

router = APIRouter(prefix="/configuracion", tags=["configuracion"])


@router.get("", response_class=HTMLResponse)
async def vista_configuracion(
    request: Request,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    plan = await obtener_plan_del_contador(db, usuario.id)

    # Cargar config de polling existente
    config_result = await db.execute(
        select(ConfiguracionPolling).where(ConfiguracionPolling.usuario_id == usuario.id)
    )
    config = config_result.scalar_one_or_none()

    horarios = config.horarios if config else ["08:00", "12:00", "16:00"]
    tipo_dias = config.tipo_dias if config else "interdiario"

    return templates.TemplateResponse(
        request,
        "configuracion/index.html",
        {
            "usuario": usuario,
            "plan": plan,
            "horarios_actuales": horarios,
            "tipo_dias": tipo_dias,
        },
    )


@router.post("/horarios")
async def guardar_horarios(
    request: Request,
    horarios_json: str = Form(""),
    tipo_dias: str = Form("interdiario"),
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    try:
        horarios = json.loads(horarios_json)
        if not isinstance(horarios, list):
            raise ValueError("formato inválido")
    except Exception:
        return RedirectResponse(url="/configuracion?error=formato", status_code=303)

    plan = await obtener_plan_del_contador(db, usuario.id)
    max_h = plan.max_horarios_polling if plan else 3
    if len(horarios) > max_h:
        return RedirectResponse(
            url=f"/configuracion?error=limite&max={max_h}", status_code=303
        )

    # Validar formato HH:MM
    horarios_validos = []
    for h in horarios:
        if isinstance(h, str) and len(h) == 5 and h[2] == ":":
            try:
                hh, mm = int(h[:2]), int(h[3:])
                if 0 <= hh < 24 and 0 <= mm < 60:
                    horarios_validos.append(h)
            except ValueError:
                pass

    config_result = await db.execute(
        select(ConfiguracionPolling).where(ConfiguracionPolling.usuario_id == usuario.id)
    )
    config = config_result.scalar_one_or_none()

    if config:
        config.horarios = horarios_validos
        config.tipo_dias = tipo_dias
    else:
        config = ConfiguracionPolling(
            usuario_id=usuario.id,
            horarios=horarios_validos,
            tipo_dias=tipo_dias,
            activo=True,
        )
        db.add(config)
    await db.commit()
    return RedirectResponse(url="/configuracion?ok=horarios", status_code=303)
