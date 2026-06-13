"""Schemas Pydantic para credenciales SOL.

Las credenciales en texto plano solo viajan en el request de creación/actualización.
NUNCA se devuelven descifradas en respuestas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CredencialSolCreate(BaseModel):
    tipo_usuario: int = Field(default=2, description="1=usuario SOL, 2=DNI titular")
    dni: Optional[str] = None
    usuario_sol: Optional[str] = None
    clave_sol: str = Field(..., min_length=1)


class CredencialSolEstado(BaseModel):
    """Solo estado, nunca valores descifrados."""
    id: int
    cliente_ruc_id: int
    tipo_usuario: int
    ultima_verificacion: Optional[datetime] = None
    ultima_verificacion_exitosa: Optional[datetime] = None
    ultimo_error: Optional[str] = None

    model_config = {"from_attributes": True}
