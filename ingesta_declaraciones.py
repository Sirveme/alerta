# -*- coding: utf-8 -*-
"""
ingesta_declaraciones.py — alerta.pe  (modo `decl-carga`, ESCRIBE en BD)
═══════════════════════════════════════════════════════════════════════════
Primera carga / carga incremental de DECLARACIONES Y PAGOS de un contribuyente.
A diferencia de `decl-diag`/`decl-diag2` (que solo vuelcan estructura), este modo
**persiste** en `declaraciones_juradas` y `pagos_tributarios`.

Diseño acordado (feat/decl-carga):
  · 01 Consulta general  → por PERÍODO, en bloques ≤6 meses (declaró / pagó /
    importe + vigencia de rectificatorias). Fuente autoritativa de la rejilla.
  · 02 Consulta DDJJ y pagos → por FECHA DE PRESENTACIÓN, misma cantidad de bloques
    ≤6 meses. Sirve para detectar presentaciones TARDÍAS que la ventana de 01 no vio.
  · Detalle de Tributos Pagados de cada boleta → `cod_tributo` fiscal real
    (best-effort: si falla, el pago igual se guarda con cod_tributo NULL).
  · Upsert IDEMPOTENTE por (contribuyente_id, num_orden). NUNCA borra; solo
    inserta/actualiza.
  · Atómico POR BLOQUE: si un bloque da `sin_acceso` / `error_filtro` /
    `sin_conexion` / `pantalla_de_aceptacion` / `timeout`, NO se escribe NADA de ese
    bloque y se reporta.
  · Timeout por bloque (def. 180 s). Guardián de aceptación ACTIVO (REGLA DURA SOL).

Ejecución: LOCAL con `railway run` (necesita DATABASE_PUBLIC_URL). NO corre en el
worker de producción. Ver run_scraper.py (modo decl-carga).

REGLA DURA SOL/SUNAT: solo navega y consulta. Si aparece una pantalla de
aceptación/consentimiento, se ABORTA ese bloque sin tocar nada (guardian_aceptacion).
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from scraper_sunat_playwgth import SunatConfig, login_sol, log, ts_lima
from scraper_sunat_declaraciones import (
    _ir_por_codigo, _enviar_filtro_01, _hay_resultados_01, _frame_con,
    _esperar_frame_con, _esperar_frame_url, _sin_acceso_visible,
    _sumar_meses, _pausa, _sanea,
)
from parser_declaraciones import (
    parse_consulta_general, parse_djj_pagos, parse_detalle_tributos,
    aplicar_detalle_a_pagos, PAGO_FORMULARIOS, PARSER_VERSION,
)
from models import DeclaracionJurada, PagoTributario

try:
    from guardian_aceptacion import detectar_aceptacion
except Exception:                                            # pragma: no cover
    def detectar_aceptacion(page):
        return (False, None)

# Códigos de menú SOL confirmados en el volcado de decl-diag2.
CODE_01, URLF_01 = "12.8.1.1.2", "consultaS01Alias"
CODE_02, URLF_02 = "12.1.1.1.4", "cdpS01Alias"
DESC_01 = "Consulta general — Detalle de declaraciones y pagos"
DESC_02 = "Consulta de declaraciones juradas y pagos"

BLOQUE_MESES = 6
TIMEOUT_BLOQUE_S = int(os.getenv("DECL_CARGA_TIMEOUT_BLOQUE", "180"))
MAX_DETALLES_BLOQUE = int(os.getenv("DECL_CARGA_MAX_DETALLES", "80"))
# Reserva de tiempo (s) que el detalle NUNCA consume: garantiza que la 02 del bloque
# siempre tenga margen (antes la 02 del 2º bloque se quedaba en timeout).
RESERVA_02_S = int(os.getenv("DECL_CARGA_RESERVA_02", "45"))

DEST = Path("./descargas")

# Estados de un sub-paso leídos "bien" (se puede escribir). Cualquier otro ⇒ el
# bloque NO escribe nada de esa fuente.
_OK = ("con_datos", "sin_datos")
# Estados que hacen PARCIAL la corrida (algo no se pudo leer/escribir).
_MALOS = ("sin_acceso", "error_filtro", "no_se_pudo_leer",
          "pantalla_de_aceptacion", "timeout", "sin_conexion")


# ───────────────────────── planificación de bloques ─────────────────────────
def _ultimo_dia_mes(d: date) -> date:
    """Último día del mes de `d`."""
    return _sumar_meses(date(d.year, d.month, 1), 1) - timedelta(days=1)


def _planificar_bloques(meses: int) -> list[dict]:
    """UN bloque = el MISMO tramo en 01 (períodos) y 02 (fechas). Devuelve, de más
    antiguo a más reciente:
        {periodo_desde, periodo_hasta (AAAAMM),
         fecha_desde, fecha_hasta (date, para la 02)}
    El bloque cubre `periodo_desde`..`periodo_desde`+5 (≤6 meses, tope en el mes
    actual). La ventana de fechas de la 02 = 1er día del 1er período .. último día
    del último período (tope hoy). Así 01 y 02 nunca quedan cruzados."""
    hoy = date.today()
    hoy_m1 = date(hoy.year, hoy.month, 1)
    inicio = _sumar_meses(hoy_m1, -(max(1, meses) - 1))
    out, desde = [], inicio
    while desde <= hoy_m1:
        hasta = _sumar_meses(desde, BLOQUE_MESES - 1)
        if hasta > hoy_m1:
            hasta = hoy_m1
        f_desde = date(desde.year, desde.month, 1)
        f_hasta = min(_ultimo_dia_mes(hasta), hoy)
        out.append({
            "periodo_desde": f"{desde.year}{desde.month:02d}",
            "periodo_hasta": f"{hasta.year}{hasta.month:02d}",
            "fecha_desde": f_desde, "fecha_hasta": f_hasta,
        })
        desde = _sumar_meses(desde, BLOQUE_MESES)
    return out


# ───────────────────────── navegación 02 por fechas ─────────────────────────
def _enviar_rango_02_fechas(page, cfg: SunatConfig, desde: date, hasta: date,
                            reabrir=None):
    """Como el `_enviar_rango_02` del diag, pero con fechas EXPLÍCITAS (ventana del
    bloque). Devuelve (scope, aviso): aviso = motivo si el formulario no cargó."""
    fr = _esperar_frame_con(page, "form[name='formFechas']", 30)
    if not fr and reabrir:
        try:
            reabrir()
        except Exception:
            pass
        fr = _esperar_frame_con(page, "form[name='formFechas']", 30)
    if not fr:
        return None, ("El formulario de fechas de la Consulta de DDJJ y pagos no "
                      "cargó (iframe ifrVCE) tras esperar + reintento.")
    try:
        fr.fill("input[name='fechaDesde']", desde.strftime("%d/%m/%Y"))
        fr.fill("input[name='fechaHasta']", hasta.strftime("%d/%m/%Y"))
        fr.evaluate("consulta(document.formFechas)")
        log(f"    02: enviado rango {desde:%d/%m/%Y}–{hasta:%d/%m/%Y}.", "INFO")
    except Exception as e:
        log(f"    02: no pude enviar el rango: {_sanea(str(e), cfg)}", "WARN")
        return fr, None
    _pausa()
    return (_frame_con(page, "form[name='formComprobante']") or fr), None


# ───────────────────────── detalle de tributos (todos) ─────────────────────────
def _restaurar_grilla_01(page, cfg: SunatConfig, periodo_desde: str) -> bool:
    """Vuelve a la grilla de la 01 tras ver un Detalle (abrir el detalle reemplaza
    la grilla). UN solo intento: 1 reapertura + 1 filtro, SIN reintento anidado.
    Devuelve True si la grilla volvió."""
    try:
        _ir_por_codigo(page, CODE_01, DESC_01)
        _esperar_frame_url(page, URLF_01, 15, abortar_si=_sin_acceso_visible)
        _enviar_filtro_01(page, cfg, BLOQUE_MESES, periodo_desde, reabrir=None)
    except Exception:
        return False
    return _hay_resultados_01(page)


def _capturar_detalles(page, cfg: SunatConfig, pagos: list[dict],
                       periodo_desde: str, vence_en: float) -> list[dict]:
    """Para cada pago con puntero `detalle_tributos`, abre el 'Detalle de Tributos
    Pagados' y lo parsea → tributo fiscal real. Best-effort: un fallo NO aborta el
    bloque (el pago se guarda con cod_tributo NULL). Entre boletas re-filtra la 01
    con UN solo intento; si no vuelve la grilla, se corta (no se martilla)."""
    objetivo = [pg for pg in pagos if pg.get("detalle_tributos")]
    detalles: list[dict] = []
    for i, pg in enumerate(objetivo):
        if time.monotonic() > vence_en or i >= MAX_DETALLES_BLOQUE:
            log("    04: corto el detalle (reserva de tiempo para la 02 / tope "
                "de boletas del bloque).", "WARN")
            break
        sc = _frame_con(page, "a[href*='verDetalleTributos(']")
        if not sc:
            break
        a = pg["detalle_tributos"]     # {ruc, cod_form, tipnro, num_orden}
        try:
            sc.evaluate(
                "(a)=>verDetalleTributos(a.t,a.r,a.f,a.tp,a.n)",
                {"t": "Detalle de Tributos Pagados", "r": a.get("ruc"),
                 "f": a.get("cod_form"), "tp": a.get("tipnro"),
                 "n": a.get("num_orden")})
        except Exception:
            continue
        _pausa()
        fr = (_esperar_frame_con(page, "table[id^='tblVeriPagoRpta']", 15)
              or _frame_con(page, "table[id^='tblVeriPagoRpta']"))
        if fr:
            try:
                detalles.append(parse_detalle_tributos(fr.content()))
            except Exception:
                pass
        if i + 1 < len(objetivo):          # volver a la grilla para la siguiente
            if not _restaurar_grilla_01(page, cfg, periodo_desde):
                log("    04: no pude volver a la grilla tras el detalle; corto "
                    "(los pagos restantes quedan con cod_tributo NULL).", "WARN")
                break
    return detalles


def _dump_pantalla(page, cfg: SunatConfig, etiqueta: str) -> str | None:
    """Vuelca el HTML ENMASCARADO de la página + frames (para inspeccionar una
    pantalla marcada como aceptación). No hace clic, no descarga."""
    try:
        carpeta = DEST / (f"decl_carga_{cfg.ruc}_"
                          f"{time.strftime('%Y%m%d_%H%M%S')}_{etiqueta}")
        carpeta.mkdir(parents=True, exist_ok=True)
        scopes = [page]
        try:
            scopes += list(page.frames)
        except Exception:
            pass
        for i, sc in enumerate(scopes):
            try:
                (carpeta / f"{i:02d}.html").write_text(
                    _sanea(sc.content(), cfg), encoding="utf-8")
            except Exception:
                pass
        return str(carpeta)
    except Exception:
        return None


# ───────────────────────── recolección (sync, Playwright) ─────────────────────────
def recolectar(cfg: SunatConfig, meses: int) -> dict:
    """Login + recorrido por bloques (01 + detalle + 02). NO escribe en BD: devuelve
    los datos parseados por bloque para que la capa async los persista. Cada bloque
    trae su estado (para decidir si se escribe)."""
    res = {"exito": False, "motivo": None, "ruc": cfg.ruc, "bloques": []}
    bloques = _planificar_bloques(meses)

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
                res["motivo"] = "login"
                log("  decl-carga: login SOL rechazado "
                    "(¿clave? ¿sesión en uso por el worker?).", "ERROR")
                return res
            log(f"  decl-carga: login OK. {len(bloques)} bloque(s) de ≤6 meses "
                f"(últimos {meses} meses).", "OK")

            for bloque in bloques:
                res["bloques"].append(_procesar_bloque(page, cfg, bloque))
            res["exito"] = True
        except Exception as e:                       # red/navegador caído
            res["motivo"] = "sin_conexion"
            log(f"  decl-carga: corte de navegación: {_sanea(str(e), cfg)}", "ERROR")
        finally:
            try:
                ctx.close(); nav.close()
            except Exception:
                pass
    return res


def _procesar_bloque(page, cfg: SunatConfig, bloque: dict) -> dict:
    """Un bloque: 01 (grilla + detalle) y 02 (rango por fecha), SOBRE EL MISMO TRAMO.
    Devuelve datos + estados; captura sus propias excepciones (nunca rompe la
    corrida)."""
    periodo_desde = bloque["periodo_desde"]
    f_desde, f_hasta = bloque["fecha_desde"], bloque["fecha_hasta"]
    bl = {"periodo_desde": periodo_desde, "periodo_hasta": bloque["periodo_hasta"],
          "ventana_02": [f_desde.isoformat(), f_hasta.isoformat()],
          "estado_01": "no_se_pudo_leer", "estado_02": "no_se_pudo_leer",
          "declaraciones": [], "pagos": [], "presentaciones": [],
          "aviso_01": None, "aviso_02": None, "aceptacion": None,
          "dump": None, "at": ts_lima()}
    vence_en = time.monotonic() + TIMEOUT_BLOQUE_S
    # Reserva de tiempo para la 02: el detalle de la 01 nunca pasa de aquí.
    detalle_vence = vence_en - RESERVA_02_S
    # AMBOS rangos del bloque, en el mismo tramo (lo pidió Duilio).
    log(f"  · bloque {periodo_desde}..{bloque['periodo_hasta']} | "
        f"01 períodos {periodo_desde[4:]}/{periodo_desde[:4]}–"
        f"{bloque['periodo_hasta'][4:]}/{bloque['periodo_hasta'][:4]} | "
        f"02 fechas {f_desde:%d/%m/%Y}–{f_hasta:%d/%m/%Y}", "INFO")

    # ── 01 Consulta general ────────────────────────────────────────────────
    try:
        if not _ir_por_codigo(page, CODE_01, DESC_01):
            bl["aviso_01"] = "no se pudo invocar el código 01"
        else:
            _esperar_frame_url(page, URLF_01, 20, abortar_si=_sin_acceso_visible)
            acc = _aceptacion(page)
            if acc:
                bl["estado_01"] = "pantalla_de_aceptacion"
                bl["aceptacion"] = acc
                bl["dump"] = _dump_pantalla(page, cfg, "acept01")
                log(f"    01: pantalla de aceptación ({acc}); NO se toca nada, no "
                    f"se escribe el bloque. HTML en {bl['dump']}.", "WARN")
            elif _sin_acceso_visible(page):
                bl["estado_01"] = "sin_acceso"
            elif time.monotonic() > vence_en:
                bl["estado_01"] = "timeout"
            else:
                # 1 filtro, con 1 reintento interno máximo (reabrir).
                _sc, aviso = _enviar_filtro_01(
                    page, cfg, BLOQUE_MESES, periodo_desde,
                    reabrir=lambda: _ir_por_codigo(page, CODE_01, DESC_01))
                if _sin_acceso_visible(page):
                    bl["estado_01"] = "sin_acceso"
                elif aviso or not _hay_resultados_01(page):
                    bl["estado_01"] = "error_filtro"
                    bl["aviso_01"] = aviso or ("La Consulta general no avanzó a "
                                               "resultados (rango ≤ 6 meses).")
                else:
                    grid = _frame_con(page, "#tblDetalleDeclPagos")
                    cg = parse_consulta_general(grid.content() if grid else "")
                    bl["declaraciones"] = cg.get("declaraciones", [])
                    bl["pagos"] = cg.get("pagos", [])
                    # Detalle de Tributos Pagados → cod_tributo real (best-effort),
                    # con reserva de tiempo para que la 02 no quede en timeout.
                    if bl["pagos"] and time.monotonic() < detalle_vence:
                        dets = _capturar_detalles(page, cfg, bl["pagos"],
                                                  periodo_desde, detalle_vence)
                        if dets:
                            aplicar_detalle_a_pagos(bl["pagos"], dets)
                    bl["estado_01"] = ("con_datos"
                                       if (bl["declaraciones"] or bl["pagos"])
                                       else "sin_datos")
    except Exception as e:
        bl["aviso_01"] = _sanea(f"{type(e).__name__}: {e}", cfg)
        bl["estado_01"] = "no_se_pudo_leer"

    # ── 02 Consulta de DDJJ y pagos (MISMO tramo, por fecha de presentación) ─
    try:
        if time.monotonic() > vence_en:
            bl["estado_02"] = "timeout"
        elif not _ir_por_codigo(page, CODE_02, DESC_02):
            bl["aviso_02"] = "no se pudo invocar el código 02"
        else:
            _esperar_frame_url(page, URLF_02, 20, abortar_si=_sin_acceso_visible)
            acc = _aceptacion(page)
            if acc:
                bl["estado_02"] = "pantalla_de_aceptacion"
                bl["aceptacion"] = bl["aceptacion"] or acc
                bl["dump"] = bl["dump"] or _dump_pantalla(page, cfg, "acept02")
            elif _sin_acceso_visible(page):
                bl["estado_02"] = "sin_acceso"
            else:
                sc, aviso = _enviar_rango_02_fechas(
                    page, cfg, f_desde, f_hasta,
                    reabrir=lambda: _ir_por_codigo(page, CODE_02, DESC_02))
                if _sin_acceso_visible(page):
                    bl["estado_02"] = "sin_acceso"
                elif aviso:
                    bl["estado_02"] = "error_filtro"
                    bl["aviso_02"] = aviso
                else:
                    dp = parse_djj_pagos((sc or page).content()
                                         if hasattr(sc or page, "content") else "")
                    bl["presentaciones"] = dp.get("presentaciones", [])
                    bl["estado_02"] = ("con_datos" if bl["presentaciones"]
                                       else "sin_datos")
    except Exception as e:
        bl["aviso_02"] = _sanea(f"{type(e).__name__}: {e}", cfg)
        bl["estado_02"] = "no_se_pudo_leer"

    _pausa()
    return bl


def _aceptacion(page):
    """Guardián (REGLA DURA): devuelve el detalle si la pantalla es de aceptación,
    None si no. NUNCA hace clic."""
    try:
        es, det = detectar_aceptacion(page)
    except Exception:
        return None
    return det if es else None


# ───────────────────────── persistencia (async, upsert) ─────────────────────────
def _es_pago(cod_form: str | None) -> bool:
    return bool(cod_form) and cod_form.strip() in PAGO_FORMULARIOS


def _fila_decl(contrib, d: dict) -> dict:
    return {
        "id": uuid.uuid4(),
        "contribuyente_id": contrib.id,
        "estudio_id": getattr(contrib, "estudio_id", None),
        "periodo": d.get("periodo"),
        "cod_formulario": d.get("cod_formulario"),
        "desc_formulario": d.get("desc_formulario"),
        "num_orden": d.get("num_orden"),
        "fecha_presentacion": d.get("fecha_presentacion"),
        "es_rectificatoria": bool(d.get("es_rectificatoria", False)),
        "es_vigente": bool(d.get("es_vigente", True)),
        "importe_pagado": d.get("importe_pagado"),
        "origen": d.get("origen", "01"),
        "parser_version": PARSER_VERSION,
    }


def _fila_pago(contrib, pg: dict) -> dict:
    return {
        "id": uuid.uuid4(),
        "contribuyente_id": contrib.id,
        "estudio_id": getattr(contrib, "estudio_id", None),
        "periodo": pg.get("periodo"),
        "cod_formulario": pg.get("cod_formulario"),
        "desc_formulario": pg.get("desc_formulario"),
        "num_orden": pg.get("num_orden"),
        "fecha_pago": pg.get("fecha_pago"),
        "banco_receptor": pg.get("banco_receptor"),
        "cod_tributo": pg.get("cod_tributo"),
        "cod_tributo_boleta": pg.get("cod_tributo_boleta"),
        "importe": pg.get("importe"),
        "origen": pg.get("origen", "01"),
        "parser_version": PARSER_VERSION,
    }


async def _upsert_declaraciones(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(DeclaracionJurada.__table__).values(filas)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_declaracion_numorden",
        set_={
            "periodo": stmt.excluded.periodo,
            "cod_formulario": stmt.excluded.cod_formulario,
            "desc_formulario": stmt.excluded.desc_formulario,
            "fecha_presentacion": stmt.excluded.fecha_presentacion,
            "es_rectificatoria": stmt.excluded.es_rectificatoria,
            "es_vigente": stmt.excluded.es_vigente,
            "importe_pagado": stmt.excluded.importe_pagado,
            "origen": stmt.excluded.origen,
            "parser_version": stmt.excluded.parser_version,
            "actualizado_at": func.now(),
        })
    await session.execute(stmt)
    return len(filas)


async def _upsert_pagos(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(PagoTributario.__table__).values(filas)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_pago_numorden",
        set_={
            "periodo": stmt.excluded.periodo,
            "cod_formulario": stmt.excluded.cod_formulario,
            "desc_formulario": stmt.excluded.desc_formulario,
            "fecha_pago": stmt.excluded.fecha_pago,
            "banco_receptor": stmt.excluded.banco_receptor,
            "cod_tributo": stmt.excluded.cod_tributo,
            "cod_tributo_boleta": stmt.excluded.cod_tributo_boleta,
            "importe": stmt.excluded.importe,
            "origen": stmt.excluded.origen,
            "parser_version": stmt.excluded.parser_version,
            "actualizado_at": func.now(),
        })
    await session.execute(stmt)
    return len(filas)


async def _insertar_si_ausente_decl(session, filas: list[dict]) -> int:
    """Para presentaciones que SOLO vio la 02 (tardías, fuera de la ventana de 01):
    inserta si no existe; si ya existe, NO toca (la 01 es más rica)."""
    if not filas:
        return 0
    stmt = pg_insert(DeclaracionJurada.__table__).values(filas)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_declaracion_numorden")
    r = await session.execute(stmt)
    return r.rowcount or 0


async def _insertar_si_ausente_pago(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(PagoTributario.__table__).values(filas)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_pago_numorden")
    r = await session.execute(stmt)
    return r.rowcount or 0


async def cargar_declaraciones(session, contrib, meses: int = 12) -> dict:
    """Orquesta la carga de un contribuyente: recolecta (hilo, Playwright) y
    persiste por bloque (idempotente, nunca borra). Devuelve un resumen con conteos
    y los estados por bloque. El commit es POR BLOQUE: un bloque con problema NO
    escribe nada y no arrastra a los demás."""
    import asyncio

    cred = getattr(contrib, "credencial", None)
    if not cred:
        return {"exito": False, "motivo": "sin_credencial",
                "ruc": contrib.ruc, "bloques": []}
    from cifrado import descifrar_clave_sol
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        return {"exito": False, "motivo": f"clave:{type(e).__name__}",
                "ruc": contrib.ruc, "bloques": []}

    cfg = SunatConfig(ruc=contrib.ruc, usuario_sol=cred.usuario_sol,
                      clave_sol=clave, headless=True)
    rec = await asyncio.to_thread(recolectar, cfg, meses)

    resumen = {"exito": rec.get("exito", False), "motivo": rec.get("motivo"),
               "ruc": contrib.ruc, "meses": meses, "bloques": [],
               "decl_escritas": 0, "pagos_escritos": 0,
               "decl_tardias": 0, "pagos_tardios": 0,
               "parcial": True, "incidencias": [], "dumps": []}
    if not rec.get("exito"):
        resumen["incidencias"].append(f"la corrida no completó ({rec.get('motivo')})")
        return resumen

    # num_orden que la 01 cubrió (para no pisar con la 02).
    num_orden_01: set[str] = set()

    # ── Fase A: 01 por bloque (atómico) ────────────────────────────────────
    for bl in rec["bloques"]:
        estado = bl["estado_01"]
        info = {"periodo_desde": bl["periodo_desde"],
                "periodo_hasta": bl.get("periodo_hasta"), "estado_01": estado,
                "estado_02": bl["estado_02"], "aviso_01": bl.get("aviso_01"),
                "aviso_02": bl.get("aviso_02"), "aceptacion": bl.get("aceptacion"),
                "dump": bl.get("dump"), "decl": 0, "pagos": 0, "escrito_01": False}
        if bl.get("dump"):
            resumen["dumps"].append(bl["dump"])
        if estado in _OK:
            try:
                filas_d = [_fila_decl(contrib, d) for d in bl["declaraciones"]
                           if d.get("num_orden") and d.get("periodo")]
                filas_p = [_fila_pago(contrib, pg) for pg in bl["pagos"]
                           if pg.get("num_orden") and pg.get("periodo")]
                nd = await _upsert_declaraciones(session, filas_d)
                npg = await _upsert_pagos(session, filas_p)
                await session.commit()
                for f in filas_d + filas_p:
                    num_orden_01.add(str(f["num_orden"]))
                resumen["decl_escritas"] += nd
                resumen["pagos_escritos"] += npg
                info["decl"], info["pagos"], info["escrito_01"] = nd, npg, True
            except Exception as e:
                await session.rollback()
                info["aviso_01"] = f"error de escritura: {type(e).__name__}: {e}"[:180]
                info["estado_01"] = "no_se_pudo_leer"
        resumen["bloques"].append(info)

    # ── Fase B: 02 → presentaciones tardías (insert-if-absent) ─────────────
    tardias_d, tardias_p = [], []
    for bl in rec["bloques"]:
        if bl["estado_02"] not in _OK:
            continue
        for pr in bl["presentaciones"]:
            no = str(pr.get("num_orden") or "")
            if not no or no in num_orden_01:
                continue
            fila_base = {
                "periodo": pr.get("periodo"),
                "cod_formulario": pr.get("cod_formulario"),
                "desc_formulario": pr.get("desc_formulario"),
                "num_orden": pr.get("num_orden"),
                "origen": "02",
            }
            if _es_pago(pr.get("cod_formulario")):
                p = {**fila_base, "fecha_pago": pr.get("fecha_presentacion")}
                tardias_p.append(_fila_pago(contrib, p))
            else:
                d = {**fila_base, "fecha_presentacion": pr.get("fecha_presentacion"),
                     "importe_pagado": None, "es_vigente": True,
                     "es_rectificatoria": False}
                tardias_d.append(_fila_decl(contrib, d))
    if tardias_d or tardias_p:
        try:
            resumen["decl_tardias"] = await _insertar_si_ausente_decl(session, tardias_d)
            resumen["pagos_tardios"] = await _insertar_si_ausente_pago(session, tardias_p)
            await session.commit()
        except Exception as e:
            await session.rollback()
            resumen["incidencias"].append(f"02 tardías no se escribieron: "
                                          f"{type(e).__name__}")

    # ── PARCIAL si algún bloque no se leyó/escribió bien (01 o 02) ──────────
    for info in resumen["bloques"]:
        tramo = f"{info['periodo_desde']}..{info.get('periodo_hasta')}"
        if info["estado_01"] in _MALOS:
            resumen["incidencias"].append(
                f"bloque {tramo} 01={info['estado_01']}"
                + (f" ({info['aviso_01']})" if info.get("aviso_01") else ""))
        if info["estado_02"] in _MALOS:
            resumen["incidencias"].append(
                f"bloque {tramo} 02={info['estado_02']}"
                + (f" ({info['aviso_02']})" if info.get("aviso_02") else ""))
    resumen["parcial"] = bool(resumen["incidencias"])
    return resumen
