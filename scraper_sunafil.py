"""
scraper_sunafil.py — alerta.pe (zAlerta-SUNAFIL-1)
═══════════════════════════════════════════════════════════════════════
Lector del buzón de la CASILLA ELECTRÓNICA de SUNAFIL, reutilizando el login
Clave SOL y el pipeline (ingesta/modelo/alertas) de SUNAT. La entrada real es
/si.inbox/Login/SUNAT, que redirige al MISMO login SOL; se llena con los mismos
selectores estables del scraper SUNAT (no se duplica la lógica de campos).

A diferencia de SUNAT (API JSON del visor), SUNAFIL es una casilla DOM (tablas,
modales, paginación) → aquí se navega con Playwright y se parsea la TABLA por el
NOMBRE de sus columnas (robusto a IDs que no conocemos hasta el diag real).

IMPORTANTE (honesto): los selectores/urls exactos de SUNAFIL solo se confirman con
sesión real en el worker. Por eso el modo `diag=True` VUELCA el DOM (html+captura)
en cada paso, para afinar los selectores antes de confiar la extracción. El parseo
por encabezado ya intenta ser correcto sin IDs; el diag confirma columnas y flujo.

REGLA PERMANENTE — SOLO LECTURA (efecto legal cero):
  · alerta.pe NUNCA acusa recibo ni marca "leído" en SUNAFIL/SUNAT. Solo LEE la
    lista (que ya muestra leído/no leído, expediente, plazo) para DETECTAR nuevas.
  · El estado "no leído" se lee SOLO para dedup/alerta; jamás para cambiarlo.
  · NO se pulsa 'Aceptar'/'Continuar'/'Guardar' (podrían registrar contacto).
  · La descarga del PDF (2º paso, tras el diag) debe hacerse por una vía que NO
    dispare el acuse automático; si abrir el documento acusa, se DIFIERE al usuario
    (o se avisa) — el acuse oficial SIEMPRE lo hace el usuario en SUNAT/SUNAFIL.
  · El "revisado" para organizar la bandeja es estado INTERNO de alerta.pe
    (leida / LecturaNotificacion, por persona), sin tocar SUNAFIL.
"""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Reuso de utilidades del scraper SUNAT (no se duplica).
from scraper_sunat_playwgth import (
    SunatConfig, log, ahora_lima, _evidencia,
)

# Entrada REAL al buzón (descubierta en el diag): el endpoint que inicia el login
# SUNAT de la casilla y aterriza en el inbox `si.inbox`. La raíz del dominio es una
# página estática de logo que deriva a la web pública — NO es el buzón.
URL_CASILLA_SUNAFIL = "https://casillaelectronica.sunafil.gob.pe/si.inbox/Login/SUNAT"

# Selectores del formulario SOL (los mismos que usa el scraper SUNAT; estables).
_SEL_RUC = ["#txtRuc", "input[name='txtRuc']", "#ruc", "input[name='ruc']"]
_SEL_USR = ["#txtUsuario", "input[name='txtUsuario']", "#usuario", "input[name='usuario']"]
_SEL_CLA = ["#txtContrasena", "input[name='txtContrasena']", "#clave",
            "input[name='clave']", "input[type='password']"]
_SEL_BTN = ["#btnAceptar", "button[type='submit']", "input[type='submit']", "#submit"]


def _fill_first(page, selectores, valor) -> bool:
    for s in selectores:
        try:
            el = page.query_selector(s)
            if el and el.is_visible():
                el.fill(valor)
                return True
        except Exception:
            continue
    return False


def _login_casilla(page, cfg, diag: bool) -> bool:
    """Entra al buzón SUNAFIL por /si.inbox/Login/SUNAT. Ese endpoint redirige al
    login SOL; si aparece el formulario, se llena (mismos selectores que SUNAT) y
    se envía. Luego se espera a estar DENTRO del inbox (URL con 'si.inbox')."""
    page.goto(URL_CASILLA_SUNAFIL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2500)
    if diag:
        log(f"   [sunafil-diag] tras entrar a si.inbox/Login/SUNAT: {page.url}", "INFO")
    # ¿Nos mandó al formulario SOL? (hay campo de clave visible) → llenar.
    try:
        page.wait_for_selector(", ".join(_SEL_CLA), timeout=20_000, state="visible")
        _fill_first(page, _SEL_RUC, cfg.ruc)
        _fill_first(page, _SEL_USR, cfg.usuario_sol)
        _fill_first(page, _SEL_CLA, cfg.clave_sol)
        for s in _SEL_BTN:
            try:
                b = page.query_selector(s)
                if b and b.is_visible():
                    b.click()
                    break
            except Exception:
                continue
        page.wait_for_timeout(3500)
        if diag:
            log(f"   [sunafil-diag] tras login SOL: {page.url}", "INFO")
    except PWTimeout:
        pass   # sin formulario → probablemente ya había sesión y entró directo
    # Esperar a estar dentro del inbox.
    try:
        page.wait_for_url("**si.inbox**", timeout=30_000)
    except Exception:
        pass
    page.wait_for_timeout(2500)   # el inbox es una app JS: dar tiempo a que pinte
    return "si.inbox" in page.url

# ── Categorías del buzón + navegación ──────────────────────────────────────
# Endpoint del dashboard del empleador (donde están los enlaces "Notificaciones
# de <categoría>"). El contexto EMPLEADOR ya queda fijado por el OAuth de
# /si.inbox/Login/SUNAT (originalUrl=…/Login/Empresa); no hay clic aparte.
URL_INICIO_EMPLEADOR = "https://casillaelectronica.sunafil.gob.pe/si.inbox/Inicio/Empleador"

# Nombre visible de cada categoría del buzón. El token de `_CAT_KW` es SOLO para
# LOCALIZAR el enlace del dashboard y hacer clic (navegación). La FORMA de la
# tabla se detecta SIEMPRE por CABECERA (ver _detectar_forma), NUNCA por el nombre:
# las categorías hoy vacías podrían traer mañana una variante de columnas distinta.
CATEGORIAS_SUNAFIL = [
    "Acciones Previas", "Fiscalización Laboral", "Cobranza Ordinaria",
    "Seguridad y Salud en el Trabajo", "Alertas de Formalización", "Orientaciones",
]
_CAT_KW = {
    "Acciones Previas": "acciones previas",
    "Fiscalización Laboral": "fiscalizacion",
    "Cobranza Ordinaria": "cobranza",
    "Seguridad y Salud en el Trabajo": "seguridad",
    "Alertas de Formalización": "formaliza",
    "Orientaciones": "orientacion",
}


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    return "".join(c for c in __import__("unicodedata").normalize("NFD", s)
                   if __import__("unicodedata").category(c) != "Mn")


_RE_FECHA = re.compile(r"\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?")


def _fecha_limpia(txt: str) -> str:
    """Extrae la fecha (dd/mm/aaaa [hh:mm[:ss]]) de una celda que puede traer
    sufijos/otra línea. Acota el valor (la col fecha_envio_sunat es VARCHAR(30))."""
    m = _RE_FECHA.search(txt or "")
    return m.group(0) if m else (txt or "").strip()[:30]


def _ir_categoria(page, nombre: str, diag: bool) -> bool:
    """Vuelve al dashboard y hace clic en el enlace 'Notificaciones de <categoría>'.
    El token solo LOCALIZA el enlace; no decide la forma de la tabla. El clic se
    dispara por JS (los commandLink JSF no siempre son 'accionables' para Playwright)."""
    kw = _CAT_KW.get(nombre, _norm(nombre).split()[0])
    page.goto(URL_INICIO_EMPLEADOR, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    _cerrar_modales(page, diag)
    objetivos = []
    for a in page.query_selector_all("a.commandLink, a[role='menuitem'], a"):
        try:
            txt = (a.inner_text() or "").strip()
        except Exception:
            continue
        if kw in _norm(txt):
            objetivos.append((a, txt))
    # preferir el enlace del RESUMEN ('Notificaciones de …') al del menú lateral
    objetivos.sort(key=lambda x: 0 if "notificacion" in _norm(x[1]) else 1)
    for a, _txt in objetivos:
        try:
            a.evaluate("el => el.click()")
            return True
        except Exception:
            continue
    return False


def _esperar_lista(page, timeout_ms: int = 12_000) -> bool:
    """Espera (sin tiempo fijo) a que pinte un datatable de notificaciones: una
    <table> con ≥2 cabeceras reales que NO sea la tabla-resumen del dashboard.
    True si apareció; False si la categoría está vacía o no cargó."""
    try:
        page.wait_for_function(
            """() => {
                const ts=[...document.querySelectorAll('table')];
                return ts.some(t=>{
                  const hs=[...t.querySelectorAll('thead th, tr th')]
                    .map(x=>(x.innerText||'').trim().toLowerCase());
                  if(hs.includes('cantidad') && hs.includes('descripción')) return false;
                  return hs.filter(h=>h).length >= 2;
                });
            }""", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _abrir_categoria(page, nombre: str, diag: bool) -> bool:
    """Navega a la lista de la categoría, con 1 REINTENTO si el datatable no pinta
    a tiempo (flakiness JSF). True si quedó una tabla de notificaciones cargada."""
    for _intento in (1, 2):
        if not _ir_categoria(page, nombre, diag):
            continue                                  # enlace no hallado → reintenta
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        if _esperar_lista(page) or _tabla_notificaciones(page) is not None:
            return True
        # navegó pero no pintó la tabla → reintenta la navegación una vez más
    return _tabla_notificaciones(page) is not None


def _tabla_notificaciones(page):
    """La <table> de notificaciones: la que tiene CABECERAS REALES (≥2), excluyendo
    la tabla-resumen del dashboard ('Descripción | Cantidad') y las tablas de layout
    sin thead. Devuelve None si no hay ninguna (categoría vacía o no cargó)."""
    cand = []
    for t in page.query_selector_all("table"):
        heads = [_norm(th.inner_text()) for th in t.query_selector_all("thead th, tr th")]
        if "cantidad" in heads and "descripcion" in heads:
            continue
        reales = [h for h in heads if h]
        if len(reales) >= 2:                       # tiene cabeceras → es un datatable
            filas = len(t.query_selector_all("tbody tr"))
            cand.append((len(reales), filas, t))
    if not cand:
        return None
    cand.sort(key=lambda x: (x[0], x[1]), reverse=True)   # más cabeceras, luego más filas
    return cand[0][2]


def _headers(tabla) -> list:
    return [th.inner_text().strip() for th in tabla.query_selector_all("thead th, tr th")]


# Marca leído/no-leído UNIVERSAL: el ícono de estado de la fila. Es la fuente
# PRIMARIA en TODAS las formas. `icon_novisto.png`=no leído, `icon_visto.png`=leído.
# (La columna 'Estado' de texto, solo en Forma B, es respaldo secundario.)
def _leido_por_icono(tr):
    """True=leído (icon_visto) · False=no leído (icon_novisto) · None=sin ícono."""
    for img in tr.query_selector_all("img"):
        src = (img.get_attribute("src") or "").lower()
        if "icon_novisto" in src:
            return False
        if "icon_visto" in src:
            return True
    return None


def _has(heads_norm: list, token: str) -> bool:
    return any(token in h for h in heads_norm)


# ── FORMAS de tabla, detectadas POR CABECERA (nunca por el nombre de categoría) ──
# Cada spec: cómo se detecta (firma de cabecera), cómo mapea columnas→claves, cómo
# forma el id ('expediente' visible | 'hash' de cat+fecha+asunto), y si su mapeo YA
# fue VALIDADO con filas reales. Las columnas ausentes en una forma quedan NULAS
# (no se inventan). Las NO validadas se usan, pero emiten una señal de confirmación
# la primera vez que traen una fila real (para cotejar el formato de los valores).
_FORMAS = [
    {   # A — Trámite/Expediente (Acciones Previas; y Fiscalización/Cobranza/Seguridad
        #     comparten esta familia cuando traen 'Registro'+'Plazo'). VALIDADO.
        "clave": "A", "nombre": "Trámite/Expediente", "validado": True, "id": "expediente",
        "coincide": lambda h: _has(h, "registro") and _has(h, "plazo"),
        "mapa": [("tipo de requerimiento", "asunto"), ("registro", "expediente"),
                 ("fecha de deposito", "fecha_envio"), ("fecha acuse de recibo", "fecha_acuse"),
                 ("fecha de notificacion", "fecha_notificacion"), ("plazo", "plazo_dias"),
                 ("fecha limite de presentacion", "fecha_limite")],
    },
    {   # B — Aviso (Orientaciones, Seguridad y Salud). VALIDADO.
        "clave": "B", "nombre": "Aviso", "validado": True, "id": "hash",
        "coincide": lambda h: _has(h, "asunto") and _has(h, "estado"),
        "mapa": [("fecha de deposito", "fecha_envio"), ("asunto", "asunto"),
                 ("estado", "estado_txt")],
    },
    {   # FISC — Fiscalización Laboral. NO VALIDADO (sin filas reales aún).
        #   Sin fecha ni plazo en la lista → esos campos quedan nulos.
        "clave": "FISC", "nombre": "Fiscalización Laboral", "validado": False, "id": "expediente",
        "coincide": lambda h: _has(h, "orden de inspeccion"),
        "mapa": [("orden de inspeccion", "expediente"), ("intendencia", "intendencia"),
                 ("estado", "estado_txt"), ("ver documentos", "_accion")],
    },
    {   # COBR — Cobranza Ordinaria. NO VALIDADO. Sin fecha/estado/plazo → nulos.
        "clave": "COBR", "nombre": "Cobranza Ordinaria", "validado": False, "id": "expediente",
        "coincide": lambda h: _has(h, "expediente sancionador"),
        "mapa": [("expediente sancionador", "expediente"), ("intendencia", "intendencia"),
                 ("ver documentos", "_accion")],
    },
    {   # AFOR — Alertas de Formalización. NO VALIDADO. Sin expediente → id por hash.
        #   'Registrar Incorporados' es acción de ESCRITURA → SOLO se mapea como
        #   acción; el parser JAMÁS la pulsa (solo lectura).
        "clave": "AFOR", "nombre": "Alertas de Formalización", "validado": False, "id": "hash",
        "coincide": lambda h: _has(h, "trabajadores incorporados") or _has(h, "registrar incorporados"),
        "mapa": [("fecha de deposito", "fecha_envio"), ("fecha de notificacion", "fecha_notificacion"),
                 ("fecha limite de respuesta", "fecha_limite"), ("asunto", "asunto"),
                 ("trabajadores incorporados", "_info"), ("opcion", "_accion"),
                 ("registrar incorporados", "_accion_escritura")],
    },
]


def _detectar_forma(heads_norm: list):
    """Devuelve el spec de forma que coincide con la cabecera, o None (desconocida)."""
    for spec in _FORMAS:
        if spec["coincide"](heads_norm):
            return spec
    return None


def _col_idx(heads_norm: list, mapa: list) -> dict:
    idx = {}
    for i, h in enumerate(heads_norm):
        for token, clave in mapa:
            if clave not in idx and (h == token or token in h):
                idx[clave] = i
    return idx


def _cel(tds, idx) -> str:
    return tds[idx].inner_text().strip() if (idx is not None and idx < len(tds)) else ""


def _clasif_valor(v: str) -> str:
    """Tipo del valor por su FORMATO (para la señal de confirmación; nunca el valor)."""
    v = (v or "").strip()
    if not v:
        return "vacío"
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}(?::\d{2})?", v):
        return "fecha+hora"
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", v):
        return "fecha"
    if re.search(r"\d+\s*d[ií]a", v, re.I):
        return "plazo(N días)"
    if re.fullmatch(r"\d+", v):
        return "número"
    if re.search(r"no\s*le[ií]d", v, re.I):
        return "estado=No leído"
    if re.search(r"\ble[ií]d[oa]\b", v, re.I):
        return "estado=Leído"
    if re.search(r"SUNAFIL", v, re.I):
        return "expediente(SUNAFIL)"
    return "texto"


_CAMPOS_FECHA = ("fecha_envio", "fecha_notificacion", "fecha_acuse", "fecha_limite")


def _parsear_pagina(tabla, spec: dict, categoria: str, heads_norm: list,
                    senales: list | None = None) -> list:
    """Parsea las filas de la página ACTUAL según el spec de forma. SOLO LEE:
    reporta leído/no-leído (por ícono); jamás lo cambia ni acusa recibo. Para las
    formas NO validadas, chequea el formato y, en la primera fila real, apunta una
    señal de confirmación en `senales` (no ingiere valores que no calzan)."""
    import hashlib
    items = []
    col = _col_idx(heads_norm, spec["mapa"])
    claves_mapa = {c for _, c in spec["mapa"]}
    validado = spec["validado"]
    for tr in tabla.query_selector_all("tbody tr"):
        tds = tr.query_selector_all("td")
        # La fila placeholder de tabla vacía ('No hay registros') es UNA sola celda
        # con colspan → una fila real tiene varias columnas. Evita ingerir el vacío.
        if len(tds) < 2:
            continue
        asunto = _cel(tds, col.get("asunto")) or categoria
        # Fechas: en formas VALIDADAS, _fecha_limpia (tolerante); en NO validadas,
        # estricta (solo acepta fecha reconocible; si no calza → nulo + aviso).
        avisos = []

        def _fecha(clave):
            raw = _cel(tds, col.get(clave)) if clave in claves_mapa else ""
            if not raw:
                return ""
            m = _RE_FECHA.search(raw)
            if m:
                return m.group(0)
            if not validado:
                avisos.append(f"{clave}: se esperaba fecha y no calza")
                return ""              # NO ingerir valor mal parseado
            return raw.strip()[:30]

        fecha_envio = _fecha("fecha_envio")
        # id del registro
        if spec["id"] == "expediente":
            cod = _cel(tds, col.get("expediente"))
            if not cod:
                continue               # sin id no se puede deduplicar → se salta
        else:  # hash de categoría+fecha+asunto (formas sin expediente visible)
            if not (fecha_envio or asunto):
                continue
            base = (categoria + "|" + fecha_envio + "|" + asunto).encode("utf-8")
            cod = "SUNAFIL-" + hashlib.sha1(base).hexdigest()[:16]
        # plazo (solo si la forma lo trae y es numérico)
        plazo = None
        if "plazo_dias" in claves_mapa:
            raw_pl = _cel(tds, col.get("plazo_dias"))
            plazo = _plazo_a_dias(raw_pl)
            if raw_pl and plazo is None and not validado:
                avisos.append("plazo: se esperaba número y no calza")
        # estado leído/no-leído: ÍCONO primario; texto 'Estado' respaldo secundario.
        leido = _leido_por_icono(tr)
        estado_txt = _cel(tds, col.get("estado_txt")) if "estado_txt" in claves_mapa else ""
        if leido is None and estado_txt:
            leido = ("no le" not in _norm(estado_txt))

        item = {
            "cod_mensaje": cod, "tipo_msj": 2, "fuente": "sunafil",
            "categoria": categoria, "asunto": asunto,
            "fecha_envio": fecha_envio,
            "fecha_notificacion": _fecha("fecha_notificacion"),
            "fecha_acuse": _fecha("fecha_acuse") if "fecha_acuse" in claves_mapa else "",
            "fecha_limite": _fecha("fecha_limite") if "fecha_limite" in claves_mapa else "",
            "plazo_dias": plazo,
            "no_leida": (leido is False),
            "_forma": spec["clave"], "_validado": validado,
            "_estado_icono": leido,
        }

        # Señal de confirmación: primera fila REAL de una forma NO validada.
        if not validado and senales is not None and \
                not any(s["categoria"] == categoria for s in senales):
            formato = {clave: _clasif_valor(_cel(tds, col.get(clave)))
                       for _, clave in spec["mapa"] if not clave.startswith("_")}
            # ¿la celda de acción esconde un id? (para confirmar id oculto vs hash)
            id_oculto = False
            for ck in ("_accion", "_accion_escritura"):
                ci = col.get(ck)
                if ci is not None and ci < len(tds):
                    if tds[ci].query_selector("[onclick], input[type='hidden'], [data-id], a[href*='id']"):
                        id_oculto = True
            senales.append({
                "categoria": categoria, "forma": spec["clave"], "nombre": spec["nombre"],
                "id_tipo": spec["id"], "columnas": heads_norm[:],
                "formato_detectado": formato, "accion_id_oculto": id_oculto,
                "avisos_formato": avisos,
            })
        items.append(item)
    return items


def _siguiente_pagina(page) -> bool:
    """Avanza a la página siguiente del datatable (jQuery DataTables / PrimeFaces).
    False si no hay siguiente o está deshabilitada. SOLO navega (no acusa nada)."""
    for sel in ("a.paginate_button.next", ".dataTables_paginate a.next",
                "li.next:not(.disabled) > a", ".ui-paginator-next"):
        btn = page.query_selector(sel)
        if not btn:
            continue
        try:
            cls = (btn.get_attribute("class") or "")
            padre = btn.evaluate("e => e.parentElement ? e.parentElement.className : ''") or ""
        except Exception:
            cls, padre = "", ""
        if "disabled" in cls or "disabled" in padre or "ui-state-disabled" in cls:
            return False
        try:
            btn.evaluate("el => el.click()")
            return True
        except Exception:
            return False
    return False


def _firma_tabla(tabla) -> str:
    """Firma interna de la página (para cortar paginación repetida). No se imprime."""
    try:
        return str(hash(tabla.inner_text()))
    except Exception:
        return ""


def _contar_filas_datos(tabla) -> int:
    """Filas con datos reales (≥2 celdas no vacías). Ignora el placeholder de tabla
    vacía ('No hay registros', normalmente una sola celda con colspan)."""
    n = 0
    for tr in tabla.query_selector_all("tbody tr"):
        tds = tr.query_selector_all("td")
        if sum(1 for td in tds if (td.inner_text() or "").strip()) >= 2:
            n += 1
    return n


def _cerrar_modales(page, diag: bool) -> None:
    """Cierra el modal inicial de SUNAFIL (contacto / resumen 'N sin revisar').

    GUARDRAIL (regla permanente): alerta.pe SOLO LEE. Aquí SOLO se DESCARTA el
    modal (X, Cerrar, Omitir, Ahora no, Cancelar) — NUNCA se pulsa 'Aceptar' /
    'Continuar' / 'Guardar', que en el modal de *Registro de contacto* podrían
    REGISTRAR el contacto (efecto real). El acuse/registro los hace el usuario."""
    for sel in ("button[aria-label='Close']", "button[aria-label='Cerrar']",
                ".modal .close", ".modal button.btn-close", ".swal2-close",
                "button:has-text('Cerrar')", "button:has-text('Omitir')",
                "button:has-text('Ahora no')", "button:has-text('Cancelar')",
                "button:has-text('Más tarde')", "button:has-text('Saltar')"):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(400)
                if diag:
                    log(f"   [sunafil-diag] modal descartado con {sel}", "INFO")
        except Exception:
            continue
    # Fallback SEGURO: si el modal persiste, tecla Escape (nunca envía formularios).
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def _plazo_a_dias(txt: str):
    m = re.search(r"(\d+)", txt or "")
    return int(m.group(1)) if m else None


def leer_casilla_sunafil(cfg: SunatConfig, conocidos: set | None = None,
                         categorias: list | None = None, diag: bool = False) -> dict:
    """Lee el buzón SUNAFIL del RUC. Reusa el login SOL. Devuelve un resultado con
    el MISMO shape que el scraper SUNAT (mensajes[]) para que `ingestar_resultado`
    lo guarde igual (con fuente='sunafil').

    Por cada categoría: navega a su lista (enlace 'Notificaciones de …'), DETECTA la
    forma de la tabla POR CABECERA (A=expediente/plazo · B=aviso), recorre TODAS las
    páginas (paginación) y parsea cada fila. El estado leído/no-leído sale del ÍCONO
    (universal). SOLO LEE: reporta el estado, nunca lo cambia ni acusa recibo.
    Si una categoría trae una forma DESCONOCIDA, la registra en `formas_desconocidas`
    con su esquema y NO la parsea (prefiere avisar 'no sé leer esto' a inventar)."""
    resultado = {"ruc": cfg.ruc, "fuente": "sunafil",
                 "scrapeado_at": ahora_lima().isoformat(), "mensajes": [],
                 "formas_desconocidas": [], "mapeos_no_validados": [], "exito": False}
    cats = categorias or CATEGORIAS_SUNAFIL
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=cfg.headless,
                                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = nav.new_context(locale="es-PE", timezone_id="America/Lima",
                              user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                                          "Chrome/120.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        try:
            # 1) Entrar al buzón por /si.inbox/Login/SUNAT (login SOL en contexto).
            if not _login_casilla(page, cfg, diag):
                log(f"SUNAFIL: no se alcanzó el inbox (URL final: {page.url}).", "ERROR")
                if diag:
                    _evidencia(page, "sunafil_00_no_inbox")
                return resultado
            _cerrar_modales(page, diag)
            if diag:
                log(f"   [sunafil-diag] DENTRO del inbox: {page.url}", "INFO")

            # Recorrer categorías: cada una navega a su lista, detecta la forma POR
            # CABECERA, pagina y parsea. dedup por id entre categorías.
            vistos = set()
            for cat in cats:
                try:
                    if not _abrir_categoria(page, cat, diag):
                        log(f"SUNAFIL '{cat}': no cargó la lista (sin acceso, sin "
                            f"registros o timeout tras reintento).", "WARN")
                        # no cortamos: el while de abajo verá 'sin tabla' → 0 filas.
                    _cerrar_modales(page, diag)

                    # Recorrer TODAS las páginas de la categoría (paginación datatable).
                    filas_cat, spec_cat, firmas, n_pag = [], None, set(), 0
                    senales_cat = []            # señal de mapeo NO validado (1ª fila real)
                    while n_pag < 40:
                        tabla = _tabla_notificaciones(page)
                        if tabla is None:
                            break
                        heads = _headers(tabla)
                        heads_norm = [_norm(h) for h in heads]
                        spec = _detectar_forma(heads_norm)
                        if spec is None:
                            # FORMA DESCONOCIDA (sin mapeador): registra el esquema + nº de
                            # filas y NO parsea (no inventa). Dice si ya se pobló.
                            if not any(fd["categoria"] == cat
                                       for fd in resultado["formas_desconocidas"]):
                                n_fd = _contar_filas_datos(tabla)
                                resultado["formas_desconocidas"].append(
                                    {"categoria": cat, "columnas": heads, "filas": n_fd})
                                log(f"SUNAFIL '{cat}': FORMA DESCONOCIDA ({n_fd} fila(s) con "
                                    f"datos) — no la parseo (prefiero avisar a inventar). "
                                    f"columnas={heads}", "WARN")
                            break
                        spec_cat = spec
                        fr = _firma_tabla(tabla)
                        if fr and fr in firmas:
                            break                        # página repetida → fin
                        firmas.add(fr)
                        filas_cat += _parsear_pagina(tabla, spec, cat, heads_norm, senales_cat)
                        if not _siguiente_pagina(page):
                            break
                        page.wait_for_timeout(1200)
                        n_pag += 1

                    # Señal de confirmación (mapeo NO validado con su 1ª fila real).
                    for s in senales_cat:
                        resultado["mapeos_no_validados"].append(s)
                        log(f"SUNAFIL '{cat}': MAPEO NO VALIDADO (forma {s['forma']}) con su "
                            f"1ª fila real → CONFIRMAR formato: {s['formato_detectado']} "
                            f"| id={s['id_tipo']} id_oculto={s['accion_id_oculto']}"
                            + (f" | AVISOS: {s['avisos_formato']}" if s['avisos_formato'] else ""),
                            "WARN")

                    no_leidas = sum(1 for f in filas_cat if f["no_leida"])
                    etq = (f"{spec_cat['clave']}{'' if spec_cat['validado'] else '·NO-VALID'}"
                           if spec_cat else "—")
                    if diag:
                        log(f"SUNAFIL '{cat}': forma {etq} · {len(filas_cat)} fila(s) en "
                            f"{n_pag + 1} pág · {no_leidas} no-leída(s).", "OK")
                    else:
                        log(f"SUNAFIL '{cat}': {len(filas_cat)} notificación(es), "
                            f"{no_leidas} no-leída(s) [forma {etq}].", "OK")
                    for f in filas_cat:
                        cod = str(f["cod_mensaje"])
                        if cod in vistos:
                            continue                     # ya visto en otra categoría
                        vistos.add(cod)
                        if conocidos is not None and cod in conocidos:
                            continue
                        for k in ("_estado_icono", "_forma", "_validado"):
                            f.pop(k, None)               # metadatos internos, no se ingestan
                        resultado["mensajes"].append(f)
                except Exception as e:
                    log(f"SUNAFIL '{cat}': error ({e}).", "WARN")
                    continue
            resultado["exito"] = True
            log(f"SUNAFIL: {len(resultado['mensajes'])} notificación(es) nueva(s).", "OK")
        finally:
            ctx.close()
            nav.close()
    return resultado
