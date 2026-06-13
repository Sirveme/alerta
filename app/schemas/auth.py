"""Schemas Pydantic para autenticación."""
from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UsuarioPublico(BaseModel):
    id: int
    email: str
    nombre: str
    rol: str

    model_config = {"from_attributes": True}
