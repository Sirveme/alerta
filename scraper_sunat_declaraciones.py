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
from datetime import datetime, date, timedelta
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

# Guardián de seguridad transversal: nunca tocar pantallas de aceptación.
from guardian_aceptacion import texto_es_aceptacion

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


# ── Clasificación del estado de una pantalla ─────────────────────────
# SOL muestra tablas de LAYOUT (cabecera, pie, calendario del NPD) con filas que
# NO son datos. Para no cantar con_datos en falso, solo cuentan las filas de una
# tabla de RESULTADOS (cuyos encabezados tienen señales reales) y, además, no
# debe aparecer un marcador de "sin registros".
_MARCA_SIN_REGISTROS = (
    "no existen registros", "no se encontraron", "no se encontro",
    "no hay registros", "1 a 0 de 0", "sin resultados", "no se hallaron",
    "no existe informacion", "no existe información", "no se encontró",
    "no se encontraron declaraciones", "no se encontraron solicitudes",
)
# Usuario SECUNDARIO sin el perfil para la opción (p.ej. FACTURA1/FACTURAL):
# SOL responde con un aviso de permisos, no con una tabla vacía.
_MARCA_SIN_ACCESO = (
    "no cuenta con los permisos", "no cuenta con permiso", "no tiene permiso",
    "no tiene acceso", "no esta autorizado", "no está autorizado",
    "acceso denegado", "no cuenta con el perfil", "no tiene el perfil",
    "opcion no disponible", "opción no disponible", "no autorizado",
    "no tiene asignada", "no cuenta con privilegios", "usted no tiene",
    "no esta habilitado", "no está habilitado", "perfil no",
)
# Encabezados que delatan una tabla de RESULTADOS (≥2 → es tabla de datos).
_SIG_ENCABEZADOS = (
    "periodo", "orden", "importe", "formulario", "tributo", "npd",
    "monto exigible", "banco", "fecha de pres",
)


def _texto_scopes(page) -> str:
    """Texto visible de la página + sus frames (para marcadores)."""
    partes = []
    for sc in _scopes(page):
        try:
            partes.append(sc.inner_text("body"))
        except Exception:
            pass
    return re.sub(r"\s+", " ", " ".join(partes)).lower()


def _hay_marcador(texto: str, marcadores) -> bool:
    return any(m in texto for m in marcadores)


def _sin_acceso_visible(page) -> bool:
    """True si la página/frames ya muestran el aviso de falta de permiso/perfil SOL
    (usuario secundario sin la opción, p.ej. 'acceso no autorizado'). Permite ABORTAR
    las esperas de iframe sin gastar el timeout completo (antes se perdían ~3 min por
    RUC sin acceso esperando un frame de operación que nunca iba a cargar)."""
    try:
        return _hay_marcador(_texto_scopes(page), _MARCA_SIN_ACCESO)
    except Exception:
        return False


def _tabla_es_resultado(t: dict) -> bool:
    heads = " ".join(t.get("encabezados") or []).lower()
    return sum(1 for k in _SIG_ENCABEZADOS if k in heads) >= 2


def _estado_desde(info: dict) -> str:
    """Estado honesto de una pantalla a partir de las señales de _volcar."""
    if info.get("estado") == "no_se_pudo_leer":
        return "no_se_pudo_leer"
    # SEGURIDAD: una pantalla de aceptación/consentimiento se reporta y NO se toca.
    if info.get("aceptacion"):
        return "pantalla_de_aceptacion"
    if info.get("sin_acceso"):
        return "sin_acceso"
    return "con_datos" if info.get("tiene_datos") else "sin_datos"


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
    # Datos REALES: filas de tablas de resultados (no layout) y sin marcador de
    # "sin registros". Además, detecta falta de acceso (usuario secundario).
    texto = _texto_scopes(page)
    info["sin_acceso"] = _hay_marcador(texto, _MARCA_SIN_ACCESO)
    # SEGURIDAD: marcar (no tocar) pantallas de aceptación/consentimiento.
    info["aceptacion"] = bool(texto_es_aceptacion(texto))
    filas_resultado = sum(t["n_filas"] for t in info["tablas"]
                          if _tabla_es_resultado(t))
    sin_registros = _hay_marcador(texto, _MARCA_SIN_REGISTROS)
    info["tiene_datos"] = (filas_resultado > 0) and not sin_registros
    info["filas_resultado"] = filas_resultado
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
                        info["estado"] = _estado_desde(info)
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


# ═══════════════════════════════════════════════════════════════════════
# FASE 2 (modo decl-diag2): navega por CÓDIGO de menú (robusto), envía un rango
# corto en la Consulta DDJJ y pagos y captura la ESTRUCTURA de la constancia 0621.
# Sigue sin escribir en BD y sin descargar PDFs (evita las rutas window.open de
# comprobanteNSIR). Códigos confirmados en el volcado de fase 1.
# ═══════════════════════════════════════════════════════════════════════
MESES_FASE2 = int(os.getenv("DECL_DIAG2_MESES", "5"))   # <6 (límite SUNAT: 182 d)

TARGETS_FASE2 = [
    {"slug": "01_consulta_general", "code": "12.8.1.1.2",
     "url_frag": "consultaS01Alias",
     "desc": "Consulta general — Detalle de declaraciones y pagos "
             "(/cl-ti-itconsdecdetinf/consultaS01Alias?accion=consgeneral)"},
    {"slug": "02_djj_pagos", "code": "12.1.1.1.4",
     "url_frag": "cdpS01Alias",
     "desc": "Consulta de declaraciones juradas y pagos "
             "(/cl-ti-itdeclpagcon-mepeco/cdpS01Alias) + rango + constancia 0621"},
    {"slug": "05_detr_recaudacion", "code": "12.4.1.1.3",
     "url_frag": "imputS01Alias",
     "desc": "Detracciones — Ingreso como recaudación "
             "(/cl-ti-itconsimput/imputS01Alias)"},
    {"slug": "06_detr_npd", "code": "12.4.1.1.6",
     "url_frag": "itnumpagdetr",
     "desc": "Detracciones — Consulta de NPD "
             "(/cl-ti-itnumpagdetr/consultaIndividual.htm)"},
]


def _ir_por_codigo(page, code: str, desc: str) -> bool:
    """Abre la operación SOL por su CÓDIGO de menú, como hace la propia web
    (ejecuta('MenuInternet.htm?action=iconExecute&code=…')). Fallback: fija el
    src del iframe de aplicación."""
    url = f"MenuInternet.htm?action=iconExecute&code={code}"
    try:
        page.evaluate("(a) => ejecuta(a.u, false, a.t, '', a.c)",
                      {"u": url, "t": desc[:60], "c": code})
        _pausa()
        return True
    except Exception:
        try:
            page.evaluate("(u) => { var f = document.getElementById("
                          "'iframeApplication'); if (f) { f.src = u; } }", url)
            _pausa()
            return True
        except Exception:
            return False


def _frame_con(page, selector: str):
    """Scope (frame o página) que contiene `selector`, o None."""
    for sc in _scopes(page):
        try:
            if sc.query_selector(selector):
                return sc
        except Exception:
            pass
    return None


def _esperar_frame_url(page, frag: str, timeout_s: float = 20.0,
                       intervalo: float = 0.5, abortar_si=None):
    """Espera a que el frame de la OPERACIÓN (iframe ifrVCE) haya navegado: busca
    un frame cuya URL contenga `frag` (p.ej. 'cdpS01Alias'). Evita volcar el
    formulario antes de que cargue (quedaría con 0 tablas).

    `abortar_si(page)->bool`: si devuelve True (p.ej. ya se ve 'acceso no
    autorizado'), corta la espera y devuelve None sin agotar el timeout."""
    frag = (frag or "").lower()
    if not frag:
        return None
    fin = time.monotonic() + timeout_s
    while True:
        for fr in page.frames:
            try:
                if frag in (fr.url or "").lower():
                    return fr
            except Exception:
                pass
        if abortar_si is not None:
            try:
                if abortar_si(page):
                    return None
            except Exception:
                pass
        if time.monotonic() >= fin:
            return None
        try:
            time.sleep(intervalo)
        except Exception:
            return None


def _esperar_frame_con(page, selector: str, timeout_s: float = 30.0,
                       intervalo: float = 0.5):
    """Como `_frame_con` pero ESPERA hasta `timeout_s` a que el selector aparezca
    en ALGÚN frame. Las operaciones SOL cargan async dentro del iframe `ifrVCE`
    ('Cargando aplicación…'); sin esperar, el form puede no existir todavía."""
    fin = time.monotonic() + timeout_s
    while time.monotonic() < fin:
        sc = _frame_con(page, selector)
        if sc:
            return sc
        try:
            time.sleep(intervalo)
        except Exception:
            break
    return _frame_con(page, selector)


def _enviar_rango_02(page, cfg: SunatConfig, meses: int, reabrir=None):
    """En la Consulta DDJJ y pagos rellena un rango de `meses` (DD/MM/YYYY, bajo el
    límite de 6 meses) y pulsa Buscar. Devuelve `(scope, aviso)`: aviso = motivo si
    el formulario no cargó (iframe async), None si OK. `reabrir` = callable para 1
    reintento."""
    fr = _esperar_frame_con(page, "form[name='formFechas']", 30)
    if not fr and reabrir:
        log("    02: formulario no apareció; reintento reabriendo la opción…", "WARN")
        try:
            reabrir()
        except Exception:
            pass
        fr = _esperar_frame_con(page, "form[name='formFechas']", 30)
    if not fr:
        log("    02: el formulario de fechas no cargó tras esperar + reintento.",
            "WARN")
        return None, ("El formulario de la Consulta de DDJJ y pagos no cargó "
                      "(iframe ifrVCE) tras esperar 30s y 1 reintento.")
    hoy = date.today()
    desde = hoy - timedelta(days=int(30.4 * meses))
    try:
        fr.fill("input[name='fechaDesde']", desde.strftime("%d/%m/%Y"))
        fr.fill("input[name='fechaHasta']", hoy.strftime("%d/%m/%Y"))
        fr.evaluate("consulta(document.formFechas)")   # misma vía que Buscar
        log(f"    02: enviado rango {desde:%d/%m/%Y}–{hoy:%d/%m/%Y}.", "INFO")
    except Exception as e:
        log(f"    02: no pude enviar el rango: {_sanea(str(e), cfg)}", "WARN")
        return fr, None
    _pausa()
    return (_frame_con(page, "form[name='formComprobante']") or fr), None


def _sel(fr, name: str, *vals) -> bool:
    """Intenta seleccionar la 1ª opción válida de un <select name=..>."""
    for v in vals:
        try:
            fr.select_option(f"select[name='{name}']", value=v)
            return True
        except Exception:
            pass
    return False


def _periodo_a_fecha(periodo: str | None):
    """AAAAMM → date(primer día del mes). None si no es válido."""
    if not periodo:
        return None
    m = re.fullmatch(r"(\d{4})(\d{2})", str(periodo).strip())
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12):
        return None
    try:
        return date(y, mo, 1)
    except ValueError:
        return None


def _sumar_meses(d: date, n: int) -> date:
    """Suma n meses a una fecha (día 1, seguro)."""
    total = d.month - 1 + n
    return date(d.year + total // 12, total % 12 + 1, 1)


def _hay_resultados_01(page) -> bool:
    """¿La 01 avanzó a la grilla de resultados? La delata la tabla de detalle."""
    for sc in _scopes(page):
        try:
            if sc.query_selector("#tblDetalleDeclPagos"):
                return True
        except Exception:
            pass
    return False


# verDetalleTributos('Detalle de Tributos Pagados', ruc, codForm, tipo, numOrd)
_RE_VERDET = re.compile(
    r"verDetalleTributos\(\s*'([^']*)'\s*,\s*'(\d+)'\s*,\s*'(\d+)'\s*,"
    r"\s*'(\d+)'\s*,\s*'(\d+)'")


def _capturar_detalle_tributos(page, carpeta: Path, cfg: SunatConfig) -> dict:
    """Abre el 'Detalle de Tributos Pagados' de la 1ª boleta de la grilla 01
    (verDetalleTributos → /cl-ti-itveripagformul/vpS01Alias). Es HTML (NO PDF):
    ahí está el tributo fiscal real que la 01 no trae inline (la columna
    'Codigo-Tributo' de una 1662 es un código de boleta, p.ej. 0018). Vuelca la
    estructura; NO descarga nada."""
    base = {"slug": "04_detalle_tributos",
            "pantalla": "04 Detalle de Tributos Pagados (1662)"}
    sc = _frame_con(page, "a[href*='verDetalleTributos(']")
    if not sc:
        return {**base, "estado": "sin_datos",
                "detalle": "la grilla 01 no tiene boleta con enlace a Detalle"}
    try:
        a = sc.query_selector("a[href*='verDetalleTributos(']")
        blob = (a.get_attribute("href") or "") + (a.get_attribute("onclick") or "")
    except Exception:
        blob = ""
    m = _RE_VERDET.search(blob)
    if not m:
        return {**base, "estado": "no_se_pudo_leer",
                "detalle": "no pude leer los args de verDetalleTributos"}
    titulo, ruc, codform, tipo, norden = m.groups()
    try:
        sc.evaluate("(a) => verDetalleTributos(a.t, a.r, a.f, a.tp, a.n)",
                    {"t": titulo, "r": ruc, "f": codform, "tp": tipo, "n": norden})
    except Exception as e:
        return {**base, "estado": "no_se_pudo_leer",
                "detalle": _sanea(f"{type(e).__name__}: {e}", cfg)}
    _pausa()
    _esperar_frame_url(page, "vpS01Alias", 20) or \
        _esperar_frame_url(page, "itveripagformul", 20)
    info = _volcar(page, carpeta, "04_detalle_tributos", cfg)
    info["estado"] = _estado_desde(info)
    info["pantalla"] = f"04 Detalle de Tributos Pagados (F{codform} orden {norden})"
    return info


def _enviar_filtro_01(page, cfg: SunatConfig, meses: int, periodo_desde=None,
                      reabrir=None):
    """Consulta general: filtra por PERÍODO (mes/año desde-hasta) y busca. Trae la
    grilla período×tributo×importe pagado.

    La 01 valida en JS que el rango NO supere **6 meses** (`validarFormCGeneral`;
    si no, `alert('El rango de consulta es mayor a 6 meses...')` y NO envía). Por
    eso con `periodo_desde` el 'hasta' = desde + 5 meses (bloque de 6), sin pasar
    de hoy. Sin `periodo_desde`, retrocede `meses` desde hoy (≤6).

    Devuelve `(scope, aviso)`: `aviso` = texto del alert de SUNAT si lo hubo
    (rango inválido) o el motivo si el form no cargó; None si todo OK.

    `reabrir` (callable sin args): reabre la opción de menú para 1 reintento si el
    formulario no apareció (el iframe `ifrVCE` carga async)."""
    fr = _esperar_frame_con(page, "form[name='consultageneral']", 30)
    if not fr and reabrir:
        log("    01: formulario no apareció; reintento reabriendo la opción…", "WARN")
        try:
            reabrir()
        except Exception:
            pass
        fr = _esperar_frame_con(page, "form[name='consultageneral']", 30)
    if not fr:
        log("    01: el formulario consultageneral no cargó tras esperar + reintento.",
            "WARN")
        return None, ("El formulario de la Consulta general no cargó (iframe "
                      "ifrVCE) tras esperar 30s y 1 reintento.")
    hoy = date.today()
    hoy_m1 = date(hoy.year, hoy.month, 1)
    desde_f = _periodo_a_fecha(periodo_desde)
    if desde_f:
        desde = desde_f
        tope = _sumar_meses(desde, 5)           # bloque de 6 meses (desde..desde+5)
        hasta = tope if tope <= hoy_m1 else hoy_m1
    else:
        desde = hoy - timedelta(days=int(30.4 * meses))
        hasta = hoy

    # Captura del alert de SUNAT (rango inválido). Con un listener, Playwright ya
    # no auto-descarta: el handler debe cerrarlo.
    avisos: list[str] = []

    def _on_dialog(d):
        try:
            avisos.append(d.message)
        finally:
            try:
                d.dismiss()
            except Exception:
                pass

    page.on("dialog", _on_dialog)
    try:
        _sel(fr, "adesde", str(desde.year))
        _sel(fr, "mdesde", f"{desde.month:02d}", str(desde.month))
        _sel(fr, "ahasta", str(hasta.year))
        _sel(fr, "mhasta", f"{hasta.month:02d}", str(hasta.month))
        fr.evaluate("if (typeof validarFormCGeneral==='function'){"
                    "validarFormCGeneral();} else {"
                    "document.consultageneral.submit();}")
        log(f"    01: enviado período {desde:%m/%Y}–{hasta:%m/%Y}.", "INFO")
    except Exception as e:
        log(f"    01: no pude enviar el filtro: {_sanea(str(e), cfg)}", "WARN")
    finally:
        _pausa()
        try:
            page.remove_listener("dialog", _on_dialog)
        except Exception:
            pass
    aviso = _sanea(avisos[0], cfg) if avisos else None
    scope = _frame_con(page, "form[name='consultageneral']") or fr
    return scope, aviso


def _resumen_constancias(scope, cfg: SunatConfig) -> dict:
    """La constancia (0621/1662) se abre con `constanciaNP(num_ord,'cod_for')` →
    `validar_constanciaNP` (XML) → window.open de un PDF `obtener_constanciaNP`.
    En DIAG NO se descarga: solo se reporta qué hay (cuántos enlaces y por
    formulario) y el patrón de URL del PDF, para decidir si se parsea el PDF."""
    hrefs = []
    try:
        for a in scope.query_selector_all(
                "a[href*='constanciaNP('], a[onclick*='constanciaNP(']"):
            hrefs.append((a.get_attribute("href") or "")
                         + (a.get_attribute("onclick") or ""))
    except Exception:
        pass
    cods = re.findall(r"constanciaNP\(\s*\d+\s*,\s*'([0-9]{3,4})'", " ".join(hrefs))
    por_form = {}
    for c in cods:
        por_form[c] = por_form.get(c, 0) + 1
    return {"slug": "03_constancia", "pantalla": "03 Constancia (0621/1662)",
            "estado": ("con_datos" if hrefs else "sin_datos"),
            "n_enlaces": len(hrefs), "por_formulario": por_form,
            "mecanismo": "constanciaNP(num_ord, cod_for) → POST accion="
                         "validar_constanciaNP → window.open accion="
                         "obtener_constanciaNP&cod_ecm=… (PDF)",
            "pdf_url_patron": _patron_url(
                "/cl-ti-itdeclpagcon-mepeco/cdpS01Alias?accion=obtener_"
                "constanciaNP&cod_ecm=", cfg),
            "detalle": "constancia = PDF; en diag NO se descarga"}


def diag_declaraciones_fase2(cfg: SunatConfig, meses: int | None = None,
                             periodo_desde: str | None = None) -> dict:
    """Fase 2: navega por código, envía rango en la 02 y captura la constancia
    0621. NO escribe en BD, NO descarga PDFs.

    `periodo_desde` (AAAAMM, opcional): fuerza el 'desde' de la pantalla 01 a ese
    período exacto (p.ej. `202605` para cerrar el importe/banco de un 1662 viejo)."""
    meses = MESES_FASE2 if meses is None else meses
    res = {"exito": False, "pantallas": [], "carpeta": None, "red": [], "fase": 2}
    ts = datetime.now(TZ_LIMA).strftime("%Y%m%d_%H%M%S")
    carpeta = DEST / f"decl_diag2_{cfg.ruc}_{ts}"
    carpeta.mkdir(parents=True, exist_ok=True)
    res["carpeta"] = str(carpeta)
    red: list = []
    cur = {"t": "00_login"}

    def _on_response(resp):
        try:
            if "sunat" not in resp.url.lower():
                return
            red.append({"pantalla": cur["t"], "metodo": resp.request.method,
                        "url": _patron_url(resp.url, cfg), "status": resp.status,
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
                log("  decl-diag2: login SOL rechazado.", "ERROR")
                return res
            log("  decl-diag2: login OK. Navego por código de menú...", "OK")
            for tg in TARGETS_FASE2:
                cur["t"] = tg["slug"]
                ahora = ts_lima()
                log(f"  decl-diag2: {tg['slug']} (code {tg['code']})...", "INFO")
                try:
                    if not _ir_por_codigo(page, tg["code"], tg["desc"]):
                        info = {"slug": tg["slug"], "estado": "no_se_pudo_leer",
                                "detalle": "no se pudo invocar el código", "at": ahora}
                    else:
                        # Esperar a que el iframe de la operación cargue (async)
                        # antes de volcar la estructura, si no sale con 0 tablas.
                        # Si ya se ve 'acceso no autorizado' (usuario secundario sin
                        # perfil), abortar la espera: no gastar el timeout en vano.
                        _esperar_frame_url(page, tg.get("url_frag", ""), 20,
                                           abortar_si=_sin_acceso_visible)
                        info = _volcar(page, carpeta, tg["slug"], cfg)
                        info["estado"] = _estado_desde(info)
                        info["at"] = ahora
                except Exception as e:
                    info = {"slug": tg["slug"], "estado": "no_se_pudo_leer",
                            "detalle": _sanea(f"{type(e).__name__}: {e}", cfg),
                            "at": ahora}
                info["code"] = tg["code"]
                info["pantalla"] = tg["desc"]
                res["pantallas"].append(info)

                # Pantalla 01 (Consulta general): enviar filtro de período y volcar
                # la grilla período×tributo×importe pagado.
                # Si el usuario NO tiene acceso a esta opción (secundario sin perfil),
                # no tiene sentido enviar el filtro/rango: se salta directo a la
                # siguiente pantalla. Así no se pierden ~30-60 s por sub-paso.
                if info["estado"] in ("sin_acceso", "pantalla_de_aceptacion"):
                    if info["estado"] == "pantalla_de_aceptacion":
                        log(f"  decl-diag2: {tg['slug']} es pantalla de aceptación "
                            f"— NO se toca nada, se salta.", "WARN")
                    _pausa()
                    continue

                if tg["slug"] == "01_consulta_general" and info["estado"] != "no_se_pudo_leer":
                    try:
                        cur["t"] = "01_resultados"
                        _sc01, aviso01 = _enviar_filtro_01(
                            page, cfg, meses, periodo_desde,
                            reabrir=lambda: _ir_por_codigo(
                                page, tg["code"], tg["desc"]))
                        r = _volcar(page, carpeta, "01_consulta_general_resultados", cfg)
                        if r.get("sin_acceso"):
                            # El filtro reveló falta de perfil: NO es error_filtro.
                            r["estado"] = "sin_acceso"
                        elif aviso01 or not _hay_resultados_01(page):
                            # El formulario NO avanzó a resultados: es un error de
                            # filtro/navegación, NO "sin datos" (confundirlos haría
                            # ver un fallo como "no hay declaraciones" → grave).
                            r["estado"] = "error_filtro"
                            r["aviso"] = aviso01 or (
                                "La Consulta general no avanzó a resultados "
                                "(revisa el rango ≤ 6 meses).")
                        else:
                            r["estado"] = _estado_desde(r)
                        r["pantalla"] = (f"01 resultados (desde {periodo_desde})"
                                         if periodo_desde
                                         else f"01 resultados (período, {meses} meses)")
                        res["pantallas"].append(r)
                        # 04 Detalle de Tributos Pagados (de la 1ª boleta 1662):
                        # el tributo fiscal real, en HTML (NO PDF).
                        if r.get("estado") == "con_datos":
                            cur["t"] = "04_detalle"
                            res["pantallas"].append(
                                _capturar_detalle_tributos(page, carpeta, cfg))
                    except Exception as e:
                        res["pantallas"].append(
                            {"slug": "01_consulta_general_resultados",
                             "estado": "no_se_pudo_leer", "pantalla": "01 resultados",
                             "detalle": _sanea(f"{type(e).__name__}: {e}", cfg)})

                # Pantalla 02: enviar rango, volcar resultados y resumir constancias.
                if tg["slug"] == "02_djj_pagos" and info["estado"] != "no_se_pudo_leer":
                    try:
                        cur["t"] = "02_resultados"
                        sc, aviso02 = _enviar_rango_02(
                            page, cfg, meses,
                            reabrir=lambda: _ir_por_codigo(
                                page, tg["code"], tg["desc"]))
                        r = _volcar(page, carpeta, "02_djj_pagos_resultados", cfg)
                        if r.get("sin_acceso"):
                            r["estado"] = "sin_acceso"
                        elif aviso02:
                            # El form no cargó: error de navegación, NO "sin datos".
                            r["estado"] = "error_filtro"
                            r["aviso"] = aviso02
                        else:
                            r["estado"] = _estado_desde(r)
                        r["pantalla"] = f"02 resultados (rango {meses} meses)"
                        res["pantallas"].append(r)
                        cur["t"] = "03_constancia"
                        res["pantallas"].append(
                            _resumen_constancias(sc or page, cfg))
                    except Exception as e:
                        res["pantallas"].append(
                            {"slug": "03_constancia",
                             "estado": "no_se_pudo_leer",
                             "detalle": _sanea(f"{type(e).__name__}: {e}", cfg),
                             "pantalla": "03 Constancia (0621/1662)"})
                _pausa()
            res["red"] = red
            # exito = el diag CORRIÓ (login + navegación), aunque alguna pantalla
            # falle. Las pantallas con error se listan aparte para que el resumen
            # NO cante "OK" si hubo error_filtro/no_se_pudo_leer.
            res["exito"] = True
            # SIN ACCESO (el contador debe asignar el perfil SOL) ≠ CON ERRORES
            # (falla nuestra o de SUNAT). Se separan para no asustar al contador con
            # un "error" cuando en realidad es un permiso que él mismo debe habilitar.
            res["pantallas_error"] = [
                {"slug": p.get("slug"), "estado": p.get("estado"),
                 "aviso": p.get("aviso") or p.get("detalle")}
                for p in res["pantallas"]
                if p.get("estado") in ("error_filtro", "no_se_pudo_leer")]
            res["con_errores"] = bool(res["pantallas_error"])
            res["pantallas_sin_acceso"] = sorted({
                p.get("pantalla") or p.get("slug")
                for p in res["pantallas"]
                if p.get("estado") == "sin_acceso"})
            res["sin_acceso"] = bool(res["pantallas_sin_acceso"])
        finally:
            try:
                ctx.close(); nav.close()
            except Exception:
                pass
    return res
