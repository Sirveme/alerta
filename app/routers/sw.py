"""Endpoint para servir el Service Worker con scope correcto y anti-cache.

El Service Worker se sirve con headers anti-cache para forzar al navegador
a descargar siempre la versión más reciente. Es la única forma confiable
de propagar cambios al SW ya instalado en navegadores de usuarios.
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["sw"])


@router.get("/sw.js")
async def service_worker():
    return FileResponse(
        "static/js/sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
