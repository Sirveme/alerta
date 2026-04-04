"""
routers/directorio_page.py — Directorio de proveedores con fiabilidad.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario
from app.models.directorio import DirectorioRUC

router = APIRouter(tags=["directorio"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/directorio", response_class=HTMLResponse)
def directorio_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("directorio/index.html", {
        "request": request, "user": request.state.user,
    })


@router.get("/api/directorio/buscar-ruc/{ruc}")
def buscar_ruc(
    ruc: str,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Busca RUC en directorio local. Si no existe, placeholder para API SUNAT."""
    entry = db.execute(
        select(DirectorioRUC).where(DirectorioRUC.ruc == ruc)
    ).scalar_one_or_none()

    if entry:
        return {
            "ruc": entry.ruc,
            "razon_social": entry.razon_social,
            "estado_sunat": entry.estado_sunat,
            "condicion": entry.condicion,
            "direccion": entry.direccion,
            "fiabilidad_pct": float(entry.fiabilidad_pct) if entry.fiabilidad_pct else None,
            "total_comprobantes": entry.total_comprobantes,
            "fuente": "local",
        }

    # Placeholder: en produccion consultaria API SUNAT
    return {
        "ruc": ruc,
        "razon_social": None,
        "estado_sunat": None,
        "fuente": "no_encontrado",
        "mensaje": "RUC no encontrado en directorio local. Consulta SUNAT pendiente.",
    }


class ContactoUpdate(BaseModel):
    email_contacto: Optional[str] = None
    whatsapp_contacto: Optional[str] = None


@router.post("/api/directorio/{ruc}/contacto")
def actualizar_contacto(
    ruc: str,
    body: ContactoUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualizar email/whatsapp del proveedor."""
    entry = db.execute(
        select(DirectorioRUC).where(DirectorioRUC.ruc == ruc)
    ).scalar_one_or_none()

    if not entry:
        raise HTTPException(status_code=404, detail="RUC no encontrado en directorio")

    if body.email_contacto is not None:
        entry.email_contacto = body.email_contacto
    if body.whatsapp_contacto is not None:
        entry.whatsapp_contacto = body.whatsapp_contacto
    db.commit()

    return {"detail": "Contacto actualizado"}
