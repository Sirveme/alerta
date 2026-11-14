"""
scraper_sunat_declaraciones.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Modo DIAGNÓSTICO 'decl-diag' de DECLARACIONES Y PAGOS (menú SOL).

Qué hace: reusa el login SOL y recorre, en modo SOLO LECTURA, las pantallas de
"Declaraciones y pagos" del contribuyente, y VUELCA únicamente su ESTRUCTURA:
menú, URLs (como patrón, sin valores), tablas con sus encabezados, formularios
con sus campos (nombres/tipos/opciones, NUNCA valores), iframes y las peticiones
de red (método + patrón + status + content-type HTML/JSON).

Qué NO hace: no guarda nada en la BD, no descarga comprobantes ni PDFs, no
rellena ni envía formularios con datos reales. Es la fase de descubrimiento,
igual que `sunafil-diag` (diag-first): primero se ve la estructura, LUEGO se
escribe el parser.

Ejecución: LOCAL con `railway run` y NUNCA dentro del worker de producción
(no se invoca desde el ciclo). Ver run_scraper.py (modo decl-diag).

Enmascarado (requisito duro): TODO volcado —HTML, URLs, logs— pasa por
`enmascarar` + una redacción literal del usuario SOL y la clave. Las CAPTURAS de
pantalla van DESACTIVADAS por defecto (el banner de SOL muestra el usuario); se
activan con DECL_DIAG_SHOTS=1 bajo tu responsabilidad.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

# Reusa primitivas ya validadas del scraper SUNAT (login, config, log enmascarado).
from scraper_sunat_playwgth import SunatConfig, login_sol, log, ts_lima

try:
    from seguridad_logs import enmascarar as _mask
except Exception:                                   # pragma: no cover
    def _mask(x):
        return str(x) if x is not None else ""

TZ_LIMA = ZoneInfo("America/Lima")
DEST = Path("./descargas")
PAUSA_SEG = float(os.getenv("DECL_DIAG_PAUSA_SEG", "4"))      # entre pantallas
SHOTS = os.getenv("DECL_DIAG_SHOTS", "0") == "1"             # capturas (riesgo)

# Pantallas objetivo (menú SOL). `frag` = fragmentos de texto del camino de menú,
# de la raíz a la hoja; el diag intenta abrir la hoja por texto (best-effort). Si
# no la encuentra, reporta "no_se_pudo_leer" y sigue (nunca rompe).
TARGETS = [
    {"slug": "01_consulta_general",
     "desc": "Detalle de declaraciones y pagos → Consulta general "
             "(Período, N° Form, N° Orden, Descripción, Fecha Pres., Banco, "
             "Código-Tributo, Importe pagado)",
     "frag": ["declaraciones y pagos", "consulta general"]},
    {"slug": "02_consulta_djj_pagos",
     "desc": "Consulta de declaraciones juradas y pagos (por fecha de "
             "presentación, rangos ≤6 meses; enlace 'Comprobante' por fila)",
     "frag": ["consulta de declaraciones juradas"]},
    {"slug": "04_detalle_tributos_pagados",
     "desc": "Detalle de Tributos Pagados (boletas 1662)",
     "frag": ["detalle de tributos pagados"]},
    {"slug": "05_detracciones_recaudacion",
     "desc": "Detracciones — Consulta de ingreso como recaudación",
     "frag": ["ingreso como recaudación"]},
    {"slug": "06_detracciones_npd",
     "desc": "Detracciones — Consulta de NPD",
     "frag": ["consulta de npd"]},
    # El 03 (Constancia del Formulario 0621) se alcanza DESDE una fila de la
    # pantalla 02 (enlace 'Comprobante'), que requiere enviar un rango de fechas.
    # Es una SEGUNDA pasada, una vez que el diag nos dé los campos del formulario.
]


# ── Utilidades de enmascarado ────────────────────────────────────────
def _sanea(txt, cfg: SunatConfig) -> str:
    """Enmascarado genérico (token=/DNI/email) + redacción literal de los secretos
    conocidos (usuario SOL y clave). Se aplica a TODO lo que se escribe o loguea."""
    if txt is None:
        return ""
    t = _mask(str(txt))
    for secreto in (getattr(cfg, "usuario_sol", ""), getattr(cfg, "clave_sol", "")):
        if secreto:
            t = t.replace(secreto, "***")
    return t


def _patron_url(u: str, cfg: SunatConfig) -> str:
    """Devuelve el PATRÓN de la URL: ruta + claves de query SIN valores (así nunca
    se filtra un token). p.ej. '/cl-ti-itmenu/x.htm?token=&ruc='."""
    if not u:
        return ""
    base, _, query = u.partition("?")
    claves = [kv.split("=", 1)[0] for kv in query.split("&") if kv]
    patron = base + ("?" + "=&".join(claves) + "=" if claves else "")
    return _sanea(patron, cfg)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _pausa():
    try:
        time.sleep(PAUSA_SEG)
    except Exception:
        pass


# ── Extracción de estructura (sin valores sensibles) ─────────────────
def _tablas(scope, cfg: SunatConfig) -> list:
    out = []
    for t in scope.query_selector_all("table"):
        heads = [_sanea(th.inner_text(), cfg)
                 for th in t.query_selector_all("thead th")]
        if not heads:
            first = t.query_selector("tr")
            if first:
                heads = [_sanea(c.inner_text(), cfg)
                         for c in first.query_selector_all("th,td")]
        filas = t.query_selector_all("tbody tr") or t.query_selector_all("tr")
        out.append({"id": t.get_attribute("id"), "class": t.get_attribute("class"),
                    "encabezados": [h for h in heads if h][:20],
                    "n_filas": len(filas)})
    return out


def _formularios(scope, cfg: SunatConfig) -> list:
    out = []
    for f in scope.query_selector_all("form"):
        campos = []
        for el in f.query_selector_all("input, select, textarea"):
            try:
                tag = (el.evaluate("e => e.tagName") or "").lower()
            except Exception:
                tag = ""
            opciones = None
            if tag == "select":
                opciones = [_sanea(o.inner_text(), cfg)
                            for o in el.query_selector_all("option")][:12]
            campos.append({"tag": tag, "name": el.get_attribute("name"),
                           "type": el.get_attribute("type"),
                           "id": el.get_attribute("id"), "opciones": opciones})
        out.append({"action": _patron_url(f.get_attribute("action") or "", cfg),
                    "method": (f.get_attribute("method") or "get").lower(),
                    "campos": campos[:40]})
    return out


def _scopes(page):
    """La página y sus frames (las operaciones SOL cargan en iframes)."""
    yield page
    for fr in page.frames:
        if fr is not page.main_frame:
            yield fr


def _volcar(page, carpeta: Path, slug: str, cfg: SunatConfig) -> dict:
    """Vuelca HTML enmascarado (página + frames SUNAT) y devuelve la estructura.
    Captura de pantalla solo si DECL_DIAG_SHOTS=1 (puede mostrar el usuario SOL)."""
    info = {"slug": slug, "url": _patron_url(page.url, cfg),
            "titulo": _sanea(page.title() if hasattr(page, "title") else "", cfg),
            "tablas": [], "formularios": [], "iframes": [], "tiene_datos": False,
            "archivos": []}
    for i, sc in enumerate(_scopes(page)):
        try:
            tablas = _tablas(sc, cfg)
            forms = _formularios(sc, cfg)
        except Exception:
            tablas, forms = [], []
        info["tablas"] += tablas
        info["formularios"] += forms
        # HTML enmascarado del scope (frame incluido) si parece de SUNAT.
        try:
            url = getattr(sc, "url", "") or ""
            if i == 0 or "sunat" in url.lower():
                nombre = f"{slug}.html" if i == 0 else f"{slug}_frame{i}.html"
                (carpeta / nombre).write_text(_sanea(sc.content(), cfg),
                                              encoding="utf-8")
                info["archivos"].append(nombre)
                if i > 0:
                    info["iframes"].append(_patron_url(url, cfg))
        except Exception:
            pass
    info["tiene_datos"] = any(t["n_filas"] > 0 for t in info["tablas"])
    if SHOTS:
        try:
            page.screenshot(path=str(carpeta / f"{slug}.png"), full_page=True)
            info["archivos"].append(f"{slug}.png")
        except Exception:
            pass
    return info


def _menu_catalogo(page, cfg: SunatConfig) -> list:
    """Catálogo del menú SOL: cada opción con su texto y destino (href/onclick/id),
    enmascarado. Es el artefacto clave de la 1ª corrida: con él mapeamos las rutas
    exactas de cada pantalla sin adivinar."""
    cat = []
    for sc in _scopes(page):
        try:
            nodos = sc.query_selector_all("a, li[onclick], span[onclick], button")
        except Exception:
            continue
        for a in nodos:
            txt = _norm(a.inner_text() or "")
            if not txt or len(txt) > 80:
                continue
            cat.append({"texto": _sanea(txt, cfg),
                        "href": _patron_url(a.get_attribute("href") or "", cfg),
                        "onclick": _sanea((a.get_attribute("onclick") or "")[:120], cfg),
                        "id": a.get_attribute("id")})
    # Dedup por (texto,id) conservando orden.
    visto, uniq = set(), []
    for c in cat:
        k = (c["texto"], c["id"])
        if k not in visto:
            visto.add(k)
            uniq.append(c)
    return uniq[:400]


def _buscar_por_texto(scope, frag: str):
    f = _norm(frag)
    try:
        nodos = scope.query_selector_all("a, li[onclick], span[onclick], button")
    except Exception:
        return None
    for a in nodos:
        if f in _norm(a.inner_text() or ""):
            return a
    return None


def _abrir_opcion(page, fragmentos: list):
    """Abre la hoja del menú por texto (best-effort). Expande por los fragmentos
    previos si están presentes. Devuelve la Page destino (popup) o la misma page;
    None si no encontró la opción."""
    for previo in fragmentos[:-1]:
        el = None
        for sc in _scopes(page):
            el = _buscar_por_texto(sc, previo)
            if el:
                break
        if el:
            try:
                el.click(timeout=4000)
                _pausa()
            except Exception:
                pass
    objetivo = fragmentos[-1]
    hoja = None
    for sc in _scopes(page):
        hoja = _buscar_por_texto(sc, objetivo)
        if hoja:
            break
    if not hoja:
        return None
    try:
        with page.context.expect_page(timeout=3500) as pop:
            hoja.click(timeout=5000)
        _pausa()
        return pop.value                         # abrió en pestaña nueva
    except Exception:
        _pausa()
        return page                              # cargó en iframe / misma página


def diag_declaraciones(cfg: SunatConfig) -> dict:
    """Orquesta el diag: login → catálogo de menú → recorre pantallas y vuelca
    estructura. NO escribe en BD. Devuelve resumen (para run_scraper)."""
    res = {"exito": False, "pantallas": [], "menu": [], "carpeta": None, "red": []}
    ts = datetime.now(TZ_LIMA).strftime("%Y%m%d_%H%M%S")
    carpeta = DEST / f"decl_diag_{cfg.ruc}_{ts}"
    carpeta.mkdir(parents=True, exist_ok=True)
    res["carpeta"] = str(carpeta)

    red: list = []
    cur = {"t": "00_login"}

    def _on_response(resp):
        try:
            u = resp.url
            if "sunat" not in u.lower():
                return
            red.append({"pantalla": cur["t"], "metodo": resp.request.method,
                        "url": _patron_url(u, cfg), "status": resp.status,
                        "ctype": ((resp.headers or {}).get("content-type", ""))[:40]})
        except Exception:
            pass

    with sync_playwright() as p:
        nav = p.chromium.launch(
            headless=cfg.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = nav.new_context(
            locale="es-PE", timezone_id="America/Lima",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"))
        ctx.on("response", _on_response)
        page = ctx.new_page()
        try:
            if not login_sol(page, cfg):
                res["motivo"] = "login"
                log("  decl-diag: login SOL rechazado (¿clave? ¿sesión en uso "
                    "por el worker?).", "ERROR")
                return res
            log("  decl-diag: login OK. Vuelco catálogo de menú...", "OK")
            cur["t"] = "00_menu"
            res["menu"] = _menu_catalogo(page, cfg)
            res["pantallas"].append(_volcar(page, carpeta, "00_menu", cfg))
            try:
                (carpeta / "00_menu_catalogo.txt").write_text(
                    "\n".join(f"- {c['texto']}  [id={c['id']}] {c['href']} "
                              f"{c['onclick']}" for c in res["menu"]),
                    encoding="utf-8")
            except Exception:
                pass

            for tg in TARGETS:
                cur["t"] = tg["slug"]
                ahora = ts_lima()
                log(f"  decl-diag: abriendo «{tg['slug']}»...", "INFO")
                try:
                    destino = _abrir_opcion(page, tg["frag"])
                    if destino is None:
                        info = {"slug": tg["slug"], "estado": "no_se_pudo_leer",
                                "detalle": "opción no encontrada por texto", "at": ahora}
                    else:
                        info = _volcar(destino, carpeta, tg["slug"], cfg)
                        info["estado"] = ("con_datos" if info["tiene_datos"]
                                          else "sin_datos")
                        info["at"] = ahora
                        if destino is not page:               # cerrar popup
                            try:
                                destino.close()
                            except Exception:
                                pass
                except Exception as e:
                    info = {"slug": tg["slug"], "estado": "no_se_pudo_leer",
                            "detalle": _sanea(f"{type(e).__name__}: {e}", cfg),
                            "at": ahora}
                info["pantalla"] = tg["desc"]
                res["pantallas"].append(info)
                _pausa()
            res["red"] = red
            res["exito"] = True
        finally:
            try:
                ctx.close(); nav.close()
            except Exception:
                pass
    return res
