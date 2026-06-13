"""JWT, hashing y Fernet."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from cryptography.fernet import Fernet
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    subject: str,
    rol: str,
    extra_claims: Optional[dict] = None,
) -> str:
    """Crea JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "rol": rol,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    """Crea JWT refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """Decodifica JWT. Devuelve None si inválido o expirado."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


# === Fernet ===

def _fernet() -> Fernet:
    if not settings.fernet_key:
        raise RuntimeError("FERNET_KEY no configurado en .env")
    return Fernet(settings.fernet_key.encode())


def encrypt_secret(plain: str) -> str:
    """Encripta string (ej: clave SOL)."""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(encrypted: str) -> str:
    """Desencripta string."""
    if not encrypted:
        return ""
    return _fernet().decrypt(encrypted.encode()).decode()
