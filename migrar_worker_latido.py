"""
migrar_worker_latido.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Aditiva e IDEMPOTENTE:
  - crea la tabla `worker_estado` (latido del worker) + fila singleton,
  - agrega `contribuyentes.ultimo_error_motivo` y `ultimo_error_at`.
No toca datos existentes. `checkfirst`/`IF NOT EXISTS` → re-ejecutable.

Uso:  python migrar_worker_latido.py
"""
import asyncio

from sqlalchemy import text

from db import engine
from models import WorkerEstado


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(WorkerEstado.__table__.create, checkfirst=True)
        await conn.execute(text(
            "INSERT INTO worker_estado "
            "(id, rucs_total, rucs_ok, rucs_fallo, rucs_timeout) "
            "VALUES ('singleton', 0, 0, 0, 0) ON CONFLICT (id) DO NOTHING"))
        await conn.execute(text(
            "ALTER TABLE contribuyentes "
            "ADD COLUMN IF NOT EXISTS ultimo_error_motivo VARCHAR(20)"))
        await conn.execute(text(
            "ALTER TABLE contribuyentes "
            "ADD COLUMN IF NOT EXISTS ultimo_error_at TIMESTAMPTZ"))
        ok = await conn.run_sync(
            lambda c: engine.dialect.has_table(c, "worker_estado"))
    print("worker_estado existe:", ok, "| columnas de error agregadas a contribuyentes")
    print("OK" if ok else "ERROR")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
