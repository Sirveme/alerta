"""
webapp/routers/interno.py — alerta.pe · Páginas INTERNAS (solo SOPORTE_GLOBAL).
═══════════════════════════════════════════════════════════════════════
`/interno/mapa` — "Mapa Global de alerta.pe": referencia privada para Duilio
(SOPORTE_GLOBAL). Contenido editable en `webapp/contenido/mapa_global.md`.

Privada de verdad: cualquier usuario que NO sea SOPORTE_GLOBAL recibe 404
(no 403) → la página es invisible para el resto. `noindex` en el <meta> y
en la cabecera X-Robots-Tag.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse

from ..core import templates, TZ_LIMA
from ..deps import UsuarioActual, usuario_actual
from ..mapa_render import cargar_mapa

router = APIRouter(tags=["interno"])


@router.get("/interno/mapa", response_class=HTMLResponse)
async def mapa_global(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    # Invisible para no-soporte: 404, no 403.
    if not user.es_soporte_global:
        raise HTTPException(status_code=404)
    mapa = cargar_mapa()
    resp = templates.TemplateResponse(request, "mapa.html", {
        "user": user, "mapa": mapa,
        "generado": datetime.now(TZ_LIMA).strftime("%d/%m/%Y %H:%M"),
    })
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp
