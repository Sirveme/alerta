"""
migrar_carpeta_zAlerta28.py — alerta.pe   (C:\\alertape\\...)
═══════════════════════════════════════════════════════════════════════
Migración SIN Alembic para zAlerta-28 (clasificación por carpeta de SUNAT).
Idempotente; correr en prod ANTES de desplegar.

  notificaciones: cod_carpeta VARCHAR(10), nombre_carpeta VARCHAR(120)

No hay backfill de datos: la carpeta solo la conoce el scraper en la lectura.
Las notificaciones existentes quedan con carpeta NULL y se RECLASIFICAN solas
en la próxima lectura del worker (ingesta.py actualiza en sitio las que están
en OTRO/sin carpeta). Las nuevas ya nacen clasificadas.

Uso:
    python migrar_carpeta_zAlerta28.py
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
        await conn.execute(text(
            "ALTER TABLE notificaciones ADD COLUMN IF NOT EXISTS cod_carpeta VARCHAR(10);"))
        await conn.execute(text(
            "ALTER TABLE notificaciones ADD COLUMN IF NOT EXISTS nombre_carpeta VARCHAR(120);"))


async def main() -> None:
    print("→ Migrando zAlerta-28 (carpeta de SUNAT en notificaciones)…")
    await _migrar()
    print("✓ Migración zAlerta-28 completa (cod_carpeta, nombre_carpeta).")


if __name__ == "__main__":
    asyncio.run(main())
