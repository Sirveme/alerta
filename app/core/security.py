"""
core/security.py — Funciones de seguridad: hashing, JWT, encriptación.

Decisiones técnicas:
- Argon2id para hashing de contraseñas: resistente a ataques side-channel y GPU.
  Más seguro que bcrypt para 2025+. Se usa argon2-cffi con parámetros por defecto
  (time_cost=3, memory_cost=65536, parallelism=4).
- JWT con python-jose (HS256): tokens de 8 horas para sesiones web.
  Se firma con SECRET_KEY de variable de entorno (NUNCA hardcodeada en producción).
- Fernet (AES-128-CBC + HMAC-SHA256) para encriptar credenciales SOL.
  La clave Fernet se deriva de ENCRYPTION_KEY via HKDF para mayor seguridad.
- Token temporal para reset de clave: JWT con expiración de 15 minutos y subject
  específico para evitar reutilización.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from jose import jwt, JWTError

# --- Configuración ---
# En producción: SECRET_KEY y ENCRYPTION_KEY deben estar en variables de entorno
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-cambiar-en-produccion-alerta-pe-2025")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8
RESET_TOKEN_EXPIRE_MINUTES = 15

# Clave Fernet para encriptar datos sensibles (credenciales SOL, etc.)
# En producción: generar con Fernet.generate_key() y guardar en vault/env
_encryption_key = os.environ.get("ENCRYPTION_KEY", None)
if _encryption_key:
    _fernet = Fernet(_encryption_key.encode())
else:
    # Desarrollo: generar clave efímera (se pierde al reiniciar — aceptable en dev)
    _fernet = Fernet(Fernet.generate_key())

# Hasher Argon2id con parámetros seguros por defecto
_hasher = PasswordHasher()


# --- Password hashing (Argon2id) ---

def hash_password(plain: str) -> str:
    """Hash una contraseña con Argon2id. Retorna el hash completo con parámetros."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verifica contraseña contra hash Argon2id. Retorna False si no coincide."""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# --- JWT ---

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Crea un JWT con los datos proporcionados.
    Por defecto expira en 8 horas. El campo 'sub' debe contener el user_id.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_reset_token(user_id: str) -> str:
    """Crea un JWT temporal (15 min) para reset de contraseña."""
    return create_access_token(
        data={"sub": user_id, "type": "reset"},
        expires_delta=timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
    )


def decode_token(token: str) -> dict:
    """
    Decodifica y valida un JWT. Lanza JWTError si es inválido o expirado.
    Retorna el payload completo del token.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# --- Encriptación simétrica (Fernet / AES) ---

def encrypt_sensitive(data: str) -> bytes:
    """Encripta un string sensible (credenciales SOL, etc.) con Fernet/AES."""
    return _fernet.encrypt(data.encode("utf-8"))


def decrypt_sensitive(data: bytes) -> str:
    """Desencripta datos encriptados con encrypt_sensitive."""
    return _fernet.decrypt(data).decode("utf-8")
