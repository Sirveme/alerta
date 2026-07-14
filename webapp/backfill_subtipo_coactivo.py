"""
backfill_subtipo_coactivo.py — alerta.pe (zAlerta-71)
═══════════════════════════════════════════════════════════════════════
Puebla `notificaciones.subtipo_coactivo` en las coactivas EXISTENTES sin
re-scrapear: el `asunto` ya está en la BD. Reusa la MISMA función
`subtipo_coactivo()` de clasificacion.py (fuente única, sin patrones
divergentes) y ajusta la `urgencia` según el subtipo (los de alivio dejan
de ser rojos). Idempotente: re-ejecutar da el mismo resultado.

Uso:
    python backfill_subtipo_coactivo.py --dry     # muestra qué cambiaría, no escribe
    python backfill_subtipo_coactivo.py           # aplica
    python backfill_subtipo_coactivo.py --ruc 20103830991   # solo un buzón
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), ".env"))
except ImportError:
    pass

from models import Notificacion, Contribuyente, TipoDocumento
from clasificacion import clasificar, subtipo_coactivo


def _url() -> str:
    u = os.environ["DATABASE_URL"]
    return (u.replace("postgresql+asyncpg://", "postgresql://")
             .replace("postgres://", "postgresql://")
             .replace("postgresql://", "postgresql+asyncpg://"))


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill de subtipo_coactivo.")
    ap.add_argument("--dry", action="store_true", help="No escribe; solo muestra.")
    ap.add_argument("--ruc", help="Limitar a un RUC (buzón).")
    args = ap.parse_args()

    eng = create_async_engine(_url())
    Sm = async_sessionmaker(eng, expire_on_commit=False)
    async with Sm() as session:
        # Candidatas: ya-coactivas (para completar subtipo) O cuyo ASUNTO diga
        # "coactiv" (zAlerta-78: las mal clasificadas como AVISO sin guiones).
        q = (select(Notificacion).where(
                or_(Notificacion.tipo_documento_enum == TipoDocumento.COBRANZA_COACTIVA,
                    Notificacion.asunto.ilike("%coactiv%"))))
        if args.ruc:
            q = (q.join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
                  .where(Contribuyente.ruc == args.ruc))
        notifs = list(await session.scalars(q))

        cambios = Counter()
        upgrades = 0   # AVISO/otro → coactiva
        for n in notifs:
            # Fuente única: la MISMA clasificación del pipeline (base + subtipo).
            tipo, urg, _ = clasificar(n.nombre_carpeta, n.asunto, False)
            if tipo != TipoDocumento.COBRANZA_COACTIVA:
                continue   # no es coactiva → no tocar
            st = subtipo_coactivo(n.asunto)
            if n.tipo_documento_enum != TipoDocumento.COBRANZA_COACTIVA:
                upgrades += 1
            if (n.tipo_documento_enum != tipo or n.subtipo_coactivo != st
                    or n.urgencia != urg):
                n.tipo_documento_enum = tipo
                n.subtipo_coactivo = st
                n.urgencia = urg
                cambios[st or "generica"] += 1

        total_cambios = sum(cambios.values())
        print(f"Candidatas revisadas: {len(notifs)}")
        print(f"AVISO/otro → coactiva (upgrades): {upgrades}")
        print("Cambios por subtipo:", dict(cambios) or "ninguno")
        print(f"Total a actualizar: {total_cambios}")

        if args.dry:
            await session.rollback()
            print("\n[--dry: no se escribió nada]")
        else:
            await session.commit()
            print("\n✓ Backfill aplicado (idempotente).")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
