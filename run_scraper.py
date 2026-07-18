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
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update, or_, and_, exists
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


async def main(ruc_filtro: str | None = None, forzar: bool = False,
               frescura_horas: int = FRESCURA_HORAS_DEFAULT,
               solo_censo: bool = False, backfill: bool = False,
               genhtml: bool = False, genhtml_diag: bool = False,
               pdf: bool = False, pdf_diag: bool = False) -> None:
    log("═══ alerta.pe — orquestador scraper → BD ═══")
    if pdf_diag:
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
                if pdf or pdf_diag:
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
                log(f"  {contrib.ruc}: error inesperado: {e}", "ERROR")
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
    ruc = None
    forzar = False
    solo_censo = False
    backfill = False
    genhtml = False
    genhtml_diag = False
    pdf = False
    pdf_diag = False
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
        else:
            ruc = arg
    asyncio.run(main(ruc, forzar, solo_censo=solo_censo, backfill=backfill,
                     genhtml=genhtml, genhtml_diag=genhtml_diag,
                     pdf=pdf, pdf_diag=pdf_diag))