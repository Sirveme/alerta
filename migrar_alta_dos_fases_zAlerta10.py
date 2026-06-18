"""
migrar_alta_dos_fases_zAlerta10.py — alerta.pe   (C:\\alertape\\...)
═══════════════════════════════════════════════════════════════════════
Migración SIN Alembic para zAlerta-10 (alta de clientes en dos fases).

Qué hace (todo idempotente, seguro de correr varias veces):
  1. Crea la tabla `ruc_cache` (caché incremental ruc → razón social).
  2. Crea la tabla `solicitudes_validacion_credencial` (el ciclo
     flag→worker→resultado de "Comprobar conexión").

NO toca ninguna tabla existente. Usa CREATE TABLE IF NOT EXISTS.

Uso:
    python migrar_alta_dos_fases_zAlerta10.py
"""

from __future__ import annotations

import asyncio
import sys

# La consola de Windows (cp1252) no imprime los caracteres de estado (→ ✓).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import text

from db import engine


async def _crear_tablas() -> None:
    async with engine.begin() as conn:
        # ── ruc_cache ──
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS ruc_cache ("
            "  ruc VARCHAR(11) PRIMARY KEY,"
            "  razon_social VARCHAR(255),"
            "  estado_sunat VARCHAR(50),"
            "  consultado_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ");"
        ))

        # ── solicitudes_validacion_credencial ──
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS solicitudes_validacion_credencial ("
            "  id UUID PRIMARY KEY,"
            "  estudio_id UUID NOT NULL REFERENCES estudios_contables(id) ON DELETE CASCADE,"
            "  ruc VARCHAR(11) NOT NULL,"
            "  usuario_sol VARCHAR(50) NOT NULL,"
            "  clave_sol_cifrada TEXT,"
            "  estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',"
            "  creado_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            "  procesado_at TIMESTAMPTZ"
            ");"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_validacion_estudio "
            "ON solicitudes_validacion_credencial (estudio_id);"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_validacion_pendiente "
            "ON solicitudes_validacion_credencial (estado);"
        ))


async def main() -> None:
    print("→ Creando tablas de zAlerta-10 (ruc_cache + validación credenciales)…")
    await _crear_tablas()
    print("  ✓ ruc_cache lista.")
    print("  ✓ solicitudes_validacion_credencial lista.")
    print("✓ Migración zAlerta-10 completa.")


if __name__ == "__main__":
    asyncio.run(main())
