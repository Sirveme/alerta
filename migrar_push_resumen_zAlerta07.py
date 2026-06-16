"""
migrar_push_resumen_zAlerta07.py — alerta.pe   (C:\\alertape\\...)
═══════════════════════════════════════════════════════════════════════
Migración SIN Alembic para zAlerta-07 (bienvenida con resumen + push).

Qué hace (idempotente):
  - usuarios: + ultima_visita_at TIMESTAMPTZ NULL (base para "nuevas desde
    tu última visita").
  - crea la tabla push_suscripciones (UUID) vía create_all si no existe.

Uso:
    python migrar_push_resumen_zAlerta07.py
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

from db import engine, crear_tablas


async def _alter_usuarios() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultima_visita_at "
            "TIMESTAMPTZ;"))


async def main() -> None:
    print("→ Agregando usuarios.ultima_visita_at…")
    await _alter_usuarios()
    print("  ✓ ultima_visita_at lista.")
    print("→ Creando tabla push_suscripciones (create_all idempotente)…")
    await crear_tablas()   # create_all: no toca tablas existentes
    print("  ✓ push_suscripciones lista.")
    print("✓ Migración zAlerta-07 completa.")


if __name__ == "__main__":
    asyncio.run(main())
