"""Endpoint para servir el Service Worker con el header correcto.

Necesario para que el SW tenga scope='/'.
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["sw"])


@router.get("/sw.js")
async def service_worker():
    return FileResponse(
        "static/js/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )
