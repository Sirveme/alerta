"""
migrar_capa_gestion_evidencias.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Crea la tabla `evidencias` (Capa de Gestión Capa 2, zAlerta-90) si falta.
Hoy NINGUNA vista la consulta (la relación Instruccion.evidencias es lazy
y no se dispara), así que su ausencia no da 500 — pero la creamos para dejar
el modelo completo y evitar un 500 latente cuando llegue la Capa 2. Depende
de `instrucciones` (FK), que ya existe.

ADITIVA e IDEMPOTENTE (`checkfirst=True`): no recrea si ya existe, no toca
datos. `tipo_evidencia` es VARCHAR (sin tipo PG nuevo).

Uso:  python migrar_capa_gestion_evidencias.py
"""
import asyncio

from db import engine
from models import Evidencia


async def main():
    async with engine.begin() as conn:
        antes = await conn.run_sync(
            lambda c: engine.dialect.has_table(c, "evidencias"))
        await conn.run_sync(Evidencia.__table__.create, checkfirst=True)
        despues = await conn.run_sync(
            lambda c: engine.dialect.has_table(c, "evidencias"))
    print(f"evidencias: existía_antes={antes}  existe_ahora={despues}")
    print("OK — tabla lista" if despues else "ERROR — no se creó")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
