"""
routers/notificaciones_page.py — Central de notificaciones manuales.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario

router = APIRouter(tags=["notificaciones"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/notificaciones-central", response_class=HTMLResponse)
def notificaciones_central(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("notificaciones/index.html", {
        "request": request, "user": request.state.user,
    })


class EnvioNotifRequest(BaseModel):
    destinatarios: list[int] = []
    mensaje: str
    canal: str = "push"
    plantilla: Optional[str] = None


@router.post("/notificaciones-central/enviar")
def enviar_notificacion(
    body: EnvioNotifRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea y envia notificacion manual (placeholder)."""
    return {
        "enviadas": len(body.destinatarios),
        "fallidas": 0,
        "mensaje": f"Notificacion enviada a {len(body.destinatarios)} destinatarios",
    }


@router.get("/notificaciones-central/historial")
def historial_notificaciones(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista de notificaciones enviadas (placeholder)."""
    return {"items": [], "total": 0}
