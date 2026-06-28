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

import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Contribuyente, Notificacion, Adjunto, TipoDocumento)
from clasificacion import clasificar

TZ_LIMA = ZoneInfo("America/Lima")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def _leer_pdf_local(pdfs, nombre_archivo: str) -> bytes | None:
    """Lee los bytes del PDF que el scraper descargó al filesystem del worker,
    para persistirlo en BD (bytea_temporal) y que la WEB lo sirva sin depender
    del filesystem del worker (zAlerta-08 #4). Devuelve los bytes o None.

    NOTA: esta es la ÚNICA excepción permitida al 'no tocar motor': solo
    persiste el PDF, no cambia la lógica de scraping/login.
    """
    if not pdfs or not nombre_archivo:
        return None
    objetivo = nombre_archivo.lower()
    for ruta in pdfs:
        if not isinstance(ruta, str) or ruta.startswith("PENDIENTE:"):
            continue
        base = os.path.basename(ruta).lower()
        if base == objetivo or base == objetivo + ".pdf" or objetivo in base:
            try:
                p = Path(ruta)
                if p.exists():
                    data = p.read_bytes()
                    if data[:4] == b"%PDF" or len(data) > 1000:
                        return data
            except Exception:
                return None
    return None


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

        # ── Clasificación: CARPETA > ASUNTO > OTRO (zAlerta-28/32) ──
        cod_carp = msg.get("cod_carpeta") or None
        nom_carp = msg.get("nombre_carpeta") or None
        asunto_msg = msg.get("asunto")
        tipo_doc, urgencia, _fuente = clasificar(
            nom_carp, asunto_msg, bool(msg.get("urgente")))
        if tipo_doc == TipoDocumento.OTRO and (nom_carp or asunto_msg):
            stats.setdefault("no_clasificados", set()).add(
                (nom_carp or "")[:30] + " | " + (asunto_msg or "")[:50])

        # ── DEDUP: ¿ya existe esta notificación? ──
        existe = await session.scalar(
            select(Notificacion).where(
                Notificacion.contribuyente_id == contribuyente_id,
                Notificacion.cod_mensaje_sunat == cod,
                Notificacion.tipo_msj == tipo_msj,
            )
        )
        if existe is not None:
            stats["mensajes_duplicados"] += 1
            notif_id = existe.id
            # Reclasificar EN SITIO los mensajes viejos SIN clasificar (OTRO/None).
            # Usa carpeta+asunto: la capa de asunto rescata los carpeta-NULL viejos
            # sin re-descargar PDFs (asunto/texto ya están en BD). No degrada:
            # nunca pisa una clasificación previa válida.
            if existe.tipo_documento_enum in (None, TipoDocumento.OTRO):
                cambio = False
                if cod_carp and existe.cod_carpeta is None:
                    existe.cod_carpeta = cod_carp
                    existe.nombre_carpeta = nom_carp
                    cambio = True
                if tipo_doc != TipoDocumento.OTRO or existe.urgencia != urgencia:
                    existe.tipo_documento_enum = tipo_doc
                    existe.urgencia = urgencia
                    cambio = True
                if cambio:
                    existe.clasificado_at = ahora_lima()
                    stats["reclasificados"] = stats.get("reclasificados", 0) + 1
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
                # Carpeta + clasificación (señal oficial de SUNAT).
                cod_carpeta=cod_carp,
                nombre_carpeta=nom_carp,
                tipo_documento_enum=tipo_doc,
                urgencia=urgencia,
                clasificado_at=ahora_lima(),
            )
            session.add(notif)
            await session.flush()   # para obtener notif.id
            notif_id = notif.id
            stats["mensajes_nuevos"] += 1

        # ── Adjuntos (dedup por cod_archivo, desde listAttach del detalle) ──
        detalle = msg.get("detalle") or {}
        pdfs_locales = msg.get("pdfs") or []   # rutas que descargó el scraper
        for att in (detalle.get("listAttach") or []):
            cod_arch = str(att.get("codArchivo") or "")
            nombre = att.get("nomArchivo") or ""
            if not cod_arch or not nombre:
                continue
            pdf_bytes = _leer_pdf_local(pdfs_locales, nombre)
            dup = await session.scalar(
                select(Adjunto).where(
                    Adjunto.notificacion_id == notif_id,
                    Adjunto.cod_archivo_sunat == cod_arch,
                )
            )
            if dup:
                stats["adjuntos_duplicados"] += 1
                # Backfill: si el PDF no estaba en BD y ahora lo tenemos, guardarlo.
                if dup.bytea_temporal is None and pdf_bytes:
                    dup.bytea_temporal = pdf_bytes
                    dup.descargado = True
                    dup.descargado_at = ahora_lima()
                continue
            session.add(Adjunto(
                notificacion_id=notif_id,
                estudio_id=estudio_id,
                cod_archivo_sunat=cod_arch,
                nombre_archivo=nombre,
                tamano_bytes=att.get("cntTamarch"),
                # PDF persistido en BD para que la WEB lo sirva (zAlerta-08 #4).
                bytea_temporal=pdf_bytes,
                descargado=bool(pdf_bytes),
                descargado_at=ahora_lima() if pdf_bytes else None,
            ))
            stats["adjuntos_nuevos"] += 1

    # Marcar el scrapeo en el contribuyente
    contrib = await session.get(Contribuyente, contribuyente_id)
    if contrib:
        contrib.ultimo_scrapeo_at = ahora_lima()
        contrib.ultimo_scrapeo_ok = bool(resultado.get("exito"))

    await session.commit()
    return stats