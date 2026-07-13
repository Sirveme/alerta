"""
backfill_pago.py — alerta.pe (zAlerta-74)
═══════════════════════════════════════════════════════════════════════
Crea el DocumentoValorado(tipo='PAGO') de los Formularios 1662 EXISTENTES
sin re-scrapear: el PDF de la constancia ya está en BD (adjuntos.bytea_
temporal). El backfill lee esos bytes, extrae el texto (pypdf), sube el PDF
a GCS y crea el valorado con su pdf_texto → así `/api/valor/{id}/asociados`
puede vincular pago↔valor y reaparece el filtro "Pagos".

Reusa `subtipo`/patrones de clasificacion.py y `extraer_pago` (zAlerta-69).
Idempotente: si el 1662 ya tiene su DocumentoValorado, se salta.

Uso:
    python backfill_pago.py --dry            # muestra qué haría, no escribe
    python backfill_pago.py                  # aplica (todos los buzones)
    python backfill_pago.py --ruc 20103830991
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), ".env"))
except ImportError:
    pass

import pypdf
import gcs
from models import (Notificacion, Contribuyente, Adjunto, DocumentoValorado,
                    TipoDocumento, TipoValorado)
from clasificacion import subtipo_coactivo  # noqa: F401  (fuente única)
from webapp.deuda import extraer_pago


def _url() -> str:
    u = os.environ["DATABASE_URL"]
    return (u.replace("postgresql+asyncpg://", "postgresql://")
             .replace("postgres://", "postgresql://")
             .replace("postgresql://", "postgresql+asyncpg://"))


def _texto_pdf(bytes_: bytes) -> str:
    try:
        rd = pypdf.PdfReader(io.BytesIO(bytes_))
        return "\n".join((p.extract_text() or "") for p in rd.pages)
    except Exception as e:
        print("  (aviso) no se pudo extraer texto:", str(e)[:60])
        return ""


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill de valorados PAGO (1662).")
    ap.add_argument("--dry", action="store_true", help="No escribe; solo muestra.")
    ap.add_argument("--ruc", help="Limitar a un RUC.")
    args = ap.parse_args()

    eng = create_async_engine(_url())
    Sm = async_sessionmaker(eng, expire_on_commit=False)
    async with Sm() as session:
        # 1662 = notifs tipo PAGO. Solo las que NO tienen ya su valorado.
        q = (select(Notificacion, Contribuyente)
             .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
             .where(Notificacion.tipo_documento_enum == TipoDocumento.PAGO))
        if args.ruc:
            q = q.where(Contribuyente.ruc == args.ruc)
        filas = list(await session.execute(q))

        creados = saltados = sanados = sin_pdf = 0
        for notif, contrib in filas:
            existente = await session.scalar(select(DocumentoValorado).where(
                DocumentoValorado.notificacion_id == notif.id))
            if existente:
                # Auto-sanar: si el valorado existe sin gcs_key y ahora hay GCS
                # + PDF en BD, sube la constancia y completa la gcs_key.
                if existente.gcs_key is None and gcs.gcs_disponible() and not args.dry:
                    adj = await session.scalar(select(Adjunto).where(
                        Adjunto.notificacion_id == notif.id,
                        Adjunto.bytea_temporal.is_not(None)))
                    if adj and adj.bytea_temporal:
                        blob = f"{contrib.id}/valorados/pago_{existente.num_documento}_{notif.cod_mensaje_sunat}.pdf"
                        existente.gcs_key = gcs.subir_pdf(bytes(adj.bytea_temporal), blob)
                        sanados += 1
                        continue
                saltados += 1
                continue
            adj = await session.scalar(select(Adjunto).where(
                Adjunto.notificacion_id == notif.id,
                Adjunto.bytea_temporal.is_not(None)))
            if not adj or not adj.bytea_temporal:
                sin_pdf += 1
                print(f"  {contrib.ruc} 1662 sin PDF en BD (necesita re-scrapeo): "
                      f"{(notif.asunto or '')[:40]}")
                continue

            texto = _texto_pdf(adj.bytea_temporal)
            datos = extraer_pago(texto)
            num_doc = datos.get("n_orden") or (notif.asunto or "")[:60]
            print(f"  {contrib.ruc} 1662 orden {num_doc}: importe={datos.get('importe_fmt')} "
                  f"tributo={(datos.get('tributo') or '')[:22]} valor={datos.get('valor_pagado')}")

            if args.dry:
                creados += 1
                continue

            # Subir la constancia a GCS (permanente), como los demás valorados.
            blob = f"{contrib.id}/valorados/pago_{num_doc}_{notif.cod_mensaje_sunat}.pdf"
            gcs_key = gcs.subir_pdf(bytes(adj.bytea_temporal), blob)
            session.add(DocumentoValorado(
                contribuyente_id=contrib.id, notificacion_id=notif.id,
                estudio_id=notif.estudio_id, tipo_valorado=TipoValorado.PAGO,
                num_documento=num_doc, pdf_texto=texto, gcs_key=gcs_key))
            creados += 1

        print(f"\nPAGO valorados a crear: {creados} | ya existían: {saltados} | "
              f"gcs sanados: {sanados} | sin PDF en BD: {sin_pdf}")
        if args.dry:
            await session.rollback()
            print("[--dry: no se escribió nada]")
        else:
            await session.commit()
            print("✓ Backfill aplicado (idempotente).")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
