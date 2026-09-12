"""
migrar_sesion_version_zAlertaLogout.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Migración SIN Alembic. Idempotente y ADITIVA. Fix de seguridad del LOGOUT:
revocación server-side de sesiones.

Añade `sesion_version` (INT NOT NULL DEFAULT 1) a `personas` Y `usuarios`. El
token de sesión lleva `sv`=este valor; /logout y el cambio de clave lo
INCREMENTAN → todo token viejo queda inválido al instante (usuario_actual
compara token.sv vs BD). Cierra el logout que no revocaba nada server-side.

CORTE LIMPIO: al desplegar el código, los tokens viejos (sin `sv`) se rechazan →
todos re-loguean UNA vez. Esta migración es solo aditiva (no cambia comportamiento
por sí sola); el efecto de revocación llega con el deploy del código.

Correr en prod ANTES de desplegar el código.

Uso:
    python migrar_sesion_version_zAlertaLogout.py
"""

from __future__ import annotations

import asyncio
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import text

from db import engine


async def _migrar() -> None:
    async with engine.begin() as conn:
        for tabla in ("personas", "usuarios"):
            await conn.execute(text(
                f"ALTER TABLE {tabla} "
                f"ADD COLUMN IF NOT EXISTS sesion_version INTEGER NOT NULL DEFAULT 1;"))


async def main() -> None:
    print("→ Añadiendo sesion_version a personas y usuarios (default 1)…")
    await _migrar()
    print("✓ sesion_version lista. (El corte limpio ocurre al desplegar el código.)")


if __name__ == "__main__":
    asyncio.run(main())
