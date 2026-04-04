"""
schemas/ingesta.py — Pydantic schemas para endpoints de ingesta.
"""

from typing import Optional

from pydantic import BaseModel


class IngestaFotoResponse(BaseModel):
    tarea_id: str
    mensaje: str


class IngestaXMLResponse(BaseModel):
    comprobante_id: int
    tipo: str
    serie: str
    correlativo: str
    total: float
    es_duplicado: bool
    duplicado_nivel: int


class TareaEstadoResponse(BaseModel):
    estado: str  # pending | started | success | failure
    progreso: Optional[int] = None
    resultado: Optional[dict] = None
    error: Optional[str] = None
