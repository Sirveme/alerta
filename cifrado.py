"""
cifrado.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Cifrado/descifrado de las claves SOL de los contribuyentes con Fernet.

REGLAS:
  - La clave maestra Fernet vive en la env var FERNET_KEY_SOL (Railway).
    NUNCA en el repo, nunca hardcodeada.
  - La BD guarda solo el TOKEN cifrado (clave_sol_cifrada), nunca texto plano.
  - Si FERNET_KEY_SOL no existe, fallar ruidosamente (no cifrar con clave débil).

Generar una clave maestra nueva (una sola vez, guardar en Railway):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Uso:
    from cifrado import cifrar_clave_sol, descifrar_clave_sol
    token = cifrar_clave_sol("MiClaveSOL123")      # → str (guardar en BD)
    clave = descifrar_clave_sol(token)             # → "MiClaveSOL123"
"""

from __future__ import annotations

import os
import functools

from cryptography.fernet import Fernet, InvalidToken


class ErrorCifrado(Exception):
    """Error al cifrar/descifrar (clave maestra ausente o token inválido)."""


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Instancia Fernet desde FERNET_KEY_SOL. Cacheada (se lee una vez)."""
    key = os.getenv("FERNET_KEY_SOL")
    if not key:
        raise ErrorCifrado(
            "FERNET_KEY_SOL no está definida. Generá una con: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" y ponela en las "
            "variables de entorno (Railway).")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise ErrorCifrado(f"FERNET_KEY_SOL inválida: {e}")


def cifrar_clave_sol(clave_plana: str) -> str:
    """Cifra una clave SOL. Devuelve el token (str) para guardar en BD."""
    if not clave_plana:
        raise ErrorCifrado("No se puede cifrar una clave vacía.")
    token = _fernet().encrypt(clave_plana.encode("utf-8"))
    return token.decode("utf-8")


def descifrar_clave_sol(token: str) -> str:
    """Descifra el token guardado en BD. Devuelve la clave SOL en claro."""
    if not token:
        raise ErrorCifrado("No se puede descifrar un token vacío.")
    try:
        clave = _fernet().decrypt(token.encode("utf-8"))
        return clave.decode("utf-8")
    except InvalidToken:
        raise ErrorCifrado(
            "Token inválido o cifrado con otra FERNET_KEY_SOL. "
            "¿Cambió la clave maestra?")


def verificar_configuracion() -> bool:
    """Smoke test: cifra y descifra un valor de prueba. True si todo OK."""
    try:
        prueba = "test_clave_sol_123"
        return descifrar_clave_sol(cifrar_clave_sol(prueba)) == prueba
    except ErrorCifrado:
        return False


if __name__ == "__main__":
    # Permite probar rápido:  python cifrado.py
    import sys
    if not os.getenv("FERNET_KEY_SOL"):
        print("⚠  FERNET_KEY_SOL no está definida.")
        print("   Generá una clave con:")
        print('   python -c "from cryptography.fernet import Fernet; '
              'print(Fernet.generate_key().decode())"')
        sys.exit(1)
    if verificar_configuracion():
        print("✓ Cifrado Fernet funcionando correctamente.")
    else:
        print("✗ Falló el smoke test de cifrado.")
        sys.exit(1)