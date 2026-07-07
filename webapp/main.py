"""
webapp/main.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
App FastAPI de la WebApp. Monta estáticos, registra routers, maneja la
redirección a /login cuando no hay sesión y sirve el service worker /
manifest de la PWA.

Arranque local:
    uvicorn webapp.main:app --reload
"""

from __future__ import annotations
import sys

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core import STATIC_DIR, templates
from .deps import RedirigirALogin
from . import auth
from .routers import (
    dashboard, notificaciones, voz, actualizar, push, clientes, registro,
    superadmin, landing, resumen, pagos, cuenta, blog, blog_admin,
)

app = FastAPI(title="alerta.pe", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Routers
app.include_router(auth.router)
app.include_router(registro.router)
app.include_router(dashboard.router)
app.include_router(notificaciones.router)
app.include_router(voz.router)
app.include_router(actualizar.router)
app.include_router(push.router)
app.include_router(clientes.router)
app.include_router(superadmin.router)
app.include_router(landing.router)
app.include_router(resumen.router)
app.include_router(pagos.router)
app.include_router(cuenta.router)
app.include_router(blog.router)          # blog público + SEO (zAlerta-40)
app.include_router(blog_admin.router)    # panel admin del blog (solo admin)


# Sin sesión → al login (para vistas HTML); JSON para llamadas API.
@app.exception_handler(RedirigirALogin)
async def _sin_sesion(request: Request, exc: RedirigirALogin):
    # Las llamadas API (fetch/JSON, normalmente POST) reciben 401 JSON para
    # que el front lo maneje; las navegaciones GET van al login.
    accept = request.headers.get("accept", "")
    es_api = "application/json" in accept or request.method != "GET"
    if es_api:
        return JSONResponse({"error": "Sesión expirada."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


# ─── PWA: service worker en la raíz (scope completo) y manifest ───
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/manifest.json", include_in_schema=False)
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json",
                        media_type="application/manifest+json")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}