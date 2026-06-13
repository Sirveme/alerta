"""Servicio de encriptación/desencriptación de credenciales SOL.

Envuelve las primitivas Fernet de app.core.security para uso a nivel de credenciales.
NUNCA loggear ni exponer valores descifrados.
"""
from app.core.security import decrypt_secret, encrypt_secret


def encriptar_credencial(plain: str) -> str:
    """Encripta una credencial SOL (clave, dni o usuario)."""
    return encrypt_secret(plain)


def desencriptar_credencial(encrypted: str) -> str:
    """Desencripta una credencial SOL. Solo usar en memoria al momento del polling."""
    return decrypt_secret(encrypted)
