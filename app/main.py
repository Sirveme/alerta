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
app.include_router(sw.router)


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