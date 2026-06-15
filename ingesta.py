"""
ingesta.py — alerta.pe   (C:\\alertape\\ingesta.py)
═══════════════════════════════════════════════════════════════════════
Toma el resultado del scraper Playwright y lo guarda en la BD respetando
la regla de oro: NUNCA duplicar un mensaje (dedup por uq_notif_dedup).

Flujo:
    resultado_scraper (dict) → ingestar_resultado(session, contribuyente_id)
      - por cada mensaje: INSERT si no existe (cod_mensaje + tipo_msj)
      - por cada adjunto:  INSERT si no existe (cod_archivo)
      - marca contribuyente.ultimo_scrapeo_at / _ok

Async (SQLAlchemy 2.0 + asyncpg). Todo en hora Lima.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Contribuyente, Notificacion, Adjunto

TZ_LIMA = ZoneInfo("America/Lima")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def _parse_fecha_publica(valor: str | None) -> datetime | None:
    """fecPublica viene como 'dd/MM/YYYY HH:MM:SS' → datetime tz Lima."""
    if not valor:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor, fmt).replace(tzinfo=TZ_LIMA)
        except ValueError:
            continue
    return None


async def ingestar_resultado(
    session: AsyncSession,
    estudio_id: uuid.UUID,
    contribuyente_id: uuid.UUID,
    resultado: dict,
) -> dict:
    """Guarda el resultado del scraper. Devuelve stats de la ingesta.

    resultado: el JSON que produce scraper_sunat_playwgth (clave 'mensajes').
    """
    stats = {"mensajes_nuevos": 0, "mensajes_duplicados": 0,
             "adjuntos_nuevos": 0, "adjuntos_duplicados": 0}

    for msg in resultado.get("mensajes", []):
        cod = str(msg.get("cod_mensaje") or "")
        tipo_msj = int(msg.get("tipo_msj") or 0)
        if not cod or not tipo_msj:
            continue

        # ── DEDUP: ¿ya existe esta notificación? ──
        existe = await session.scalar(
            select(Notificacion.id).where(
                Notificacion.contribuyente_id == contribuyente_id,
                Notificacion.cod_mensaje_sunat == cod,
                Notificacion.tipo_msj == tipo_msj,
            )
        )
        if existe:
            stats["mensajes_duplicados"] += 1
            notif_id = existe
        else:
            notif = Notificacion(
                estudio_id=estudio_id,
                contribuyente_id=contribuyente_id,
                cod_mensaje_sunat=cod,
                tipo_msj=tipo_msj,
                asunto=msg.get("asunto"),
                texto_html=msg.get("texto_html"),
                cant_adjuntos=int(msg.get("cant_adjuntos") or 0),
                fecha_envio_sunat=msg.get("fecha_envio"),
                fecha_publica_sunat=_parse_fecha_publica(
                    (msg.get("raw") or {}).get("fecPublica") or msg.get("fecha_envio")),
                raw_detalle=msg.get("detalle"),
            )
            session.add(notif)
            await session.flush()   # para obtener notif.id
            notif_id = notif.id
            stats["mensajes_nuevos"] += 1

        # ── Adjuntos (dedup por cod_archivo, desde listAttach del detalle) ──
        detalle = msg.get("detalle") or {}
        for att in (detalle.get("listAttach") or []):
            cod_arch = str(att.get("codArchivo") or "")
            nombre = att.get("nomArchivo") or ""
            if not cod_arch or not nombre:
                continue
            dup = await session.scalar(
                select(Adjunto.id).where(
                    Adjunto.notificacion_id == notif_id,
                    Adjunto.cod_archivo_sunat == cod_arch,
                )
            )
            if dup:
                stats["adjuntos_duplicados"] += 1
                continue
            session.add(Adjunto(
                notificacion_id=notif_id,
                estudio_id=estudio_id,
                cod_archivo_sunat=cod_arch,
                nombre_archivo=nombre,
                tamano_bytes=att.get("cntTamarch"),
                # gcs_key / bytea_temporal se llenan en el paso de subida
            ))
            stats["adjuntos_nuevos"] += 1

    # Marcar el scrapeo en el contribuyente
    contrib = await session.get(Contribuyente, contribuyente_id)
    if contrib:
        contrib.ultimo_scrapeo_at = ahora_lima()
        contrib.ultimo_scrapeo_ok = bool(resultado.get("exito"))

    await session.commit()
    return stats