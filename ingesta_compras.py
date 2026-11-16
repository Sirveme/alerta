# -*- coding: utf-8 -*-
"""
ingesta_compras.py — alerta.pe  (proyecto vistas-club)
═══════════════════════════════════════════════════════════════════════
Modo `compras-carga <RUC> <periodo>`: lee el Registro de Compras Electrónico
(RCE / SIRE, modalidad "propuesta") de SOL y **PERSISTE** en comprobantes_compra
(+ comprobante_items). Mismo espíritu que decl-carga:

  · SOLO CONSULTA (REGLA DURA SOL): lee la propuesta; NUNCA la acepta/registra.
    Guardián de aceptación ACTIVO (guardian_aceptacion.detectar_aceptacion).
  · Upsert IDEMPOTENTE por (contribuyente, proveedor, tipo, serie, número) y por
    (comprobante, línea). Nunca borra; solo inserta/actualiza.
  · Atómico: si no hay acceso / aceptación / timeout / corte → NO escribe y reporta
    "COMPRAS-CARGA PARCIAL" (nunca "OK" si falta algo).
  · Corre a MANO (local con `railway run`), nunca en el worker.

Endpoints (verificados contra los volcados comp_diag_*):
  · LISTA (cabeceras, PAGINADA): api-sire …/rce/propuesta/web/propuesta/{periodo}/busqueda
    — menú SOL 60.2.2.1.1 (SIRE Compras); el bearer se captura de la red.
  · DETALLE (ítems por CPE): servicio api-cpe (menú 11.38.1.1.1). Su URL exacta NO
    quedó en los artefactos del diag; se toma de COMPRAS_DETALLE_CPE_URL (env). Si no
    está, se cargan las cabeceras y los ítems quedan PENDIENTES (PARCIAL).
"""
from __future__ import annotations

import asyncio
import math
import os
import uuid
from datetime import date

from playwright.sync_api import sync_playwright
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func

from scraper_sunat_playwgth import SunatConfig, login_sol, log, ts_lima
from scraper_sunat_declaraciones import _ir_por_codigo, _sanea, _pausa
from parser_compras import (parse_lista_compras, parse_detalle_items, clave_cpe,
                            PARSER_VERSION)
from models import ComprobanteCompra, ComprobanteItem

try:
    from guardian_aceptacion import detectar_aceptacion
except Exception:                                   # pragma: no cover
    def detectar_aceptacion(page):                  # fallback inocuo
        return (False, None)

# ── Config ──────────────────────────────────────────────────────────────
CODE_SIRE_COMPRAS = os.getenv("COMPRAS_CODE_SIRE", "60.2.2.1.1")
CODE_CPE_CONSULTA = os.getenv("COMPRAS_CODE_CPE", "11.38.1.1.1")
URL_BUSQUEDA = ("https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros/"
                "rce/propuesta/web/propuesta/{periodo}/busqueda"
                "?codTipoOpe=1&page={page}&perPage={per}")
PER_PAGE = int(os.getenv("COMPRAS_PER_PAGE", "20"))
# URL del detalle de un CPE (api-cpe). Placeholders disponibles: {ruc_prov} {tipo}
# {serie} {numero} {periodo} {ruc}. Si no se define, los ítems quedan pendientes.
DETALLE_CPE_URL = os.getenv("COMPRAS_DETALLE_CPE_URL", "").strip()
TIMEOUT_BEARER_S = float(os.getenv("COMPRAS_TIMEOUT_BEARER", "40"))
MAX_PAGINAS = int(os.getenv("COMPRAS_MAX_PAGINAS", "50"))

_OK = ("con_datos", "sin_datos")
_MALOS = ("sin_acceso", "pantalla_de_aceptacion", "timeout", "error_filtro",
          "sin_conexion", "no_se_pudo_leer")
DEST = "./descargas"


# ── Captura del bearer de la red ────────────────────────────────────────
def _capturar_bearer(page, host_frag: str) -> dict:
    """Devuelve {'authorization':…, 'headers':{…}} de la 1.ª request a `host_frag`
    que lleve Authorization. Hay que registrar ANTES de abrir la operación."""
    capt: dict = {}

    def _on_request(req):
        if capt:
            return
        try:
            if host_frag not in (req.url or ""):
                return
            h = req.headers
            auth = h.get("authorization") or h.get("Authorization")
            if auth and "bearer" in auth.lower():
                capt["authorization"] = auth
                capt["headers"] = dict(h)
        except Exception:
            pass

    page.on("request", _on_request)
    return capt


def _esperar(cond, timeout_s: float, intervalo: float = 0.5) -> bool:
    import time
    fin = time.monotonic() + timeout_s
    while time.monotonic() < fin:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(intervalo)
    return False


def _headers_api(capt: dict) -> dict:
    """Headers para replicar la llamada JSON (hereda los del SPA, sin los de cuerpo)."""
    base = {k: v for k, v in (capt.get("headers") or {}).items()
            if k.lower() not in ("content-length", "content-type", "host",
                                 "origin", "referer", "accept-encoding")}
    base["authorization"] = capt.get("authorization", "")
    base.setdefault("accept", "application/json")
    return base


def _get_json(page, url: str, headers: dict):
    """GET JSON con la sesión del navegador + bearer. Devuelve (status, data|None)."""
    try:
        resp = page.request.get(url, headers=headers, timeout=45000)
        st = resp.status
        if st != 200:
            return st, None
        try:
            return st, resp.json()
        except Exception:
            return st, None
    except Exception:
        return None, None


# ── Recolección (hilo, Playwright sync) ─────────────────────────────────
def recolectar_compras(cfg: SunatConfig, periodo: str) -> dict:
    """Login + SIRE propuesta (cabeceras, paginado) + detalle api-cpe (si hay
    endpoint). NO escribe en BD: devuelve datos parseados y el estado."""
    res = {"exito": False, "motivo": None, "ruc": cfg.ruc, "periodo": periodo,
           "estado": "sin_conexion", "cabeceras": [], "items_por_clave": {},
           "totales": {}, "incidencias": [], "dump": None}

    with sync_playwright() as p:
        nav = p.chromium.launch(
            headless=cfg.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = nav.new_context(
            locale="es-PE", timezone_id="America/Lima",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        try:
            page.set_default_timeout(45000)
        except Exception:
            pass
        try:
            if not login_sol(page, cfg):
                res["motivo"], res["estado"] = "login", "sin_acceso"
                log("  compras-carga: login SOL rechazado "
                    "(¿clave? ¿sesión en uso por el worker?).", "ERROR")
                return res

            # ── SIRE Compras: capturar bearer y paginar la propuesta ──
            capt_sire = _capturar_bearer(page, "api-sire.sunat.gob.pe")
            _ir_por_codigo(page, CODE_SIRE_COMPRAS, "SIRE Compras")
            _pausa()
            # REGLA DURA: si la pantalla pide aceptar algo, abortar sin tocar nada.
            es_acept, detalle = detectar_aceptacion(page)
            if es_acept:
                res["estado"], res["motivo"] = "pantalla_de_aceptacion", detalle
                res["dump"] = _dump(page, cfg, "acept_sire")
                log(f"  compras-carga: pantalla de aceptación ({detalle}); "
                    "abortado sin clic.", "ERROR")
                return res
            if not _esperar(lambda: bool(capt_sire), TIMEOUT_BEARER_S):
                res["estado"], res["motivo"] = "sin_acceso", "sin_bearer_sire"
                res["incidencias"].append(
                    "no se capturó el token SIRE (¿sin acceso al RCE?).")
                res["dump"] = _dump(page, cfg, "sire_sin_bearer")
                return res

            headers = _headers_api(capt_sire)
            st, data = _get_json(
                page, URL_BUSQUEDA.format(periodo=periodo, page=1, per=PER_PAGE),
                headers)
            if st != 200 or data is None:
                res["estado"], res["motivo"] = "error_filtro", f"busqueda_status={st}"
                res["incidencias"].append(f"la búsqueda RCE devolvió status {st}.")
                return res

            parsed = parse_lista_compras(data)
            cabeceras = list(parsed["cabeceras"])
            res["totales"] = parsed["totales"]
            total = (parsed["paginacion"] or {}).get("totalRegistros") or len(cabeceras)
            paginas = min(MAX_PAGINAS, math.ceil(total / PER_PAGE) if total else 1)
            for pg_i in range(2, paginas + 1):
                st, data = _get_json(
                    page,
                    URL_BUSQUEDA.format(periodo=periodo, page=pg_i, per=PER_PAGE),
                    headers)
                if st == 200 and data:
                    cabeceras.extend(parse_lista_compras(data)["cabeceras"])
                else:
                    res["incidencias"].append(f"página {pg_i} RCE: status {st}.")
                _pausa()

            res["cabeceras"] = cabeceras
            res["estado"] = "con_datos" if cabeceras else "sin_datos"
            log(f"  compras-carga: RCE {periodo} → {len(cabeceras)} comprobante(s) "
                f"(de {total}).", "OK")

            # ── Detalle de ítems (api-cpe) — solo si hay endpoint configurado ──
            if cabeceras and DETALLE_CPE_URL:
                _recolectar_items(page, cfg, periodo, cabeceras, res)
            elif cabeceras and not DETALLE_CPE_URL:
                res["incidencias"].append(
                    "ítems PENDIENTES: falta COMPRAS_DETALLE_CPE_URL (endpoint "
                    "api-cpe del detalle). Se cargaron solo las cabeceras.")

            res["exito"] = True
        except Exception as e:
            res["estado"], res["motivo"] = "sin_conexion", "excepcion"
            log(f"  compras-carga: corte de navegación: {_sanea(str(e), cfg)}",
                "ERROR")
        finally:
            try:
                ctx.close(); nav.close()
            except Exception:
                pass
    return res


def _recolectar_items(page, cfg: SunatConfig, periodo: str,
                      cabeceras: list[dict], res: dict) -> None:
    """Abre el servicio CPE, captura su bearer y baja el detalle de cada CPE según
    COMPRAS_DETALLE_CPE_URL. Best-effort: lo que falle queda sin ítems (PARCIAL)."""
    capt_cpe = _capturar_bearer(page, "api-cpe")
    _ir_por_codigo(page, CODE_CPE_CONSULTA, "Consulta CPE")
    _pausa()
    es_acept, detalle = detectar_aceptacion(page)
    if es_acept:
        res["incidencias"].append(f"CPE: pantalla de aceptación ({detalle}); "
                                  "ítems omitidos sin tocar nada.")
        res["dump"] = _dump(page, cfg, "acept_cpe")
        return
    if not _esperar(lambda: bool(capt_cpe), TIMEOUT_BEARER_S):
        res["incidencias"].append("no se capturó el token CPE; ítems pendientes.")
        return
    headers = _headers_api(capt_cpe)
    ok = 0
    for cab in cabeceras:
        url = DETALLE_CPE_URL.format(
            ruc=cfg.ruc, periodo=periodo,
            ruc_prov=cab.get("ruc_proveedor") or "",
            tipo=cab.get("cod_tipo_cdp") or "",
            serie=cab.get("serie") or "", numero=cab.get("numero") or "")
        st, data = _get_json(page, url, headers)
        if st == 200 and data:
            det = parse_detalle_items(data)
            if det["items"]:
                res["items_por_clave"][clave_cpe(cab)] = det["items"]
                ok += 1
        _pausa()
    log(f"  compras-carga: detalle de ítems en {ok}/{len(cabeceras)} comprobantes.",
        "OK")
    if ok < len(cabeceras):
        res["incidencias"].append(
            f"ítems incompletos: {ok}/{len(cabeceras)} comprobantes con detalle.")


def _dump(page, cfg: SunatConfig, etiqueta: str):
    """Vuelca HTML enmascarado de la pantalla (para inspección ante aceptación)."""
    import pathlib
    try:
        carpeta = pathlib.Path(DEST) / f"compras_carga_{cfg.ruc}_{ts_lima()}_{etiqueta}"
        carpeta.mkdir(parents=True, exist_ok=True)
        for i, fr in enumerate([page] + list(page.frames)):
            try:
                html = fr.content() if hasattr(fr, "content") else ""
            except Exception:
                continue
            (carpeta / f"scope_{i}.html").write_text(
                _sanea(html, cfg), encoding="utf-8")
        return str(carpeta)
    except Exception:
        return None


# ── Persistencia (upsert idempotente) ───────────────────────────────────
def _fila_comprobante(contrib, cab: dict) -> dict:
    return {
        "id": uuid.uuid4(),
        "contribuyente_id": contrib.id,
        "estudio_id": getattr(contrib, "estudio_id", None),
        "periodo": cab.get("periodo"),
        "cod_car": cab.get("cod_car"),
        "cod_tipo_cdp": cab.get("cod_tipo_cdp"),
        "desc_tipo_cdp": cab.get("desc_tipo_cdp"),
        "serie": cab.get("serie"),
        "numero": cab.get("numero"),
        "numero_final": cab.get("numero_final"),
        "fecha_emision": cab.get("fecha_emision"),
        "fecha_vencimiento": cab.get("fecha_vencimiento"),
        "tipo_doc_proveedor": cab.get("tipo_doc_proveedor"),
        "ruc_proveedor": cab.get("ruc_proveedor"),
        "razon_social_proveedor": cab.get("razon_social_proveedor"),
        "moneda": cab.get("moneda"),
        "tipo_cambio": cab.get("tipo_cambio"),
        "cod_estado": cab.get("cod_estado"),
        "estado": cab.get("estado"),
        "base_gravada": cab.get("base_gravada"),
        "igv": cab.get("igv"),
        "base_exon_inaf": cab.get("base_exon_inaf"),
        "valor_adq_ng": cab.get("valor_adq_ng"),
        "isc": cab.get("isc"),
        "icbper": cab.get("icbper"),
        "otros_tributos": cab.get("otros_tributos"),
        "importe_total": cab.get("importe_total"),
        "ind_detraccion": cab.get("ind_detraccion"),
        "ind_oper_gratuita": cab.get("ind_oper_gratuita"),
        "num_inconsistencias": cab.get("num_inconsistencias"),
        "origen": "rce",
        "parser_version": PARSER_VERSION,
    }


_SET_COMPROBANTE = ("periodo", "cod_car", "desc_tipo_cdp", "numero_final",
                    "fecha_emision", "fecha_vencimiento", "tipo_doc_proveedor",
                    "razon_social_proveedor", "moneda", "tipo_cambio", "cod_estado",
                    "estado", "base_gravada", "igv", "base_exon_inaf",
                    "valor_adq_ng", "isc", "icbper", "otros_tributos",
                    "importe_total", "ind_detraccion", "ind_oper_gratuita",
                    "num_inconsistencias", "origen", "parser_version")


async def _upsert_comprobantes(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(ComprobanteCompra.__table__).values(filas)
    set_ = {c: getattr(stmt.excluded, c) for c in _SET_COMPROBANTE}
    set_["actualizado_at"] = func.now()
    stmt = stmt.on_conflict_do_update(constraint="uq_compra_cpe", set_=set_)
    await session.execute(stmt)
    return len(filas)


async def _map_ids(session, contrib, periodo: str) -> dict:
    """Mapa {(ruc_prov,tipo,serie,numero): id} de los comprobantes del período."""
    q = select(
        ComprobanteCompra.id, ComprobanteCompra.ruc_proveedor,
        ComprobanteCompra.cod_tipo_cdp, ComprobanteCompra.serie,
        ComprobanteCompra.numero
    ).where(ComprobanteCompra.contribuyente_id == contrib.id,
            ComprobanteCompra.periodo == periodo)
    out = {}
    for r in (await session.execute(q)).all():
        out[(r.ruc_proveedor, r.cod_tipo_cdp, r.serie, r.numero)] = r.id
    return out


def _fila_item(comprobante_id, it: dict) -> dict:
    return {
        "id": uuid.uuid4(),
        "comprobante_id": comprobante_id,
        "linea": it.get("linea"),
        "codigo": it.get("codigo"),
        "descripcion": it.get("descripcion"),
        "unidad": it.get("unidad"),
        "desc_unidad": it.get("desc_unidad"),
        "cantidad": it.get("cantidad"),
        "valor_unitario": it.get("valor_unitario"),
        "descuento": it.get("descuento"),
        "icbper": it.get("icbper"),
        "importe_total": it.get("importe_total"),
        "parser_version": PARSER_VERSION,
    }


_SET_ITEM = ("codigo", "descripcion", "unidad", "desc_unidad", "cantidad",
             "valor_unitario", "descuento", "icbper", "importe_total",
             "parser_version")


async def _upsert_items(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(ComprobanteItem.__table__).values(filas)
    set_ = {c: getattr(stmt.excluded, c) for c in _SET_ITEM}
    stmt = stmt.on_conflict_do_update(constraint="uq_compra_item_linea", set_=set_)
    await session.execute(stmt)
    return len(filas)


async def cargar_compras(session, contrib, periodo: str) -> dict:
    """Orquesta la carga de Compras de un contribuyente/período: recolecta (hilo,
    Playwright) y persiste (idempotente, nunca borra). Devuelve un resumen con
    conteos y estado. PARCIAL si algo faltó."""
    cred = getattr(contrib, "credencial", None)
    if not cred:
        return {"exito": False, "motivo": "sin_credencial", "ruc": contrib.ruc,
                "periodo": periodo, "parcial": True, "incidencias": ["sin credencial SOL"]}
    from cifrado import descifrar_clave_sol
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        return {"exito": False, "motivo": f"clave:{type(e).__name__}",
                "ruc": contrib.ruc, "periodo": periodo, "parcial": True,
                "incidencias": [f"no se pudo descifrar la clave: {type(e).__name__}"]}

    cfg = SunatConfig(ruc=contrib.ruc, usuario_sol=cred.usuario_sol,
                      clave_sol=clave, headless=True)
    rec = await asyncio.to_thread(recolectar_compras, cfg, periodo)

    resumen = {"exito": rec.get("exito", False), "motivo": rec.get("motivo"),
               "ruc": contrib.ruc, "periodo": periodo, "estado": rec.get("estado"),
               "comprobantes": 0, "items": 0, "parcial": True,
               "incidencias": list(rec.get("incidencias") or []),
               "dump": rec.get("dump"), "totales": rec.get("totales") or {}}

    if not rec.get("exito") or rec.get("estado") in _MALOS:
        resumen["incidencias"].insert(
            0, f"la corrida no completó (estado={rec.get('estado')}, "
               f"motivo={rec.get('motivo')}).")
        return resumen

    cabeceras = rec.get("cabeceras") or []
    if not cabeceras:
        resumen["parcial"] = False       # sin_datos legítimo (el período no tuvo compras)
        return resumen

    # ── Cabeceras (atómico) ──
    try:
        filas_c = [_fila_comprobante(contrib, c) for c in cabeceras
                   if c.get("ruc_proveedor") and c.get("serie") and c.get("numero")]
        n_c = await _upsert_comprobantes(session, filas_c)
        await session.commit()
        resumen["comprobantes"] = n_c
    except Exception as e:
        await session.rollback()
        resumen["incidencias"].append(
            f"error al escribir cabeceras: {type(e).__name__}: {e}"[:180])
        return resumen

    # ── Ítems (si se recolectaron) ──
    items_por_clave = rec.get("items_por_clave") or {}
    if items_por_clave:
        try:
            ids = await _map_ids(session, contrib, periodo)
            filas_i = []
            for clave, items in items_por_clave.items():
                cid = ids.get(clave)
                if not cid:
                    continue
                for it in items:
                    if it.get("linea") is not None:
                        filas_i.append(_fila_item(cid, it))
            n_i = await _upsert_items(session, filas_i)
            await session.commit()
            resumen["items"] = n_i
        except Exception as e:
            await session.rollback()
            resumen["incidencias"].append(
                f"error al escribir ítems: {type(e).__name__}: {e}"[:180])

    # PARCIAL salvo que: cabeceras OK y (ítems completos o no se esperaban ítems).
    sin_items_pend = not any("tems" in i or "PENDIENTE" in i.upper()
                             for i in resumen["incidencias"])
    resumen["parcial"] = not sin_items_pend
    return resumen
