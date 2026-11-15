"""
migrar_estudio_es_demo.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Aditiva e IDEMPOTENTE. NO toca datos de clientes reales.

  1) Agrega `estudios_contables.es_demo BOOLEAN NOT NULL DEFAULT FALSE`.
     (En Postgres, ADD COLUMN ... DEFAULT FALSE rellena las filas existentes;
      IF NOT EXISTS → re-ejecutable sin error.)
  2) Marca es_demo=TRUE SOLO en el ESTUDIO DEMO (UUID fijo, confirmado por
     Duilio). El resto queda en FALSE.

Efecto: el worker deja de scrapear los contribuyentes del estudio demo
(ver worker._procesar_fondo), pero el estudio sigue ACTIVO y visible en la app
para los videos. Sus notificaciones son data sembrada (seed_demo_video.py).

Uso (solo cuando Duilio lo apruebe; NO correr en prod sin su OK):
    python migrar_estudio_es_demo.py
"""
import asyncio
import os

from sqlalchemy import text

from db import engine

# ESTUDIO DEMO (aislado en prod, usado para videos). Overridable por si cambia.
DEMO_ESTUDIO_ID = os.getenv(
    "DEMO_ESTUDIO_ID", "be9abf3e-54bb-4451-8a19-5b1ee742b4c8")


async def main():
    async with engine.begin() as conn:
        # 1) Columna aditiva (idempotente).
        await conn.execute(text(
            "ALTER TABLE estudios_contables "
            "ADD COLUMN IF NOT EXISTS es_demo BOOLEAN NOT NULL DEFAULT FALSE"))
        # 2) Marcar SOLO el estudio demo.
        res = await conn.execute(
            text("UPDATE estudios_contables SET es_demo = TRUE "
                 "WHERE id = :id AND es_demo IS DISTINCT FROM TRUE"),
            {"id": DEMO_ESTUDIO_ID})
        # Verificación: cuántos estudios demo y cuántos contribuyentes se excluyen.
        n_demo = await conn.scalar(
            text("SELECT count(*) FROM estudios_contables WHERE es_demo = TRUE"))
        n_contrib = await conn.scalar(
            text("SELECT count(*) FROM contribuyentes c "
                 "JOIN estudios_contables e ON e.id = c.estudio_id "
                 "WHERE e.es_demo = TRUE"))
    print(f"Columna es_demo lista. Filas marcadas ahora: {res.rowcount}")
    print(f"Estudios demo: {n_demo} | contribuyentes que el worker ignorará: {n_contrib}")
    print("OK")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
