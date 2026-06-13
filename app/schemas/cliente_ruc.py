"""Schemas Pydantic para ClienteRuc."""
from typing import Optional

from pydantic import BaseModel, Field


class ClienteRucBase(BaseModel):
    ruc: str = Field(..., min_length=11, max_length=11)
    razon_social: str
    nombre_referencia: Optional[str] = None
    es_propio_estudio: bool = False


class ClienteRucCreate(ClienteRucBase):
    pass


class ClienteRucUpdate(BaseModel):
    razon_social: Optional[str] = None
    nombre_referencia: Optional[str] = None
    activo: Optional[bool] = None


class ClienteRucPublico(ClienteRucBase):
    id: int
    activo: bool

    model_config = {"from_attributes": True}
