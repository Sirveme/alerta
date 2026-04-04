"""
schemas/pago.py — Pydantic schemas para pagos (request/response).
"""

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class PagoOut(BaseModel):
    id: int
    empresa_id: int
    monto: float
    moneda: str
    canal: str
    estado: str
    fecha_pago: str
    pagador_nombre: Optional[str]
    pagador_documento: Optional[str]
    numero_operacion: Optional[str]
    comprobante_id: Optional[int]

    class Config:
        from_attributes = True


class CruzarManualIn(BaseModel):
    comprobante_id: int


class ResumenMesOut(BaseModel):
    canal: str
    total: float
    cantidad: int
