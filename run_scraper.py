"""
run_scraper.py — alerta.pe   (C:\\alertape\\run_scraper.py)
═══════════════════════════════════════════════════════════════════════
Orquestador: cierra el circuito scraper → BD.

Por cada contribuyente ACTIVO con credencial válida:
  1. descifra la clave SOL (Fernet)
  2. corre el scraper Playwright (login + buzón + mensajes + PDFs)
  3. ingesta el resultado en la BD con dedup
  4. (opcional) acumula pistas para investigar httpx

Uso:
    python run_scraper.py                 # todos los contribuyentes activos
    python run_scraper.py <RUC>           # solo ese RUC

Pensado para correr en el scheduler (Railway beat) en producción.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update, or_, and_, exists, cast, Text
from sqlalchemy.orm import selectinload

from db import get_session
from models import (Contribuyente, CredencialSol, EstadoContribuyente,
                    Notificacion, Adjunto, DocumentoValorado, BarridoMetrica,
                    TIPODOC_A_VALORADO)
from cifrado import descifrar_clave_sol
from ingesta import ingestar_resultado
import gcs

# El scraper validado (Playwright puro). Ajustar al nombre real del archivo.
import scraper_sunat_playwgth as scraper
import scraper_sunafil   # lector de la casilla SUNAFIL (SUNAFIL-1)

TZ_LIMA = ZoneInfo("America/Lima")

# Frescura para el SCHEDULER automático de fondo (detección de novedades).
# NO aplica al botón "Actualizar ahora" del contador, que usa forzar=True
# y scrapea siempre, sin importar este valor.
FRESCURA_HORAS_DEFAULT = 3


def log(msg: str, nivel: str = "INFO") -> None:
    print(f"[{datetime.now(TZ_LIMA).strftime('%d/%m/%Y %H:%M:%S')}] "
          f"[{nivel}] {msg}", flush=True)


async def _contribuyentes_a_scrapear(session, ruc_filtro: str | None):
    q = (select(Contribuyente)
         .options(selectinload(Contribuyente.credencial))
         .where(Contribuyente.estado == EstadoContribuyente.ACTIVO))
    if ruc_filtro:
        q = q.where(Contribuyente.ruc == ruc_filtro)
    return list(await session.scalars(q))


def _scrapear_sync(ruc: str, usuario_sol: str, clave_sol: str,
                   conocidos: set | None = None,
                   anio_desde: int | None = None,
                   solo_censo: bool = False,
                   backfill: bool = False) -> dict:
    """Llama al scraper Playwright (sync) con las credenciales descifradas.
    conocidos (zAlerta-46): set de cod_mensaje ya en BD → lectura incremental.
    anio_desde (zAlerta-72): año más antiguo de deuda a descargar (por buzón).
    solo_censo (Tandas CCPL): lista y cuenta por año SIN descargar nada.
    backfill (zAlerta-84): baja el histórico pendiente (sin filtro de año)."""
    cfg = scraper.SunatConfig(
        ruc=ruc, usuario_sol=usuario_sol, clave_sol=clave_sol,
        headless=True,
    )
    return scraper.scrapear_ruc(cfg, conocidos=conocidos, anio_desde=anio_desde,
                                solo_censo=solo_censo, backfill=backfill)


def _genhtml_sync(ruc: str, usuario_sol: str, clave_sol: str, pend: list) -> dict:
    """Login + captura genhtml (cuerpo + PDF) de la lista `pend` (zAlerta-94)."""
    cfg = scraper.SunatConfig(ruc=ruc, usuario_sol=usuario_sol,
                              clave_sol=clave_sol, headless=True)
    return scraper.scrapear_ruc(cfg, genhtml_pend=pend)


def _pdf_sync(ruc: str, usuario_sol: str, clave_sol: str, pend: list) -> dict:
    """Login + captura GENERAL de PDF por id_archivo de la lista (zAlerta-95)."""
    cfg = scraper.SunatConfig(ruc=ruc, usuario_sol=usuario_sol,
                              clave_sol=clave_sol, headless=True)
    return scraper.scrapear_ruc(cfg, pdf_pend=pend)


def _ceros_sync(ruc: str, usuario_sol: str, clave_sol: str, pend: list,
                diag: bool = False) -> dict:
    """Login + captura de adjuntos codArchivo=0 por nombre (zAlerta-96)."""
    cfg = scraper.SunatConfig(ruc=ruc, usuario_sol=usuario_sol,
                              clave_sol=clave_sol, headless=True)
    return scraper.scrapear_ruc(cfg, ceros_pend=pend, ceros_diag=diag)


def _ceros_de(raw_detalle) -> list:
    """nomArchivo de los adjuntos codArchivo=0 (o null) del listAttach que son
    ARCHIVO real (con nombre) y NO el generador de cuerpo (indMensaje='3')."""
    d = raw_detalle
    if not isinstance(d, dict):
        import json as _json
        try:
            d = _json.loads(raw_detalle)
        except Exception:
            return []
    out = []
    for a in (d.get("listAttach") or []):
        if not isinstance(a, dict):
            continue
        nom = a.get("nomArchivo")
        cod = a.get("codArchivo")
        if nom and (cod == 0 or cod is None) and str(a.get("indMensaje") or "") != "3":
            out.append(nom)
    return out


def _slug_arch(nom: str) -> str:
    import re as _re
    return _re.sub(r"[^A-Za-z0-9]+", "_", nom or "")[:48] or "archivo"


def _pdf_meta_de(texto_html: str | None) -> dict | None:
    """Extrae {id_archivo, sistema, cod_mensaje, numero} del JSON del mensaje si
    trae id_archivo (zAlerta-95). None si no aplica. Regla universal: id_archivo
    en el JSON → hay un PDF descargable por ese id (cualquier tipo)."""
    if not texto_html or not texto_html.strip().startswith("{"):
        return None
    import json as _json
    import html as _h
    from urllib.parse import unquote as _unq
    d = None
    for intento in (lambda: _json.loads(_unq(texto_html.strip())),
                    lambda: _json.loads(texto_html.strip())):
        try:
            d = intento(); break
        except Exception:
            continue
    if not isinstance(d, dict):
        return None
    ida = d.get("id_archivo")
    if not (ida and str(ida).isdigit()):
        return None
    return {"id_archivo": str(ida), "sistema": d.get("sistema"),
            "cod_mensaje": d.get("cod_mensaje"),
            "numero": _h.unescape(str(d.get("numero") or d.get("des_tip_doc") or ""))}


_JSON_CONTENIDO = ("adq_", "pro_", "tbodyCompras", "tbodyVentas",
                   "listaDocumentos", "lstTramEspecif", "numTicket")


def _es_cabecera_sola(texto_html: str | None) -> bool:
    """True si texto_html es JSON de SOLO cabecera (numruc/razonSocial/período…)
    SIN claves de contenido → su cuerpo real vive en el generador (zAlerta-94).
    Distingue de los subtipos que z-92/93 SÍ renderizan desde el JSON."""
    if not texto_html or not texto_html.strip().startswith("{"):
        return False
    import json as _json
    from urllib.parse import unquote as _unq
    d = None
    for intento in (lambda: _json.loads(_unq(texto_html.strip())),
                    lambda: _json.loads(texto_html.strip())):
        try:
            d = intento(); break
        except Exception:
            continue
    if not isinstance(d, dict):
        return False
    return not any(any(k.startswith(p) or k == p for p in _JSON_CONTENIDO) for k in d)


async def _sanar_gcs_desde_bytea(session, contrib: Contribuyente) -> int:
    """Sube a GCS los adjuntos que YA tienen bytes en BD (bytea_temporal) pero
    NO gcs_key, SIN tocar SUNAT (zAlerta-84). Es la mitad barata del backfill:
    los PDF bajados en la PC local quedaron sin subir; aquí se suben con los
    bytes que ya tenemos. Worker-only (si GCS no está, no hace nada)."""
    if not gcs.gcs_disponible():
        return 0
    filas = list(await session.execute(
        select(Adjunto, Notificacion.cod_mensaje_sunat)
        .join(Notificacion, Notificacion.id == Adjunto.notificacion_id)
        .where(Notificacion.contribuyente_id == contrib.id,
               Adjunto.gcs_key.is_(None),
               Adjunto.bytea_temporal.is_not(None))))
    n = 0
    for adj, cod_msg in filas:
        blob = f"{contrib.id}/adjuntos/{adj.cod_archivo_sunat}_{cod_msg}.pdf"
        key = gcs.subir_pdf(bytes(adj.bytea_temporal), blob)
        if key:
            adj.gcs_key = key
            n += 1
    if n:
        await session.commit()
    return n


async def _premarcar_cuerpo_solo(session, contrib: Contribuyente) -> int:
    """Marca revisado_sin_adjunto SIN tocar SUNAT (zAlerta-85): informativos que
    SUNAT declara sin adjunto (cant_adjuntos=0), cuyo cuerpo YA tenemos y que NO
    son deuda (los de deuda pueden tener 2º PDF por goArchivoDescarga). Colapsa el
    backlog de 'cuerpo-solo' de un tiro, así el backfill solo va por lo que falta."""
    deuda_types = list(TIPODOC_A_VALORADO.keys())
    sin_adj = ~exists().where(Adjunto.notificacion_id == Notificacion.id)
    res = await session.execute(
        update(Notificacion)
        .where(Notificacion.contribuyente_id == contrib.id,
               Notificacion.cant_adjuntos == 0,
               Notificacion.texto_html.is_not(None),
               Notificacion.revisado_sin_adjunto.is_(False),
               or_(Notificacion.tipo_documento_enum.is_(None),
                   Notificacion.tipo_documento_enum.not_in(deuda_types)),
               sin_adj)
        .values(revisado_sin_adjunto=True))
    await session.commit()
    return res.rowcount or 0


async def _reset_valorado_recuperable(session, contrib: Contribuyente) -> int:
    """zAlerta-87: quita el falso 'no disponible' de deudas cuya carátula SÍ ofrece
    la resolución (id_archivo/goArchivo presente) — recuperables. El backfill las
    reintenta con el chequeo de integridad YA corregido (guiones normalizados). Las
    que de verdad no tienen carátula quedan marcadas (no-disponible honesto)."""
    res = await session.execute(
        update(Notificacion)
        .where(Notificacion.contribuyente_id == contrib.id,
               Notificacion.valorado_no_disponible.is_(True),
               Notificacion.raw_detalle["url"].astext.like("%id_archivo%"))
        .values(valorado_no_disponible=False))
    await session.commit()
    return res.rowcount or 0


async def _cods_backfill_skip(session, contrib: Contribuyente) -> set:
    """Skip set del backfill DEUDA-AWARE (zAlerta-86). Un mensaje está COMPLETO
    (se salta) si:
      · tiene su documento_valorado (deuda: la RESOLUCIÓN ya está), o
      · es NO-deuda y tiene adjunto en GCS (constancia/informativo ya está), o
      · está revisado_sin_adjunto o valorado_no_disponible (SUNAT no lo ofrece).
    CLAVE: una DEUDA con solo su constancia en GCS pero SIN valorado NO está
    completa → se reintenta. (Ese era el bug: se saltaba y nunca traía la
    resolución del embargo/coactiva.)"""
    deuda_types = list(TIPODOC_A_VALORADO.keys())
    val_done = {str(c) for c in await session.scalars(
        select(Notificacion.cod_mensaje_sunat)
        .join(DocumentoValorado, DocumentoValorado.notificacion_id == Notificacion.id)
        .where(Notificacion.contribuyente_id == contrib.id))}
    nd_gcs = {str(c) for c in await session.scalars(
        select(Notificacion.cod_mensaje_sunat)
        .join(Adjunto, Adjunto.notificacion_id == Notificacion.id)
        .where(Notificacion.contribuyente_id == contrib.id,
               Adjunto.gcs_key.is_not(None),
               or_(Notificacion.tipo_documento_enum.is_(None),
                   Notificacion.tipo_documento_enum.not_in(deuda_types))))}
    marcados = {str(c) for c in await session.scalars(
        select(Notificacion.cod_mensaje_sunat)
        .where(Notificacion.contribuyente_id == contrib.id,
               or_(Notificacion.revisado_sin_adjunto.is_(True),
                   Notificacion.valorado_no_disponible.is_(True))))}
    return val_done | nd_gcs | marcados


async def _cods_completos(session, contrib: Contribuyente) -> tuple[set, set]:
    """Devuelve (con_gcs, revisados): cod_mensaje que ya están COMPLETOS —
    con PDF en GCS, o revisados sin adjunto (SUNAT no da PDF). El backfill los
    salta; el censo detallado los usa para contar con/sin PDF (zAlerta-85)."""
    con_gcs = {str(c) for c in await session.scalars(
        select(Notificacion.cod_mensaje_sunat)
        .join(Adjunto, Adjunto.notificacion_id == Notificacion.id)
        .where(Notificacion.contribuyente_id == contrib.id,
               Adjunto.gcs_key.is_not(None)))}
    revisados = {str(c) for c in await session.scalars(
        select(Notificacion.cod_mensaje_sunat)
        .where(Notificacion.contribuyente_id == contrib.id,
               Notificacion.revisado_sin_adjunto.is_(True)))}
    return con_gcs, revisados


async def _guardar_censo_detallado(session, contrib: Contribuyente,
                                   resultado: dict) -> dict:
    """Censo DETALLADO por año (zAlerta-85): cruza el índice del buzón
    (censo_cods del scraper) contra lo que ya está en BD/GCS, para separar
    con_pdf / pendientes / revisados. Lo guarda en censo_json y lo devuelve.
      {"2024": {"total":160, "con_pdf":90, "pendientes":70, "revisados":0}, ...}"""
    censo_cods = resultado.get("censo_cods") or {}
    con_gcs, revisados = await _cods_completos(session, contrib)
    detalle = {}
    for anio, cods in censo_cods.items():
        cset = {str(c) for c in cods}
        con = len(cset & con_gcs)
        rev = len(cset & revisados)
        detalle[str(anio)] = {
            "total": len(cset), "con_pdf": con, "revisados": rev,
            "pendientes": max(0, len(cset) - con - rev)}
    contrib.censo_json = detalle
    contrib.censo_at = datetime.now(TZ_LIMA)
    return detalle


async def procesar_contribuyente(session, contrib: Contribuyente,
                                 frescura_horas: int, forzar: bool,
                                 full: bool = False,
                                 solo_censo: bool = False,
                                 backfill: bool = False) -> None:
    # ── Control de frescura: ¿hace falta tocar SUNAT? ──
    if not forzar and contrib.ultimo_scrapeo_ok and contrib.ultimo_scrapeo_at:
        antiguedad = datetime.now(TZ_LIMA) - contrib.ultimo_scrapeo_at
        if antiguedad < timedelta(hours=frescura_horas):
            horas = round(antiguedad.total_seconds() / 3600, 1)
            log(f"  {contrib.ruc}: fresco (scrapeado hace {horas}h), "
                f"sirvo desde BD.", "INFO")
            return

    cred = contrib.credencial
    if not cred or not cred.valida:
        log(f"  {contrib.ruc}: sin credencial válida, salteado.", "WARN")
        return

    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        log(f"  {contrib.ruc}: error descifrando clave: {e}", "ERROR")
        return

    # ── Backfill (zAlerta-84): antes de tocar SUNAT, sanar barato lo que ya
    # tenemos en BD — subir a GCS los adjuntos con bytea pero sin gcs_key. ──
    if backfill:
        sanados = await _sanar_gcs_desde_bytea(session, contrib)
        if sanados:
            log(f"  {contrib.ruc}: self-heal GCS — {sanados} adjunto(s) "
                f"subido(s) desde bytea (sin tocar SUNAT).", "OK")
        premarcados = await _premarcar_cuerpo_solo(session, contrib)
        if premarcados:
            log(f"  {contrib.ruc}: pre-marca — {premarcados} informativo(s) "
                f"sin adjunto marcado(s) revisado (sin tocar SUNAT).", "OK")
        recuperables = await _reset_valorado_recuperable(session, contrib)
        if recuperables:
            log(f"  {contrib.ruc}: reset — {recuperables} deuda(s) con resolución "
                f"disponible reactivada(s) para reintentar (zAlerta-87).", "OK")

    # ── Decidir FULL vs INCREMENTAL (zAlerta-46) ──
    # Full si: se pidió (barrido nocturno de seguridad) o NUNCA hubo un full
    # exitoso (primer scan = base). Si no, incremental: solo lo nuevo.
    # solo_censo (Tandas CCPL): lista TODO (conocidos=None) para contar por año,
    # pero NO es un full de descarga y NO sella base.
    # backfill (zAlerta-84): "conocidos" = lo que YA está COMPLETO en GCS (adjunto
    # con gcs_key); se salta eso y se baja el resto de a MAX_DOCS, sin filtro año.
    if solo_censo:
        hacer_full = False
        conocidos = None
    elif backfill:
        hacer_full = False
        # "conocidos" (se saltan) = lo YA COMPLETO, deuda-aware (zAlerta-86): una
        # coactiva con solo su constancia NO cuenta como completa → se reintenta
        # su resolución. El resto son pendientes REALES → el backlog converge.
        conocidos = await _cods_backfill_skip(session, contrib)
    else:
        hacer_full = full or (contrib.ultimo_barrido_full_at is None)
        conocidos = None
        if not hacer_full:
            cods = await session.scalars(
                select(Notificacion.cod_mensaje_sunat).where(
                    Notificacion.contribuyente_id == contrib.id))
            conocidos = {str(c) for c in cods}
    modo = ("CENSO" if solo_censo else
            f"BACKFILL ({len(conocidos)} completos, salta esos)" if backfill else
            "FULL" if hacer_full else f"incremental ({len(conocidos)} conocidos)")
    log(f"  {contrib.ruc}: scrapeando [{modo}]...")

    # Año-desde de deuda POR BUZÓN (zAlerta-72): el scraper baja desde el año
    # CUBIERTO (piso de descarga); si no hay, default año_actual − 2.
    anio_desde = (contrib.anio_deuda_cubierto_desde
                  or contrib.anio_deuda_desde
                  or (datetime.now(TZ_LIMA).year - 2))

    # El scraper es sync (Playwright sync_api); lo corremos en un thread
    # para no bloquear el loop async.
    resultado = await asyncio.to_thread(
        _scrapear_sync, contrib.ruc, cred.usuario_sol, clave, conocidos,
        anio_desde, solo_censo, backfill)

    if not resultado.get("exito"):
        log(f"  {contrib.ruc}: scraping falló.", "ERROR")
        contrib.ultimo_scrapeo_at = datetime.now(TZ_LIMA)
        contrib.ultimo_scrapeo_ok = False
        await session.commit()
        return

    # ── CENSO puro (Tandas CCPL): NO se ingesta (registrar notifs volvería a
    # TODO "conocido" y las tandas incrementales saltarían todo → nunca bajarían
    # PDFs). Solo guarda el DESGLOSE por año (total/con_pdf/pendientes) y sale. ──
    if solo_censo:
        detalle = await _guardar_censo_detallado(session, contrib, resultado)
        met = resultado.get("metricas") or {}
        session.add(BarridoMetrica(
            contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
            modo="censo", peticiones=met.get("peticiones", 0),
            duracion_seg=met.get("duracion_seg"),
            docs_procesados=met.get("docs_procesados", 0),
            pdfs_descargados=0, limite_alcanzado=False, exito=True))
        await session.commit()
        total = sum(d["total"] for d in detalle.values())
        pend = sum(d["pendientes"] for d in detalle.values())
        log(f"  {contrib.ruc}: CENSO — {total} docs en {len(detalle)} año(s); "
            f"{pend} pendientes de PDF. {met.get('peticiones', 0)} peticiones, "
            f"{met.get('duracion_seg', 0)}s. (sin descargar)", "OK")
        return

    stats = await ingestar_resultado(
        session, contrib.estudio_id, contrib.id, resultado)
    # ── Métricas de barrido + censo (zAlerta-83): tablero de riesgo de ban ──
    met = resultado.get("metricas") or {}
    session.add(BarridoMetrica(
        contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
        modo=("backfill" if backfill else "full" if hacer_full else "incremental"),
        peticiones=met.get("peticiones", 0),
        duracion_seg=met.get("duracion_seg"),
        docs_procesados=met.get("docs_procesados", 0),
        pdfs_descargados=met.get("pdfs_descargados", 0),
        sinpdf_marcados=met.get("sinpdf_marcados", 0),
        limite_alcanzado=bool(met.get("limite_alcanzado")),
        exito=True))
    # Censo DETALLADO por año (zAlerta-85): solo en FULL es fiable, porque el
    # índice se listó completo. Incremental Y backfill saltan conocidos ANTES de
    # contar → índice parcial → no sobrescribir. Tras backfill, se refresca el
    # desglose con otra corrida `censo` (o el censo programado).
    if resultado.get("censo_cods") and hacer_full:
        await _guardar_censo_detallado(session, contrib, resultado)
    # Un FULL exitoso sella la base contra la que compara el incremental.
    if hacer_full:
        contrib.ultimo_barrido_full_at = datetime.now(TZ_LIMA)
    await session.commit()
    log(f"  {contrib.ruc}: OK [{modo}] — {stats['mensajes_nuevos']} nuevos, "
        f"{stats['mensajes_duplicados']} duplicados, "
        f"{stats['adjuntos_nuevos']} adjuntos nuevos.", "OK")


async def procesar_genhtml(session, contrib: Contribuyente,
                           limite: int | None = None,
                           diagnostico: bool = False) -> int:
    """Captura el cuerpo (genhtml) + PDF de los avisos de la familia
    gendocS01Alias cabecera-sola (zAlerta-94). En diagnóstico: procesa 1 e imprime
    lo capturado SIN persistir (validar el parseo en el worker antes de escalar)."""
    cred = contrib.credencial
    if not cred or not cred.valida:
        log(f"  {contrib.ruc}: sin credencial válida, salteado.", "WARN")
        return 0
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        log(f"  {contrib.ruc}: error descifrando clave: {e}", "ERROR")
        return 0
    # Familia: url del generador + cabecera-sola + aún sin cuerpo capturado +
    # SIN documento propio (ni adjunto ni valorado). Excluye coactivas/OP/multas
    # que ya tienen su PDF por z-86/87/88 — el genhtml es para los avisos mudos.
    sin_adj = ~exists().where(Adjunto.notificacion_id == Notificacion.id)
    sin_val = ~exists().where(DocumentoValorado.notificacion_id == Notificacion.id)
    todos = list(await session.scalars(
        select(Notificacion).where(
            Notificacion.contribuyente_id == contrib.id,
            Notificacion.cuerpo_capturado.is_(None),
            Notificacion.raw_detalle["url"].astext.like("%gendocS01Alias%"),
            Notificacion.texto_html.is_not(None),
            sin_adj, sin_val)))
    fam = [n for n in todos if _es_cabecera_sola(n.texto_html)]
    if diagnostico:
        fam = fam[:1]
    elif limite:
        fam = fam[:limite]
    if not fam:
        log(f"  {contrib.ruc}: sin avisos genhtml pendientes.", "INFO")
        return 0
    pend = [{"id": n.id, "cod_mensaje": n.cod_mensaje_sunat,
             "url": (n.raw_detalle or {}).get("url")} for n in fam]
    log(f"  {contrib.ruc}: capturando {len(pend)} aviso(s) genhtml"
        + (" [DIAGNÓSTICO, no persiste]" if diagnostico else "") + "...")
    resultado = await asyncio.to_thread(
        _genhtml_sync, contrib.ruc, cred.usuario_sol, clave, pend)
    if not resultado.get("exito"):
        log(f"  {contrib.ruc}: captura genhtml falló (login?).", "ERROR")
        return 0
    caps = resultado.get("genhtml_capturados") or []
    by_id = {n.id: n for n in fam}
    n_cuerpo = n_pdf = 0
    for cap in caps:
        n = by_id.get(cap["id"])
        if not n:
            continue
        if diagnostico:
            cuerpo = cap.get("cuerpo") or ""
            log(f"    [DIAG] cod={cap['cod_mensaje']} motivo={cap['motivo']} "
                f"cuerpo_len={len(cuerpo)} pdf={'sí' if cap['pdf_bytes'] else 'no'}", "INFO")
            if cuerpo:
                log("    [DIAG] cuerpo (200): "
                    + cuerpo[:200].replace("\n", " "), "INFO")
            continue
        if cap.get("cuerpo"):
            n.cuerpo_capturado = cap["cuerpo"]
            n_cuerpo += 1
        if cap.get("pdf_bytes"):
            cod_arch = f"genhtml_{n.cod_mensaje_sunat}"
            blob = f"{contrib.id}/genhtml/{n.cod_mensaje_sunat}.pdf"
            key = gcs.subir_pdf(cap["pdf_bytes"], blob) if gcs.gcs_disponible() else None
            existe = await session.scalar(select(Adjunto).where(
                Adjunto.notificacion_id == n.id,
                Adjunto.cod_archivo_sunat == cod_arch))
            if existe:
                existe.bytea_temporal = cap["pdf_bytes"]
                if key:
                    existe.gcs_key = key
            else:
                session.add(Adjunto(
                    notificacion_id=n.id, cod_archivo_sunat=cod_arch,
                    nombre_archivo=f"Reporte_{n.cod_mensaje_sunat}.pdf",
                    bytea_temporal=cap["pdf_bytes"], gcs_key=key))
            n.revisado_sin_adjunto = False       # ya tiene adjunto real
            n.cant_adjuntos = max(n.cant_adjuntos or 0, 1)
            n_pdf += 1
    if diagnostico:
        return len(caps)
    await session.commit()
    log(f"  {contrib.ruc}: genhtml OK — {n_cuerpo} cuerpo(s), {n_pdf} PDF(s) "
        f"de {len(caps)} aviso(s).", "OK")
    return len(caps)


async def procesar_pdf_general(session, contrib: Contribuyente,
                               limite: int | None = None,
                               diagnostico: bool = False) -> int:
    """Captura GENERAL de PDF (zAlerta-95): TODA notif cuyo JSON traiga id_archivo
    y aún no tenga su PDF en GCS → pide el PDF por ese id (regla universal, cubre
    Resolución de Conclusión, Cartas y cualquier tipo futuro). Log de progreso."""
    cred = contrib.credencial
    if not cred or not cred.valida:
        log(f"  {contrib.ruc}: sin credencial válida, salteado.", "WARN")
        return 0
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        log(f"  {contrib.ruc}: error descifrando clave: {e}", "ERROR")
        return 0
    # Familia: JSON con id_archivo + SIN adjunto en GCS + sin valorado.
    sin_gcs = ~exists().where(and_(
        Adjunto.notificacion_id == Notificacion.id, Adjunto.gcs_key.is_not(None)))
    sin_val = ~exists().where(DocumentoValorado.notificacion_id == Notificacion.id)
    todos = list(await session.scalars(
        select(Notificacion).where(
            Notificacion.contribuyente_id == contrib.id,
            Notificacion.texto_html.is_not(None),
            sin_gcs, sin_val)))
    fam = []
    for n in todos:
        meta = _pdf_meta_de(n.texto_html)
        if meta:
            fam.append((n, meta))
    if diagnostico:
        fam = fam[:1]
    elif limite:
        fam = fam[:limite]
    if not fam:
        log(f"  {contrib.ruc}: sin PDFs pendientes por id_archivo.", "INFO")
        return 0
    pend = [{"id": n.id, "cod_mensaje": m["cod_mensaje"] or n.cod_mensaje_sunat,
             "id_archivo": m["id_archivo"], "sistema": m["sistema"],
             "num_documento": m["numero"]} for n, m in fam]
    log(f"  {contrib.ruc}: capturando {len(pend)} PDF(s) por id_archivo"
        + (" [DIAGNÓSTICO, no persiste]" if diagnostico else "") + "...")
    resultado = await asyncio.to_thread(
        _pdf_sync, contrib.ruc, cred.usuario_sol, clave, pend)
    if not resultado.get("exito"):
        log(f"  {contrib.ruc}: captura PDF general falló (login?).", "ERROR")
        return 0
    caps = resultado.get("pdf_capturados") or []
    by_id = {n.id: n for n, _ in fam}
    n_pdf = n_nodisp = 0
    for cap in caps:
        n = by_id.get(cap["id"])
        if not n:
            continue
        if diagnostico:
            log(f"    [DIAG] cod={cap['cod_mensaje']} motivo={cap['motivo']} "
                f"pdf={'sí' if cap['pdf_bytes'] else 'no'}", "INFO")
            continue
        if cap.get("pdf_bytes"):
            blob = f"{contrib.id}/adjuntos/idarch_{cap['cod_mensaje']}.pdf"
            key = gcs.subir_pdf(cap["pdf_bytes"], blob) if gcs.gcs_disponible() else None
            existe = await session.scalar(select(Adjunto).where(
                Adjunto.notificacion_id == n.id).limit(1))
            if existe:                          # rellenar la fila vacía existente
                existe.bytea_temporal = cap["pdf_bytes"]
                if key:
                    existe.gcs_key = key
            else:
                session.add(Adjunto(
                    notificacion_id=n.id,
                    cod_archivo_sunat=f"idarch_{cap['cod_mensaje']}",
                    nombre_archivo=f"Documento_{cap['cod_mensaje']}.pdf",
                    bytea_temporal=cap["pdf_bytes"], gcs_key=key))
            n.revisado_sin_adjunto = False
            n.cant_adjuntos = max(n.cant_adjuntos or 0, 1)
            n_pdf += 1
        elif cap.get("motivo") == "pdf_vacio":
            n.valorado_no_disponible = True     # SUNAT no lo sirve (honesto)
            n_nodisp += 1
    if diagnostico:
        return len(caps)
    await session.commit()
    log(f"  {contrib.ruc}: PDF general OK — {n_pdf} capturado(s), "
        f"{n_nodisp} no-disponible(s) de {len(caps)}.", "OK")
    return len(caps)


async def procesar_ceros(session, contrib: Contribuyente,
                         limite: int | None = None,
                         diagnostico: bool = False) -> int:
    """Captura de adjuntos "de ceros" (zAlerta-96): codArchivo=0 servidos por
    NOMBRE (nomArchivo). El listAttach ya está en raw_detalle; se baja cada archivo
    real por su nombre (auto-descubre la variante de petición) y sube a GCS."""
    cred = contrib.credencial
    if not cred or not cred.valida:
        log(f"  {contrib.ruc}: sin credencial válida, salteado.", "WARN")
        return 0
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        log(f"  {contrib.ruc}: error descifrando clave: {e}", "ERROR")
        return 0
    # Candidatas: raw_detalle con codArchivo:0 en su listAttach. Refina en Python.
    cand = list(await session.scalars(
        select(Notificacion).where(
            Notificacion.contribuyente_id == contrib.id,
            cast(Notificacion.raw_detalle, Text).like('%"codArchivo": 0%'))))
    ya = set()   # (notif_id, nombre) ya en GCS → no re-bajar
    if cand:
        for nid, nom in await session.execute(
                select(Adjunto.notificacion_id, Adjunto.nombre_archivo).where(
                    Adjunto.notificacion_id.in_([n.id for n in cand]),
                    Adjunto.gcs_key.is_not(None))):
            ya.add((nid, nom))
    fam = []
    for n in cand:
        noms = [x for x in _ceros_de(n.raw_detalle) if (n.id, x) not in ya]
        if noms:
            fam.append((n, noms))
    if diagnostico:
        fam = fam[:1]
    elif limite:
        fam = fam[:limite]
    if not fam:
        log(f"  {contrib.ruc}: sin adjuntos de ceros pendientes.", "INFO")
        return 0
    pend = [{"id": n.id, "cod_mensaje": n.cod_mensaje_sunat, "tipo_msj": n.tipo_msj,
             "adjuntos": (noms[:1] if diagnostico else noms)} for n, noms in fam]
    n_arch = sum(len(p["adjuntos"]) for p in pend)
    log(f"  {contrib.ruc}: capturando {n_arch} adjunto(s) de ceros en "
        f"{len(pend)} mensaje(s)" + (" [DIAGNÓSTICO]" if diagnostico else "") + "...")
    resultado = await asyncio.to_thread(
        _ceros_sync, contrib.ruc, cred.usuario_sol, clave, pend, diagnostico)
    if not resultado.get("exito"):
        log(f"  {contrib.ruc}: captura de ceros falló (login?).", "ERROR")
        return 0
    caps = resultado.get("ceros_capturados") or []
    by_id = {n.id: n for n, _ in fam}
    n_pdf = n_nod = 0
    for cap in caps:
        n = by_id.get(cap["id"])
        if not n:
            continue
        for a in cap.get("adjuntos", []):
            if diagnostico:
                log(f"    [DIAG] '{(a['nom'] or '')[:40]}' motivo={a['motivo']} "
                    f"variante={a.get('variante')} pdf={'sí' if a['pdf_bytes'] else 'no'}", "INFO")
                continue
            if a.get("pdf_bytes"):
                slug = _slug_arch(a["nom"])
                blob = f"{contrib.id}/adjuntos/ceros_{cap['cod_mensaje']}_{slug}.pdf"
                key = gcs.subir_pdf(a["pdf_bytes"], blob) if gcs.gcs_disponible() else None
                ex = await session.scalar(select(Adjunto).where(
                    Adjunto.notificacion_id == n.id,
                    Adjunto.nombre_archivo == a["nom"]).limit(1))
                if ex:
                    ex.bytea_temporal = a["pdf_bytes"]
                    if key:
                        ex.gcs_key = key
                else:
                    session.add(Adjunto(
                        notificacion_id=n.id, cod_archivo_sunat=f"ceros_{slug}"[:50],
                        nombre_archivo=a["nom"], bytea_temporal=a["pdf_bytes"], gcs_key=key))
                n.revisado_sin_adjunto = False
                n.cant_adjuntos = max(n.cant_adjuntos or 0, 1)
                n_pdf += 1
            elif a.get("motivo") == "vacio":
                n_nod += 1
    if diagnostico:
        return len(caps)
    await session.commit()
    log(f"  {contrib.ruc}: ceros OK — {n_pdf} adjunto(s) capturado(s), "
        f"{n_nod} vacío(s) de {n_arch}.", "OK")
    return len(caps)


def _sunafil_sync(ruc: str, usuario_sol: str, clave_sol: str,
                  conocidos: set | None, diag: bool) -> dict:
    """Lee la casilla SUNAFIL (sync, Playwright) con las credenciales SOL."""
    cfg = scraper.SunatConfig(ruc=ruc, usuario_sol=usuario_sol,
                              clave_sol=clave_sol, headless=True)
    return scraper_sunafil.leer_casilla_sunafil(cfg, conocidos=conocidos, diag=diag)


async def procesar_sunafil(session, contrib: Contribuyente,
                           diagnostico: bool = False) -> int:
    """Lee el buzón SUNAFIL del contribuyente y lo ingesta por el MISMO pipeline
    (fuente='sunafil'). NUEVAS = por expediente (conocidos) + estado no leído."""
    cred = contrib.credencial
    if not cred or not cred.valida:
        log(f"  {contrib.ruc}: sin credencial válida, salteado.", "WARN")
        return 0
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        log(f"  {contrib.ruc}: error descifrando clave: {e}", "ERROR")
        return 0
    # Conocidos SUNAFIL: expedientes ya guardados (dedup por fuente). En DIAG se
    # omite (no ingesta, y así el diag corre aunque el DDL de `fuente` no esté aún).
    conocidos = None
    primera_ingesta = False
    if not diagnostico:
        cods = await session.scalars(
            select(Notificacion.cod_mensaje_sunat).where(
                Notificacion.contribuyente_id == contrib.id,
                Notificacion.fuente == "sunafil"))
        conocidos = {str(c) for c in cods}
        # ¿PRIMERA ingesta SUNAFIL de este cliente? = aún NO tiene ninguna notificación
        # SUNAFIL en nuestra base (0 conocidos). Definición robusta a flakes: si la 1ª
        # corrida no trae nada, la base sigue en 0 → la siguiente sí silencia el backlog
        # real. En cuanto guarda ≥1, deja de ser "primera" → NO re-silencia después.
        primera_ingesta = (len(conocidos) == 0)
    log(f"  {contrib.ruc}: leyendo casilla SUNAFIL ({len(conocidos or [])} conocidos)"
        + (" [DIAGNÓSTICO — vuelca DOM]" if diagnostico else "") + "...")
    resultado = await asyncio.to_thread(
        _sunafil_sync, contrib.ruc, cred.usuario_sol, clave, conocidos, diagnostico)
    if not resultado.get("exito"):
        log(f"  {contrib.ruc}: lectura SUNAFIL falló (login/navegación).", "ERROR")
        return 0
    n = len(resultado.get("mensajes", []))
    if diagnostico:
        log(f"  {contrib.ruc}: SUNAFIL-DIAG — {n} fila(s) parseada(s) "
            f"(revisa las capturas/HTML del volcado). No se ingesta.", "OK")
        return n
    stats = await ingestar_resultado(session, contrib.estudio_id, contrib.id, resultado)

    # Registro de categorías que REQUIEREN ATENCIÓN (best-effort, nunca rompe):
    #  · forma_desconocida  = sin mapeador → NO se ingiere (solo se anota su actividad).
    #  · mapeo_no_validado  = con mapeador pero sin validar contra datos → SÍ se ingiere,
    #    pero al aparecer su 1ª fila real se anota el formato para que lo CONFIRMEMOS.
    desconocidas = resultado.get("formas_desconocidas") or []
    no_validados = resultado.get("mapeos_no_validados") or []
    if desconocidas or no_validados:
        try:
            estado = dict(contrib.sunafil_desconocidas_json or {})
            ahora_iso = scraper.ahora_lima().isoformat()
            for fd in desconocidas:
                cat = fd.get("categoria") or "?"
                prev = estado.get(cat) or {}
                estado[cat] = {
                    "tipo": "forma_desconocida",
                    "primera_vez": prev.get("primera_vez", ahora_iso),
                    "ultima_vez": ahora_iso,
                    "filas": fd.get("filas", 0),
                    "columnas": fd.get("columnas", []),
                }
            for s in no_validados:
                cat = s.get("categoria") or "?"
                prev = estado.get(cat) or {}
                estado[cat] = {
                    "tipo": "mapeo_no_validado",
                    "forma": s.get("forma"),
                    "confirmado": False,
                    "primera_fila_real": prev.get("primera_fila_real", ahora_iso),
                    "ultima_vez": ahora_iso,
                    "formato_detectado": s.get("formato_detectado"),
                    "id_tipo": s.get("id_tipo"),
                    "accion_id_oculto": s.get("accion_id_oculto"),
                    "avisos_formato": s.get("avisos_formato"),
                    "columnas": s.get("columnas", []),
                }
            contrib.sunafil_desconocidas_json = estado
        except Exception as e:
            log(f"  {contrib.ruc}: no pude registrar señales SUNAFIL ({e}).", "WARN")

    # Arranque SILENCIOSO del backlog (SUNAFIL_SILENCIAR_BACKLOG=1): en la PRIMERA
    # ingesta SUNAFIL de este cliente, marca TODO su histórico como ya notificado
    # (notificado_push=True) → NO alerta el backlog acumulado de golpe. De ahí en
    # adelante alerta solo lo NUEVO no-leído. Es POR-CLIENTE y se hace UNA sola vez
    # (marcador durable sunafil_inicializado_at, aunque haya 0 filas). Con el flag en
    # 0/ausente → comportamiento normal (alerta todo lo no-leído).
    silenciados = 0
    if primera_ingesta and os.getenv("SUNAFIL_SILENCIAR_BACKLOG", "0").strip().lower() \
            in ("1", "true", "yes", "on"):
        res = await session.execute(
            update(Notificacion)
            .where(Notificacion.contribuyente_id == contrib.id,
                   Notificacion.fuente == "sunafil",
                   Notificacion.notificado_push.is_(False))
            .values(notificado_push=True, notificado_push_at=scraper.ahora_lima()))
        silenciados = res.rowcount or 0

    await session.commit()
    con_datos = sum(1 for fd in desconocidas if fd.get("filas", 0) > 0)
    extra = (f" · BACKLOG SILENCIADO ({silenciados} no-leída(s) marcadas como ya "
             f"notificadas, no alertan)") if silenciados else ""
    log(f"  {contrib.ruc}: SUNAFIL OK — {stats['mensajes_nuevos']} nueva(s), "
        f"{stats['mensajes_duplicados']} ya conocidas · "
        f"{len(desconocidas)} forma(s) desconocida(s) NO ingerida(s) ({con_datos} con datos) · "
        f"{len(no_validados)} mapeo(s) no validado(s) por CONFIRMAR{extra}.", "OK")
    return n


async def main(ruc_filtro: str | None = None, forzar: bool = False,
               frescura_horas: int = FRESCURA_HORAS_DEFAULT,
               solo_censo: bool = False, backfill: bool = False,
               genhtml: bool = False, genhtml_diag: bool = False,
               pdf: bool = False, pdf_diag: bool = False,
               ceros: bool = False, ceros_diag: bool = False,
               sunafil: bool = False, sunafil_diag: bool = False) -> None:
    log("═══ alerta.pe — orquestador scraper → BD ═══")
    if sunafil_diag:
        log("modo SUNAFIL-DIAG: login + vuelca el DOM de la casilla (NO ingesta).", "WARN")
    elif sunafil:
        log("modo SUNAFIL: lee la casilla SUNAFIL e ingesta (fuente=sunafil).", "WARN")
    elif ceros_diag:
        log("modo CEROS-DIAG: prueba variantes de descarga por nombre e imprime.", "WARN")
    elif ceros:
        log("modo CEROS: captura adjuntos codArchivo=0 por nomArchivo (zAlerta-96).", "WARN")
    elif pdf_diag:
        log("modo PDF-DIAG: captura 1 PDF por id_archivo e imprime (NO persiste).", "WARN")
    elif pdf:
        log("modo PDF: captura GENERAL por id_archivo (zAlerta-95).", "WARN")
    elif genhtml_diag:
        log("modo GENHTML-DIAG: captura 1 aviso genhtml e imprime (NO persiste).", "WARN")
    elif genhtml:
        log("modo GENHTML: captura cuerpo+PDF de avisos gendocS01Alias (zAlerta-94).", "WARN")
    elif solo_censo:
        log("modo CENSO: lista y cuenta por año SIN descargar (foto previa).", "WARN")
    elif backfill:
        log("modo BACKFILL: baja el histórico pendiente de a MAX_DOCS, "
            "salta lo ya completo en GCS. Correr con throttle alto.", "WARN")
    elif forzar:
        log("modo FORZAR: se scrapea aunque esté fresco.", "WARN")
    async with get_session() as session:
        contribs = await _contribuyentes_a_scrapear(session, ruc_filtro)
        log(f"{len(contribs)} contribuyente(s) candidato(s).")
        for contrib in contribs:
            try:
                if sunafil or sunafil_diag:
                    await procesar_sunafil(session, contrib, diagnostico=sunafil_diag)
                elif ceros or ceros_diag:
                    await procesar_ceros(session, contrib, diagnostico=ceros_diag)
                elif pdf or pdf_diag:
                    await procesar_pdf_general(session, contrib,
                                               diagnostico=pdf_diag)
                elif genhtml or genhtml_diag:
                    await procesar_genhtml(session, contrib,
                                           diagnostico=genhtml_diag)
                else:
                    await procesar_contribuyente(
                        session, contrib, frescura_horas,
                        forzar=(forzar or solo_censo or backfill),
                        solo_censo=solo_censo, backfill=backfill)
            except Exception as e:
                import traceback
                log(f"  {contrib.ruc}: error inesperado: {type(e).__name__}: {e}",
                    "ERROR")
                log("  traceback:\n" + traceback.format_exc(), "ERROR")
    log("Orquestación completa.", "OK")


if __name__ == "__main__":
    # Uso:
    #   python run_scraper.py                → todos, respeta frescura 12h
    #   python run_scraper.py <RUC>          → solo ese RUC, respeta frescura
    #   python run_scraper.py <RUC> forzar   → ignora frescura, scrapea sí o sí
    #   python run_scraper.py <RUC> censo    → foto por año SIN descargar (Tandas)
    #   python run_scraper.py <RUC> backfill → baja histórico pendiente de a MAX_DOCS
    #   python run_scraper.py <RUC> genhtml-diag → captura 1 aviso genhtml e imprime
    #   python run_scraper.py <RUC> genhtml  → captura cuerpo+PDF de avisos gendocS01Alias
    #   python run_scraper.py <RUC> pdf-diag → captura 1 PDF por id_archivo e imprime
    #   python run_scraper.py <RUC> pdf      → captura GENERAL de PDF por id_archivo
    #   python run_scraper.py <RUC> ceros-diag → prueba variantes de descarga por nombre
    #   python run_scraper.py <RUC> ceros    → captura adjuntos codArchivo=0 por nomArchivo
    #   python run_scraper.py <RUC> sunafil-diag → login + vuelca el DOM de la casilla SUNAFIL
    #   python run_scraper.py <RUC> sunafil  → lee la casilla SUNAFIL e ingesta (fuente=sunafil)
    ruc = None
    forzar = False
    solo_censo = False
    backfill = False
    genhtml = False
    genhtml_diag = False
    pdf = False
    pdf_diag = False
    ceros = False
    ceros_diag = False
    sunafil = False
    sunafil_diag = False
    for arg in sys.argv[1:]:
        if arg.lower() in ("forzar", "--forzar", "-f"):
            forzar = True
        elif arg.lower() in ("censo", "--censo", "-c"):
            solo_censo = True
        elif arg.lower() in ("backfill", "--backfill", "-b"):
            backfill = True
        elif arg.lower() in ("genhtml-diag", "--genhtml-diag"):
            genhtml_diag = True
        elif arg.lower() in ("genhtml", "--genhtml"):
            genhtml = True
        elif arg.lower() in ("pdf-diag", "--pdf-diag"):
            pdf_diag = True
        elif arg.lower() in ("pdf", "--pdf"):
            pdf = True
        elif arg.lower() in ("ceros-diag", "--ceros-diag"):
            ceros_diag = True
        elif arg.lower() in ("ceros", "--ceros"):
            ceros = True
        elif arg.lower() in ("sunafil-diag", "--sunafil-diag"):
            sunafil_diag = True
        elif arg.lower() in ("sunafil", "--sunafil"):
            sunafil = True
        else:
            ruc = arg
    asyncio.run(main(ruc, forzar, solo_censo=solo_censo, backfill=backfill,
                     genhtml=genhtml, genhtml_diag=genhtml_diag,
                     pdf=pdf, pdf_diag=pdf_diag, ceros=ceros, ceros_diag=ceros_diag,
                     sunafil=sunafil, sunafil_diag=sunafil_diag))