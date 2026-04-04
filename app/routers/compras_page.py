"""
routers/compras_page.py — Pantalla de comprobantes de compra recibidos.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["compras"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/compras", response_class=HTMLResponse)
def compras_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("compras/index.html", {
        "request": request, "user": request.state.user,
    })


@router.get("/compras/nueva", response_class=HTMLResponse)
def compras_nueva(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("ingesta/formulario_manual.html", {
        "request": request, "user": request.state.user,
    })
