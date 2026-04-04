"""
schemas/comprobante.py — Pydantic schemas para comprobantes (request/response).
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class DetalleLineaIn(BaseModel):
    numero_linea: int = 1
    descripcion: str
    cantidad: Decimal = Decimal("1")
    precio_unitario: Decimal = Decimal("0")
    igv_monto: Decimal = Decimal("0")
    total_linea: Decimal = Decimal("0")
    codigo_producto: Optional[str] = None
    unidad_medida: Optional[str] = None
    categoria_ia: Optional[str] = None
    es_deducible: Optional[bool] = None


class ComprobanteIn(BaseModel):
    """Schema para creación manual de comprobante."""
    tipo: str
    serie: str
    correlativo: str
    ruc_emisor: str
    razon_social_emisor: Optional[str] = None
    ruc_receptor: str
    razon_social_receptor: Optional[str] = None
    moneda: str = "PEN"
    subtotal: Decimal = Decimal("0")
    igv: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    fecha_emision: date
    fecha_vencimiento: Optional[date] = None
    detalle_lineas: list[DetalleLineaIn] = []


class ComprobanteOut(BaseModel):
    id: int
    tipo: str
    serie: str
    correlativo: str
    ruc_emisor: str
    razon_social_emisor: Optional[str]
    ruc_receptor: str
    razon_social_receptor: Optional[str]
    moneda: str
    subtotal: float
    igv: float
    total: float
    fecha_emision: str
    fecha_vencimiento: Optional[str]
    estado: str
    hash_cpe: Optional[str]

    class Config:
        from_attributes = True


class CambiarEstadoIn(BaseModel):
    estado: str
