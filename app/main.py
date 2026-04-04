"""
alerta.pe — FastAPI Backend
Peru Sistemas Pro E.I.R.L. · RUC 20615446565

Sesión 2: se agregan routers de autenticación, configuración, empresas,
templates Jinja2, CORS, y middleware de auditoría.
Se mantiene la funcionalidad de Sesión 0 (contadores/pioneros).
"""

import os
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
from jose import jwt, JWTError

# ── CONFIGURACIÓN ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/alertape")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = Path("app/static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

BASE_DIR = Path(__file__).parent

# ── DATABASE (legacy — Sesión 0 contadores) ──────────────────
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ContadorRegistro(Base):
    __tablename__ = "contador_registro"

    id              = Column(Integer, primary_key=True, index=True)
    nombre          = Column(String(200), nullable=False)
    whatsapp        = Column(String(30),  nullable=False)
    region          = Column(String(60),  nullable=False)
    dolor           = Column(Text,        nullable=False)
    proceso_actual  = Column(Text,        nullable=True)
    sugerencia      = Column(Text,        nullable=True)
    anonimo         = Column(Boolean,     default=False)
    autoriza_foto   = Column(Boolean,     default=False)
    foto_path       = Column(String(500), nullable=True)
    publicado       = Column(Boolean,     default=True)
    creado_en       = Column(DateTime,    default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── FASTAPI ──────────────────────────────────────────────────
app = FastAPI(
    title="alerta.pe",
    description="Alertas inteligentes de pagos digitales para contadores",
    version="0.2.0",
)

# CORS — permitir todas las origenes en desarrollo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files y templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# ── ROUTERS (Sesión 2) ──────────────────────────────────────
from app.routers.auth import router as auth_router
from app.routers.config import router as config_router
from app.routers.empresas import router as empresas_router

app.include_router(auth_router)
app.include_router(config_router)
app.include_router(empresas_router)

# ── ROUTERS (Sesión 3) ──────────────────────────────────────
from app.routers.ingesta import router as ingesta_router
from app.routers.comprobantes import router as comprobantes_router
from app.routers.pagos import router as pagos_router
from app.routers.alertas import router as alertas_router
from app.routers.ws import router as ws_router

app.include_router(ingesta_router)
app.include_router(comprobantes_router)
app.include_router(pagos_router)
app.include_router(alertas_router)
app.include_router(ws_router)

# ── ROUTERS (Sesión 4) ──────────────────────────────────────
from app.routers.voz import router as voz_router

app.include_router(voz_router)

# ── ROUTERS (Sesión 5) ──────────────────────────────────────
from app.routers.notif_manual import router as notif_manual_router
from app.routers.ple import router as ple_router
from app.routers.asientos import router as asientos_router
from app.routers.correccion import router as correccion_router
from app.routers.tipo_cambio import router as tipo_cambio_router
from app.routers.exportacion import router as exportacion_router

app.include_router(notif_manual_router)
app.include_router(ple_router)
app.include_router(asientos_router)
app.include_router(correccion_router)
app.include_router(tipo_cambio_router)
app.include_router(exportacion_router)

# ── ROUTERS (Sesión 6 — Portal público reenviame.pe) ─────────
from app.routers.portal import router as portal_router
from app.routers.publico import router as publico_router

app.include_router(portal_router)
app.include_router(publico_router)

# ── ROUTERS (Sesión 7a — RendiPe) ────────────────────────────
from app.routers.rendipe import router as rendipe_router

app.include_router(rendipe_router)

# ── ROUTERS (Sesión B — SOTE + Registro) ─────────────────────
from app.routers.sote import router as sote_router
from app.routers.registro import router as registro_router

# SOTE — oculto del docs publico
app.include_router(sote_router, prefix="/sote", tags=["sote"],
                   include_in_schema=False)

# Registro de nuevos tenants
app.include_router(registro_router, tags=["registro"])

# Rate limiting para endpoints públicos
from app.core.rate_limit import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── MIDDLEWARE: inyectar datos de usuario en templates ────────
@app.middleware("http")
async def inject_user_context(request: Request, call_next):
    """
    Middleware que decodifica el JWT de la cookie 'token' y lo inyecta
    en request.state para que los templates puedan acceder a datos del usuario.
    No bloquea requests sin token (páginas públicas).
    """
    from app.core.security import SECRET_KEY, ALGORITHM
    request.state.user = None
    token = request.cookies.get("token")
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            request.state.user = payload
        except JWTError:
            pass
    response = await call_next(request)
    return response


# ── SERVICE WORKER ───────────────────────────────────────────
@app.get("/sw.js")
async def service_worker():
    return FileResponse("app/static/sw.js", media_type="application/javascript")


# ── PÁGINAS PÚBLICAS ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/pioneros", response_class=HTMLResponse)
async def pioneros(request: Request):
    return templates.TemplateResponse("pioneros/index.html", {"request": request})


# ── PÁGINAS AUTENTICADAS ─────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Si ya tiene token válido, redirigir a dashboard
    if request.state.user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@app.get("/reset-clave", response_class=HTMLResponse)
async def reset_clave_page(request: Request):
    return templates.TemplateResponse("auth/reset_clave.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": request.state.user,
    })


# ── PÁGINAS INGESTA (Sesión 3) ────────────────────────────────
@app.get("/subir-foto", response_class=HTMLResponse)
async def subir_foto_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("ingesta/subir_foto.html", {
        "request": request, "user": request.state.user,
    })


@app.get("/formulario-manual", response_class=HTMLResponse)
async def formulario_manual_page(request: Request):
    if not request.state.user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("ingesta/formulario_manual.html", {
        "request": request, "user": request.state.user,
    })


# ── API: REGISTRAR CONTADOR (legacy Sesión 0) ───────────────
@app.post("/api/contadores/registro")
async def registrar_contador(
    request:        Request,
    nombre:         str          = Form(...),
    whatsapp:       str          = Form(...),
    region:         str          = Form(...),
    dolor:          str          = Form(...),
    proceso_actual: str          = Form(""),
    sugerencia:     str          = Form(""),
    anonimo:        str          = Form("0"),
    autoriza_foto:  str          = Form("0"),
    foto:           Optional[UploadFile] = File(None),
):
    es_anonimo   = anonimo == "1"
    es_autoriza  = autoriza_foto == "1"
    foto_path    = None

    if foto and foto.filename and es_autoriza:
        ext = Path(foto.filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            return JSONResponse({"exito": False, "mensaje": "Formato de imagen no válido"}, status_code=400)
        if foto.size and foto.size > 5 * 1024 * 1024:
            return JSONResponse({"exito": False, "mensaje": "La foto no debe superar 5 MB"}, status_code=400)
        filename = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / filename
        with dest.open("wb") as f:
            shutil.copyfileobj(foto.file, f)
        foto_path = f"uploads/{filename}"

    db = SessionLocal()
    try:
        registro = ContadorRegistro(
            nombre         = nombre.strip(),
            whatsapp       = whatsapp.strip(),
            region         = region.strip(),
            dolor          = dolor.strip(),
            proceso_actual = proceso_actual.strip(),
            sugerencia     = sugerencia.strip(),
            anonimo        = es_anonimo,
            autoriza_foto  = es_autoriza,
            foto_path      = foto_path,
            publicado      = True,
        )
        db.add(registro)
        db.commit()
        db.refresh(registro)
        return JSONResponse({"exito": True, "id": registro.id, "mensaje": "¡Registrado como Pionero!"})
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/contadores/respuestas")
async def obtener_respuestas(limit: int = 50):
    db = SessionLocal()
    try:
        registros = (
            db.query(ContadorRegistro)
            .filter(ContadorRegistro.publicado == True)
            .order_by(ContadorRegistro.creado_en.desc())
            .limit(limit)
            .all()
        )
        total = db.query(ContadorRegistro).filter(ContadorRegistro.publicado == True).count()

        respuestas = []
        for r in registros:
            foto_url = f"/static/{r.foto_path}" if r.foto_path and r.autoriza_foto else None
            respuestas.append({
                "id":       r.id,
                "nombre":   r.nombre,
                "region":   r.region,
                "dolor":    r.dolor,
                "sugerencia": r.sugerencia,
                "anonimo":  r.anonimo,
                "foto_url": foto_url,
                "creado_en": r.creado_en.isoformat() if r.creado_en else None,
            })

        return JSONResponse({"respuestas": respuestas, "total": total})
    finally:
        db.close()


class AnalisisRequest(BaseModel):
    texto: str


@app.post("/api/contadores/analizar-dolor")
async def analizar_dolor(body: AnalisisRequest):
    if not OPENAI_API_KEY:
        return JSONResponse({"resumen": "API de IA no configurada."}, status_code=200)

    if len(body.texto.strip()) < 10:
        return JSONResponse({"resumen": ""}, status_code=200)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un asistente empático que ayuda a entender los dolores de contadores públicos peruanos. "
                        "Resume el problema descrito en UNA oración clara y directa, en primera persona del contador. "
                        "Usa español peruano natural. Máximo 25 palabras. Sin comillas."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Resume este dolor del contador: {body.texto}",
                },
            ],
            max_tokens=80,
            temperature=0.4,
        )
        resumen = response.choices[0].message.content.strip()
        return JSONResponse({"resumen": resumen})
    except Exception:
        return JSONResponse({"resumen": "No se pudo analizar en este momento."}, status_code=200)


# ── HEALTH CHECK ─────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "app": "alerta.pe", "version": "0.2.0"}
