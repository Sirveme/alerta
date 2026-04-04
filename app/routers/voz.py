"""
routers/voz.py — Endpoints de consultas por voz y comandos rápidos.
"""

import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.configuracion import ConfigUsuario
from app.models.voz import ConsultaVoz

router = APIRouter(prefix="/api/voz", tags=["voz"])


class ConsultaVozRequest(BaseModel):
    texto: str
    empresa_activa_id: Optional[int] = None
    periodo_activo: Optional[str] = None


class ComandoRapidoRequest(BaseModel):
    comando: str  # "alertas" | "resumen" | "pendientes" | "pagos"


@router.post("/consulta")
async def consulta_voz(
    body: ConsultaVozRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Procesa una consulta de voz: agente interpreta + ejecuta + responde.
    """
    payload = request.state.token_payload
    empresa_id = body.empresa_activa_id or payload.get("empresa_activa_id")

    if not empresa_id:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    # Obtener contexto
    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == empresa_id)
    ).scalar_one_or_none()

    config = db.execute(
        select(ConfigUsuario).where(ConfigUsuario.usuario_id == current_user.id)
    ).scalar_one_or_none()

    tono = config.tono_ia.value if config else "directo"

    from app.services.voz_service import procesar_consulta_voz

    resultado = await procesar_consulta_voz(
        db=db,
        usuario_id=str(current_user.id),
        empresa_id=empresa_id,
        texto=body.texto,
        nombre_empresa=empresa.razon_social if empresa else "",
        ruc_empresa=empresa.ruc if empresa else "",
        nombre_usuario=current_user.nombres,
        rol=payload.get("rol", "contador"),
        tono=tono,
    )

    return resultado


@router.post("/comando-rapido")
async def comando_rapido(
    body: ComandoRapidoRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ejecuta comando rápido sin pasar por agente IA."""
    payload = request.state.token_payload
    empresa_id = payload.get("empresa_activa_id")

    if not empresa_id:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    from app.services.voz_service import ejecutar_comando_rapido
    return await ejecutar_comando_rapido(db, empresa_id, body.comando)


@router.post("/transcribir")
async def transcribir_audio(
    audio: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Transcribe audio con OpenAI Whisper API.
    Acepta: webm, ogg, mp4, wav, mp3. Max 25MB.
    Prompt de contexto para mejorar precisión contable peruana.
    Costo: ~$0.006/minuto de audio.
    """
    contenido = await audio.read()

    if len(contenido) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio no debe superar 25 MB")

    try:
        from openai import OpenAI
        from app.core.config import settings

        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        # Crear file-like object con nombre para que OpenAI detecte el formato
        nombre = audio.filename or "audio.webm"
        audio_file = io.BytesIO(contenido)
        audio_file.name = nombre

        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es",
            prompt=(
                "Transcripción de consulta contable peruana. Términos esperados: "
                "RUC, IGV, SUNAT, SIRE, PLE, comprobante, factura, boleta, "
                "nota de crédito, drawback, fraccionamiento, SUNAFIL, PDT, DJ, "
                "soles, dólares, Yape, Plin, BCP, BBVA, Interbank."
            ),
        )

        return {
            "texto": transcription.text,
            "confianza_estimada": "alta",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en Whisper: {str(e)}")


@router.get("/historial")
def historial_voz(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Últimas 20 consultas de voz del usuario actual."""
    consultas = db.execute(
        select(ConsultaVoz).where(
            ConsultaVoz.usuario_id == current_user.id,
        ).order_by(ConsultaVoz.created_at.desc()).limit(20)
    ).scalars().all()

    return {
        "items": [
            {
                "id": c.id,
                "transcripcion": c.transcripcion_original,
                "intencion": c.intencion_detectada,
                "respuesta": c.respuesta_entregada,
                "tiempo_ms": c.tiempo_respuesta_ms,
                "created_at": str(c.created_at),
            }
            for c in consultas
        ],
    }
