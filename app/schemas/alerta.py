"""
schemas/alerta.py — Pydantic schemas para alertas.
"""

from typing import Optional

from pydantic import BaseModel


class AlertaOut(BaseModel):
    id: int
    empresa_id: int
    origen: str
    estado: str
    titulo: str
    descripcion: str
    codigo_entidad: Optional[str]
    comprobante_id: Optional[int]
    pago_id: Optional[int]
    created_at: str

    class Config:
        from_attributes = True
