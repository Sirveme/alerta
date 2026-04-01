"""
alerta.pe — FastAPI Backend
Peru Sistemas Pro E.I.R.L. · RUC 20615446565
"""
import os
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
from openai import OpenAI

# ── CONFIGURACIÓN ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/alertape")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = Path("app/static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

BASE_DIR = Path(__file__).parent

# ── DATABASE ─────────────────────────────────────────────────
# Railway entrega la URL como postgres:// pero SQLAlchemy necesita postgresql://
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
    foto_path       = Column(String(500), nullable=True)  # ruta relativa en /static/uploads/
    publicado       = Column(Boolean,     default=True)   # auto-publicar si autorizó
    creado_en       = Column(DateTime,    default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


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
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Service Worker debe servirse desde la raíz del dominio
@app.get("/sw.js")
async def service_worker():
    return FileResponse("app/static/sw.js", media_type="application/javascript")


# ── PÁGINAS ───────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/pioneros", response_class=HTMLResponse)
async def pioneros(request: Request):
    return templates.TemplateResponse("pioneros/index.html", {"request": request})


# ── API: REGISTRAR CONTADOR ──────────────────────────────────
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

    # Guardar foto si se autorizó
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


# ── API: OBTENER RESPUESTAS PÚBLICAS ─────────────────────────
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


# ── API: ANÁLISIS IA ─────────────────────────────────────────
class AnalisisRequest(BaseModel):
    texto: str


@app.post("/api/contadores/analizar-dolor")
async def analizar_dolor(body: AnalisisRequest):
    if not OPENAI_API_KEY:
        return JSONResponse({"resumen": "API de IA no configurada."}, status_code=200)

    if len(body.texto.strip()) < 10:
        return JSONResponse({"resumen": ""}, status_code=200)

    try:
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
    except Exception as e:
        return JSONResponse({"resumen": "No se pudo analizar en este momento."}, status_code=200)


# ── HEALTH CHECK ─────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "app": "alerta.pe", "version": "0.1.0"}