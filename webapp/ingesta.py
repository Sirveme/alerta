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

import io
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Contribuyente, Notificacion, Adjunto, TipoDocumento,
    DocumentoValorado, TipoValorado)
from clasificacion import clasificar, subtipo_coactivo
import gcs

TZ_LIMA = ZoneInfo("America/Lima")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def _texto_pdf(bytes_: bytes) -> str:
    """Extrae el texto de un PDF (pypdf). Para la constancia de pago (zAlerta-74)."""
    try:
        import pypdf
        rd = pypdf.PdfReader(io.BytesIO(bytes_))
        return "\n".join((p.extract_text() or "") for p in rd.pages)
    except Exception as e:
        print(f"[ingesta] no se pudo extraer texto del PDF: {e}", flush=True)
        return ""


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


async def _guardar_valorado(session, estudio_id, contribuyente_id, notif_id,
                            cod_mensaje, val, stats) -> None:
    """Sube el 2º PDF de deuda a GCS e inserta la CABECERA MÍNIMA en
    documento_valorado (zAlerta-34): contribuyente, notificación, tipo,
    num_documento, pdf_texto crudo, gcs_key, parseado_ok=False. El parseo de
    importe/periodo/tributo va en zAlerta-35. Dedup por notificacion_id."""
    # Dedup: un valorado por notificación.
    existe_id = await session.scalar(
        select(DocumentoValorado.id).where(
            DocumentoValorado.notificacion_id == notif_id))

    num_doc = (val.get("num_documento") or "").strip() or None
    # gcs_key: {contribuyente_id}/valorados/{num_doc}_{cod_mensaje}.pdf
    nombre = f"{num_doc or 'doc'}_{cod_mensaje}.pdf".replace("/", "-").replace(" ", "")
    blob_path = f"{contribuyente_id}/valorados/{nombre}"
    gcs_key = gcs.subir_pdf(val["pdf_bytes"], blob_path)   # None si GCS no configurado

    try:
        tipo_val = TipoValorado(val["tipo_valorado"])
    except (ValueError, KeyError):
        tipo_val = TipoValorado.ORDEN_PAGO

    if existe_id:
        dv = await session.get(DocumentoValorado, existe_id)
        if dv:   # backfill de lo que faltara (texto/gcs_key), sin pisar lo bueno.
            if not dv.pdf_texto and val.get("pdf_texto"):
                dv.pdf_texto = val["pdf_texto"]
            if not dv.gcs_key and gcs_key:
                dv.gcs_key = gcs_key
        return

    session.add(DocumentoValorado(
        contribuyente_id=contribuyente_id,
        notificacion_id=notif_id,
        estudio_id=estudio_id,
        tipo_valorado=tipo_val,
        num_documento=num_doc,
        pdf_texto=val.get("pdf_texto"),
        gcs_key=gcs_key,
        parseado_ok=False,
    ))
    stats["valorados_guardados"] = stats.get("valorados_guardados", 0) + 1


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
        # Subtipo coactivo (zAlerta-70): solo si es cobranza coactiva.
        sub_coa = (subtipo_coactivo(asunto_msg)
                   if tipo_doc == TipoDocumento.COBRANZA_COACTIVA else None)
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
            # Upgrade a PAGO (zAlerta-69): los 1662 viejos quedaron como AVISO;
            # al re-barrer se corrigen a PAGO (categoría propia). Solo sube a PAGO,
            # nunca degrada una clasificación de deuda ya válida.
            elif (tipo_doc == TipoDocumento.PAGO
                  and existe.tipo_documento_enum != TipoDocumento.PAGO):
                existe.tipo_documento_enum = TipoDocumento.PAGO
                existe.urgencia = urgencia
                existe.clasificado_at = ahora_lima()
                stats["reclasificados"] = stats.get("reclasificados", 0) + 1
            # Upgrade AVISO/otro → COACTIVA (zAlerta-78): las coactivas SIN guiones
            # (Retención, genérica, Conclusión, FL) caían como AVISO. Al re-barrer
            # se corrigen a coactiva con su subtipo y color.
            elif (tipo_doc == TipoDocumento.COBRANZA_COACTIVA
                  and existe.tipo_documento_enum != TipoDocumento.COBRANZA_COACTIVA):
                existe.tipo_documento_enum = TipoDocumento.COBRANZA_COACTIVA
                existe.subtipo_coactivo = sub_coa
                existe.urgencia = urgencia
                existe.clasificado_at = ahora_lima()
                stats["reclasificados"] = stats.get("reclasificados", 0) + 1
            # Backfill del SUBTIPO coactivo (zAlerta-70): las coactivas viejas no
            # tienen subtipo; al re-barrer se les asigna y se ajusta el color
            # (un Levantamiento deja de ser rojo).
            elif (tipo_doc == TipoDocumento.COBRANZA_COACTIVA
                  and sub_coa and existe.subtipo_coactivo != sub_coa):
                existe.subtipo_coactivo = sub_coa
                existe.urgencia = urgencia
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
                subtipo_coactivo=sub_coa,
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
        pago_bytes = None                       # constancia del 1662 (zAlerta-74)
        for att in (detalle.get("listAttach") or []):
            cod_arch = str(att.get("codArchivo") or "")
            nombre = att.get("nomArchivo") or ""
            if not cod_arch or not nombre:
                continue
            pdf_bytes = _leer_pdf_local(pdfs_locales, nombre)
            if pdf_bytes and pago_bytes is None:
                pago_bytes = pdf_bytes   # la constancia del 1662 (zAlerta-74)
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

        # ── PAGO (1662): la constancia ES el adjunto, no un 2º PDF (zAlerta-74) ──
        # Crea su DocumentoValorado(PAGO) desde los bytes del adjunto: extrae el
        # texto (para asociar pago↔valor y mostrar los datos) y sube el PDF a GCS.
        if tipo_doc == TipoDocumento.PAGO and pago_bytes:
            ya_pago = await session.scalar(select(DocumentoValorado.id).where(
                DocumentoValorado.notificacion_id == notif_id))
            if not ya_pago:
                try:
                    texto = _texto_pdf(pago_bytes)
                    m = re.search(r"orden\s*(\d{4,})", asunto_msg or "", re.I)
                    num_doc = m.group(1) if m else None
                    blob = f"{contribuyente_id}/valorados/pago_{num_doc or 'x'}_{cod}.pdf"
                    gcs_key = gcs.subir_pdf(pago_bytes, blob)
                    session.add(DocumentoValorado(
                        contribuyente_id=contribuyente_id, notificacion_id=notif_id,
                        estudio_id=estudio_id, tipo_valorado=TipoValorado.PAGO,
                        num_documento=num_doc, pdf_texto=texto, gcs_key=gcs_key))
                    stats["valorados_guardados"] = stats.get("valorados_guardados", 0) + 1
                except Exception as e:
                    print(f"[ingesta] valorado PAGO cod={cod} falló (sigo): "
                          f"{type(e).__name__}: {e}", flush=True)

        # ── 2º PDF de DEUDA → GCS + documento_valorado (zAlerta-34) ──
        # zAlerta-37 BUG A: AISLADO en savepoint. Un fallo del valorado (GCS,
        # constraint, lo que sea) revierte SOLO esa fila, NUNCA el lote de
        # notificaciones ya ingestadas. La sesión queda sana para el commit final.
        val = msg.get("valorado")
        if val and val.get("pdf_bytes"):
            try:
                async with session.begin_nested():
                    await _guardar_valorado(
                        session, estudio_id, contribuyente_id, notif_id, cod, val, stats)
            except Exception as e:
                stats["valorados_error"] = stats.get("valorados_error", 0) + 1
                print(f"[ingesta] valorado cod={cod} falló (sigo): "
                      f"{type(e).__name__}: {e}", flush=True)

    # Marcar el scrapeo en el contribuyente
    contrib = await session.get(Contribuyente, contribuyente_id)
    if contrib:
        contrib.ultimo_scrapeo_at = ahora_lima()
        contrib.ultimo_scrapeo_ok = bool(resultado.get("exito"))

    await session.commit()
    return stats