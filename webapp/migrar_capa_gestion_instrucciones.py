"""
migrar_capa_gestion_instrucciones.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Crea la tabla `instrucciones` (Capa de Gestión, zAlerta-90) si falta. La
vista /cliente/{id} la consulta en cada carga; sin ella da 500 para todo
cliente. ADITIVA e IDEMPOTENTE: `checkfirst=True` no recrea si ya existe;
no toca ninguna otra tabla ni dato. El `estado` es VARCHAR (native_enum=
False), así que no crea tipos PG nuevos.

Uso:  python migrar_capa_gestion_instrucciones.py
"""
import asyncio

from db import engine
from models import Instruccion


async def main():
    async with engine.begin() as conn:
        existe_antes = await conn.run_sync(
            lambda c: engine.dialect.has_table(c, "instrucciones"))
        await conn.run_sync(Instruccion.__table__.create, checkfirst=True)
        existe_despues = await conn.run_sync(
            lambda c: engine.dialect.has_table(c, "instrucciones"))
    print(f"instrucciones: existía_antes={existe_antes}  existe_ahora={existe_despues}")
    print("OK — tabla lista" if existe_despues else "ERROR — no se creó")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
