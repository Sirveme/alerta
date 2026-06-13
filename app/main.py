"""FastAPI app principal."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.templates import templates
from app.routers import (
    auth,
    clientes_ruc,
    configuracion,
    dashboard,
    home,
    mensajes,
    push,
    robots,
    sw,
    ui,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    from app.services.scheduler_service import iniciar_scheduler
    iniciar_scheduler()
    yield
    # shutdown
    from app.services.scheduler_service import detener_scheduler
    from app.services.sunat.session_pool import sunat_pool
    detener_scheduler()
    sunat_pool.cerrar_todas()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(home.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(ui.router)
app.include_router(clientes_ruc.router)
app.include_router(mensajes.router)
app.include_router(configuracion.router)
app.include_router(push.router)
app.include_router(robots.router)
app.include_router(sw.router)



# ============================================================
# Filtro de URLs heredadas (proyectos anteriores en este dominio)
# Bots siguen pidiendo URLs de WordPress de algún sitio anterior
# que usó alerta.pe. Devolver 410 inmediato libera el servidor.
# ============================================================
import re

PATRONES_LEGACY = [
    re.compile(r"^/\d{4}/\d{2}/\d{2}/"),  # /2022/05/13/...
    re.compile(r"^/\d{4}/\d{2}/"),         # /2022/05/
    re.compile(r"^/\d{4}/$"),              # /2022/
    re.compile(r"^/category/"),
    re.compile(r"^/tag/"),
    re.compile(r"^/wp-"),                  # /wp-admin, /wp-content, etc.
    re.compile(r"^/wprss"),
    re.compile(r"^/feed"),
    re.compile(r"^/comments/"),
    re.compile(r"^/author/"),
    re.compile(r"^/page/"),
]


@app.middleware("http")
async def filtrar_urls_legacy(request: Request, call_next):
    """Devuelve 410 Gone instantáneo a URLs heredadas de proyectos previos
    en este dominio. Libera al servidor de procesarlas.
    """
    from fastapi.responses import PlainTextResponse
    path = request.url.path
    for patron in PATRONES_LEGACY:
        if patron.match(path):
            return PlainTextResponse(
                "Esta URL no existe en alerta.pe.\n"
                "Visite https://alerta.pe para el sistema de alertas SUNAT.",
                status_code=410,
            )
    return await call_next(request)



@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "errors/404.html",
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "errors/500.html",
        status_code=500,
    )

# en app/main.py
@app.get("/health")
async def health_check():
    return {"status": "ok"}