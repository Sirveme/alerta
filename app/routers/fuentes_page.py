"""
routers/fuentes_page.py — Panel de estado de fuentes de datos.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario

router = APIRouter(tags=["fuentes"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/fuentes", response_class=HTMLResponse)
def fuentes_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("fuentes/index.html", {
        "request": request, "user": request.state.user,
    })


@router.post("/fuentes/sunat/actualizar")
def actualizar_sunat(
    current_user: Usuario = Depends(get_current_user),
):
    """Dispara sincronizacion SIRE (placeholder)."""
    return JSONResponse({"status": "pendiente", "mensaje": "Sincronizacion SIRE iniciada"})


@router.post("/fuentes/correos/procesar")
def procesar_correos(
    current_user: Usuario = Depends(get_current_user),
):
    """Dispara procesamiento manual de correos pendientes (placeholder)."""
    return JSONResponse({"status": "pendiente", "mensaje": "Procesamiento de correos iniciado"})


@router.get("/fuentes/correos/cola")
def cola_correos(
    current_user: Usuario = Depends(get_current_user),
):
    """Estado de la cola de procesamiento de correos (placeholder)."""
    return {"pendientes": 0, "procesando": 0, "completados": 0}
