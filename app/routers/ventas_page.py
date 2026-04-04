"""
routers/ventas_page.py — Pantalla de comprobantes de venta emitidos.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import get_current_user
from app.models.usuarios import Usuario

router = APIRouter(tags=["ventas"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/ventas", response_class=HTMLResponse)
def ventas_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("ventas/index.html", {
        "request": request, "user": request.state.user,
    })


@router.get("/ventas/nueva", response_class=HTMLResponse)
def ventas_nueva(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("ingesta/formulario_manual.html", {
        "request": request, "user": request.state.user,
    })
