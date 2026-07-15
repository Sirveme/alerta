"""
backfill_adjuntos_gcs.py — alerta.pe (zAlerta-82)
═══════════════════════════════════════════════════════════════════════
Sube a GCS (permanente) los adjuntos que ya tienen su PDF en BD
(bytea_temporal) pero sin gcs_key. Idempotente y auto-sanable. Reusa el
mismo patrón que backfill_pago.py. DEBE correr en el WORKER (tiene GCS).

El CUERPO fiel de los mensajes ya existentes se muestra en lectura desde
texto_html/raw_detalle (sin backfill). Los informativos que aún no tienen
detalle (cuerpo) se completan con un FULL (que ahora baja detalle de TODO).

Uso:
    python backfill_adjuntos_gcs.py --dry
    python backfill_adjuntos_gcs.py --ruc 20103830991
    python backfill_adjuntos_gcs.py                 # todos
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), ".env"))
except ImportError:
    pass

import gcs
from models import Adjunto, Notificacion, Contribuyente


def _url() -> str:
    u = os.environ["DATABASE_URL"]
    return (u.replace("postgresql+asyncpg://", "postgresql://")
             .replace("postgres://", "postgresql://")
             .replace("postgresql://", "postgresql+asyncpg://"))


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill de adjuntos a GCS.")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ruc")
    args = ap.parse_args()
    if not args.dry and not gcs.gcs_disponible():
        print("✗ GCS no disponible aquí. Corre este backfill en el WORKER.")
        return

    eng = create_async_engine(_url())
    Sm = async_sessionmaker(eng, expire_on_commit=False)
    async with Sm() as session:
        q = (select(Adjunto, Contribuyente.id)
             .join(Notificacion, Notificacion.id == Adjunto.notificacion_id)
             .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
             .where(Adjunto.bytea_temporal.is_not(None), Adjunto.gcs_key.is_(None)))
        if args.ruc:
            q = q.where(Contribuyente.ruc == args.ruc)
        filas = list(await session.execute(q))
        print(f"Adjuntos con bytea sin gcs_key: {len(filas)}")
        subidos = 0
        for adj, cid in filas:
            if args.dry:
                subidos += 1
                continue
            blob = f"{cid}/adjuntos/{adj.cod_archivo_sunat}_{adj.notificacion_id}.pdf"
            key = gcs.subir_pdf(bytes(adj.bytea_temporal), blob)
            if key:
                adj.gcs_key = key
                subidos += 1
        if args.dry:
            print(f"[--dry] subiría {subidos}")
            await session.rollback()
        else:
            await session.commit()
            print(f"✓ {subidos} adjuntos subidos a GCS (idempotente).")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
