"""
scraper_sunat.py — alerta.pe Buzón
═══════════════════════════════════════════════════════════════════════
MVP mínimo: loguea en SUNAT con Playwright (Chromium real) y baja, para
UN RUC, las carpetas, los mensajes y los PDFs del buzón electrónico.

Objetivo de esta versión: VALIDAR que Playwright resuelve el flujo OAuth
que httpx no pudo (quirk WinHTTP vs libcurl). Sin BD, sin Celery.
Guarda texto en JSON y PDFs en disco local (./descargas/).

Flujo OAuth (mapeado por F12 en pagoOK-1):
  login SOL → j_security_check → JWT en redirect
  → MenuInternet.htm?action=buzon&s=ww1
  → redirect a ww1.sunat.gob.pe/.../visor/master?hc=...&token=...
  → endpoints JSON: listarCarpetas, listNotiMenPag, obtenerDetalleNotiMen

Con Playwright NO seguimos redirects ni capturamos JWT a mano: el
navegador hace todo el baile y nosotros reusamos su contexto autenticado
(api_request_context) para las llamadas JSON y la descarga de PDFs.

Uso:
  pip install playwright
  playwright install chromium
  python scraper_sunat.py
  (credenciales por variables de entorno; ver SunatConfig abajo)

Zona horaria: SIEMPRE America/Lima.
"""

from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import time
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, Page, APIRequestContext, TimeoutError as PWTimeout

# Clasificación (para decidir qué documentos son DEUDA → 2º PDF, zAlerta-34).
# Import perezoso-seguro: si faltara (contexto sin DB), el scraper sigue
# funcionando para el índice; solo se omite la valoración.
try:
    from clasificacion import clasificar as _clasificar
    from clasificacion import solo_digitos as _solo_digitos
    from models import TIPODOC_A_VALORADO as _TIPODOC_A_VALORADO
except Exception:   # pragma: no cover
    _clasificar = None
    _solo_digitos = lambda s: "".join(ch for ch in (s or "") if ch.isdigit())
    _TIPODOC_A_VALORADO = {}

# Carga automática de .env (no declarar variables a mano en el terminal)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # si no está python-dotenv, se usan las variables del entorno

# ─────────────────────────────────────────────────────────────────────
# Zona horaria Perú — usada en TODO momento
# ─────────────────────────────────────────────────────────────────────
TZ_LIMA = ZoneInfo("America/Lima")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def ts_lima() -> str:
    return ahora_lima().strftime("%d/%m/%Y %H:%M:%S")


def log(msg: str, nivel: str = "INFO") -> None:
    print(f"[{ts_lima()}] [{nivel}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────
# Configuración (credenciales del RUC a scrapear)
# ─────────────────────────────────────────────────────────────────────
@dataclass
class SunatConfig:
    ruc: str = field(default_factory=lambda: os.getenv("SUNAT_RUC", ""))
    usuario_sol: str = field(default_factory=lambda: os.getenv("SUNAT_USUARIO", ""))
    clave_sol: str = field(default_factory=lambda: os.getenv("SUNAT_CLAVE", ""))
    # headless=False ayuda a depurar localmente; en Railway será True
    headless: bool = field(default_factory=lambda: os.getenv("SUNAT_HEADLESS", "true").lower() == "true")

    def validar(self) -> None:
        faltan = [k for k, v in {
            "SUNAT_RUC": self.ruc,
            "SUNAT_USUARIO": self.usuario_sol,
            "SUNAT_CLAVE": self.clave_sol,
        }.items() if not v]
        if faltan:
            log(f"Faltan variables de entorno: {', '.join(faltan)}", "ERROR")
            sys.exit(1)


# ─────────────────────────────────────────────────────────────────────
# URLs de SUNAT (del flujo mapeado)
# ─────────────────────────────────────────────────────────────────────
URL_LOGIN = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
URL_BUZON = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?action=buzon&s=ww1"
# Base del visor (host ww1) — los endpoints JSON cuelgan de aquí
VISOR_BASE = "https://ww1.sunat.gob.pe/ol-ti-itvisornoti"

DESCARGAS = Path("./descargas")

# Año DESDE el que se baja el 2º PDF de DEUDA (zAlerta-62: deuda histórica).
# Solo afecta a DEUDA (OP/Multa/Coactiva/Fraccionamiento), nunca a informativos
# (dos velocidades, zAlerta-45). Configurable por env; default 2019.
ANIO_DEUDA_DESDE = int(os.getenv("ANIO_DEUDA_DESDE", "2019"))

# ── Descarga CONTROLADA (zAlerta-83): gestión del riesgo de ban ──
# THROTTLE: pausa entre peticiones a SUNAT (detalle/adjuntos). El histórico va a
# ritmo seguro; los incrementales (pocos) casi no lo notan. Configurable por env.
DESCARGA_PAUSA_S = float(os.getenv("SCRAPER_PAUSA_S", "0.4"))
# LÍMITE de documentos con descarga (detalle+PDF) por barrido. Si el rango pide
# más, se descarga hasta el límite (recientes primero) y se marca para que la UI
# ofrezca "reduce años". Los buzones chicos quedan debajo → sin restricción.
MAX_DOCS_BARRIDO = int(os.getenv("SCRAPER_MAX_DOCS", "150"))
# Límites SEPARADOS del backfill (zAlerta-85): las descargas reales de PDF son
# caras (2-3 peticiones c/u) y las cuidamos; abrir un detalle sin adjunto es
# barato (1 petición), así que su tope puede ser mayor. Convergen el backlog:
# los "con PDF" bajan y ganan gcs_key; los "sin adjunto" se marcan y no vuelven.
MAX_PDF_POR_CORRIDA = int(os.getenv("SCRAPER_MAX_PDF", str(MAX_DOCS_BARRIDO)))
MAX_SINPDF_POR_CORRIDA = int(os.getenv("SCRAPER_MAX_SINPDF", "300"))


# ─────────────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────────────
def _evidencia(page: Page, etiqueta: str) -> None:
    """Vuelca screenshot + HTML + URL actual para diagnóstico.

    La página puede estar navegando (redirect OAuth); esperamos brevemente a
    que se asiente y, si aun así no se puede capturar, lo registramos sin
    generar ruido de error (no es un fallo del scraping).
    """
    DESCARGAS.mkdir(parents=True, exist_ok=True)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except Exception:
        pass
    try:
        page.screenshot(path=str(DESCARGAS / f"diag_{etiqueta}.png"), full_page=True)
    except Exception as e:
        log(f"   [diag] sin screenshot (página en movimiento): {e}", "INFO")
    try:
        (DESCARGAS / f"diag_{etiqueta}.html").write_text(page.content(), encoding="utf-8")
        log(f"   [diag] URL actual: {page.url}")
        log(f"   [diag] evidencia: diag_{etiqueta}.png / diag_{etiqueta}.html")
    except Exception as e:
        log(f"   [diag] sin HTML (página en movimiento): {e}", "INFO")


def _esperar_pagina_estable(page: Page) -> None:
    """Espera a que la página deje de navegar/redirigir antes de tocarla.

    En Railway la página de SUNAT llega más lenta y sigue navegando; estas
    esperas absorben esa latencia. En local, si ya está cargada, retornan al
    instante (no penalizan)."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except Exception:
        pass
    try:
        # networkidle puede no alcanzarse si SUNAT mantiene conexiones abiertas;
        # no es fatal, es solo un margen para que el form termine de aparecer.
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass


def login_sol(page: Page, cfg: SunatConfig) -> bool:
    """Loguea en SOL. Chromium sigue toda la cadena OAuth automáticamente.

    Robustez de timing (Railway): espera explícitamente a que el formulario
    EXISTA y sea interactuable antes de llenar cada campo, y reintenta el login
    completo si los campos no aparecen (la página de SUNAT puede llegar lenta o
    seguir navegando). Los selectores NO cambian; solo CUÁNDO se usan.
    """
    log("Abriendo portal de login SOL...")

    # El formulario de SOL pide RUC + Usuario + Clave.
    # Probamos varios selectores candidatos porque SUNAT cambia los IDs.
    sel_ruc = ["#txtRuc", "input[name='txtRuc']", "#ruc", "input[name='ruc']"]
    sel_usr = ["#txtUsuario", "input[name='txtUsuario']", "#usuario", "input[name='usuario']"]
    sel_clave = ["#txtContrasena", "input[name='txtContrasena']", "#clave",
                 "input[name='clave']", "input[type='password']"]
    sel_btn = ["#btnAceptar", "button[type='submit']", "input[type='submit']", "#submit"]

    def llenar(selectores, valor, nombre) -> bool:
        # ESPERAR a que aparezca CUALQUIERA de los selectores candidatos (hasta
        # 30s, Railway es lento) en vez de buscarlo de inmediato. El selector
        # combinado espera UNA sola vez por el conjunto, no 30s por cada uno.
        combinado = ", ".join(selectores)
        try:
            page.wait_for_selector(combinado, timeout=30_000, state="visible")
        except PWTimeout:
            log(f"   campo {nombre}: NINGÚN selector apareció en 30s {selectores}", "WARN")
            return False
        except Exception as e:
            log(f"   campo {nombre}: error esperando el campo ({e})", "WARN")
            return False
        for s in selectores:
            try:
                el = page.query_selector(s)
                if el:
                    el.fill(valor)
                    log(f"   campo {nombre}: usado selector {s}")
                    return True
            except Exception:
                continue
        log(f"   campo {nombre}: NINGÚN selector funcionó {selectores}", "WARN")
        return False

    # ── Reintentos del login completo: absorbe la variabilidad de Railway/SUNAT ──
    MAX_INTENTOS = 3
    for intento in range(1, MAX_INTENTOS + 1):
        if intento > 1:
            log(f"Reintentando login completo ({intento}/{MAX_INTENTOS})...", "WARN")
        try:
            page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            log(f"   no se pudo abrir el login (intento {intento}): {e}", "WARN")
            continue
        log(f"   URL tras goto inicial: {page.url}")

        # Esperar a que la página deje de navegar ANTES de llenar campos.
        _esperar_pagina_estable(page)
        if intento == 1:
            _evidencia(page, "01_login_inicial")

        ok_ruc = llenar(sel_ruc, cfg.ruc, "RUC")
        ok_usr = llenar(sel_usr, cfg.usuario_sol, "usuario")
        ok_clave = llenar(sel_clave, cfg.clave_sol, "clave")

        if ok_ruc and ok_usr and ok_clave:
            break  # campos completos → seguir con el envío

        log(f"No se llenaron todos los campos (intento {intento}/{MAX_INTENTOS}).",
            "WARN")
        _evidencia(page, f"02_campos_fallidos_intento{intento}")
    else:
        # El for terminó sin break: nunca se llenaron los campos.
        log("No se pudieron llenar todos los campos del login tras varios "
            "intentos.", "ERROR")
        return False

    log(f"Credenciales ingresadas (RUC {cfg.ruc}, usuario {cfg.usuario_sol})")

    # Click en el botón de envío
    clicked = False
    for s in sel_btn:
        try:
            el = page.query_selector(s)
            if el:
                el.click()
                log(f"   click en botón: {s}")
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        log("No se encontró botón de envío.", "WARN")
        _evidencia(page, "03_sin_boton")

    # En vez de exigir una URL específica, esperamos a que la red se calme
    # y reportamos DÓNDE quedó. Así ajustamos el regex con datos reales.
    try:
        page.wait_for_load_state("networkidle", timeout=45_000)
    except PWTimeout:
        log("   networkidle no se alcanzó en 45s (puede seguir cargando).", "WARN")

    # IMPORTANTE: el OAuth pasa por un estado intermedio
    # (api-seguridad.sunat.gob.pe/?code=JWT) ANTES de redirigir al menú.
    # Si caímos ahí, esperamos a que complete el redirect final a MenuInternet.
    for intento in range(20):  # ~20 * 1s = 20s máximo
        url_actual = page.url.lower()
        if "menuinternet" in url_actual or "itmenu" in url_actual:
            break  # ya llegó al menú
        if "api-seguridad" in url_actual and "code=" in url_actual:
            # Estado intermedio: el redirect aún no completó. Esperar.
            log(f"   (redirect OAuth en curso, intento {intento+1}/20)...")
            try:
                page.wait_for_url(re.compile(r"(MenuInternet|itmenu)", re.I), timeout=3_000)
                break
            except PWTimeout:
                page.wait_for_timeout(1_000)
                continue
        else:
            page.wait_for_timeout(1_000)

    log(f"   URL tras login: {page.url}")
    _evidencia(page, "04_post_login")

    # ¿Llegamos al menú? Aceptamos varias señales de éxito.
    url = page.url.lower()
    exito = ("menuinternet" in url or "itmenu" in url)
    if exito:
        log("Login OK — sesión autenticada (URL de menú detectada).", "OK")
        return True

    # Si seguimos en api-seguridad con code=, el login fue válido pero el
    # redirect no completó a tiempo — lo forzamos navegando al menú.
    if "api-seguridad" in url and "code=" in url:
        log("   Login válido pero redirect lento; forzando navegación al menú...", "WARN")
        try:
            page.goto(URL_LOGIN, wait_until="networkidle", timeout=30_000)
            if "menuinternet" in page.url.lower() or "itmenu" in page.url.lower():
                log("Login OK — menú alcanzado tras forzar navegación.", "OK")
                return True
        except Exception as e:
            log(f"   No se pudo forzar el menú: {e}", "ERROR")

    # Heurística de credencial inválida
    contenido = page.content().lower()
    if any(t in contenido for t in ["clave incorrect", "usuario incorrect",
                                     "inválid", "no coincide", "captcha"]):
        log("SUNAT parece reportar error de credenciales o captcha.", "ERROR")
    else:
        log("Login terminó en URL inesperada — revisar diag_04_post_login.html "
            "para ver dónde cayó y ajustar el regex/selectores.", "ERROR")
    return False


# ─────────────────────────────────────────────────────────────────────
# Entrar al visor del buzón
# ─────────────────────────────────────────────────────────────────────
def entrar_buzon(page: Page):
    """Entra al buzón replicando el clic real del menú SOL.

    HALLAZGO (del HTML post-login): el buzón NO se abre navegando a
    'MenuInternet.htm?action=buzon&s=ww1' directo — eso sirve la página
    puente 'Bienvenidos a SUNAT'. El menú real ejecuta:
        ejecuta('MenuInternet.htm?action=buzon', false, ...)
    al hacer clic en #aOpcionBuzon, y carga el visor dentro de un iframe.

    Por eso clickeamos el elemento real y leemos el frame resultante.
    Devuelve el Frame del visor (o None si falla).
    """
    log("Entrando al buzón: clic en #aOpcionBuzon (flujo real del menú)...")

    # El enlace puede estar oculto hasta que cargue el menú; esperamos a que exista
    try:
        page.wait_for_selector("#aOpcionBuzon", timeout=20_000, state="attached")
    except PWTimeout:
        log("No se encontró #aOpcionBuzon en el menú.", "ERROR")
        _evidencia(page, "05_sin_aOpcionBuzon")
        return None

    # Clic vía JS para disparar el handler aunque el <a> esté oculto/estilizado
    try:
        page.eval_on_selector("#aOpcionBuzon", "el => el.click()")
    except Exception:
        # fallback: invocar directamente la función del menú
        try:
            page.evaluate("ejecuta('MenuInternet.htm?action=buzon', false, '', '', '')")
        except Exception as e:
            log(f"No se pudo disparar el buzón: {e}", "ERROR")
            _evidencia(page, "06_click_buzon_fallido")
            return None

    # El visor carga en un iframe. Esperamos a que aparezca un frame de itvisornoti.
    log("Esperando a que cargue el iframe del visor (itvisornoti)...")
    visor_frame = None
    for intento in range(30):  # ~30 * 1s = 30s máx
        page.wait_for_timeout(1_000)
        for fr in page.frames:
            if "itvisornoti" in (fr.url or "") or "visor/master" in (fr.url or ""):
                visor_frame = fr
                break
        if visor_frame:
            break

    if not visor_frame:
        log("El iframe del visor no apareció. Frames actuales:", "ERROR")
        for fr in page.frames:
            log(f"   frame: {fr.url}")
        _evidencia(page, "07_sin_iframe_visor")
        return None

    log(f"Visor cargado en iframe: {visor_frame.url[:90]}...", "OK")
    page.wait_for_timeout(2_000)  # dejar asentar cookies/JS del visor
    return visor_frame


# ─────────────────────────────────────────────────────────────────────
# Llamadas JSON reusando el contexto autenticado del navegador
# ─────────────────────────────────────────────────────────────────────
def fetch_json(api: APIRequestContext, url: str, etiqueta: str):
    """GET a un endpoint JSON del visor con las cookies de la sesión actual."""
    resp = api.get(url, timeout=30_000)
    if resp.status != 200:
        log(f"{etiqueta}: status {resp.status}", "WARN")
        return None
    try:
        return resp.json()
    except Exception:
        log(f"{etiqueta}: respuesta no es JSON válido ({len(resp.body())} bytes)", "WARN")
        return None


def limpiar_html_entities(texto: str) -> str:
    """SUNAT devuelve entidades como &Oacute; — las normalizamos."""
    import html
    return html.unescape(texto or "")


def listar_carpetas(api: APIRequestContext, visor_base: str) -> list:
    # Path real capturado: /visor/ajax/listarCarpetas
    data = fetch_json(api, f"{visor_base}/visor/ajax/listarCarpetas", "listarCarpetas")
    if not data:
        return []
    if isinstance(data, dict):
        data = data.get("rows") or data.get("lista") or data.get("carpetas") or []
    for c in data:
        if isinstance(c, dict):
            c["nomCarpeta"] = limpiar_html_entities(c.get("nomCarpeta", ""))
    log(f"Carpetas encontradas: {len(data)}", "OK")
    for c in data:
        if isinstance(c, dict):
            log(f"  · [{c.get('codCarpeta')}] {c.get('nomCarpeta')} "
                f"({c.get('cantMensajes', 0)} mensajes)")
    return data


def _cod_de_row(m: dict):
    """cod_mensaje de una fila del índice (varios nombres candidatos)."""
    return (m.get("codigoMensaje") or m.get("codMensaje")
            or m.get("codMensa") or m.get("codigo"))


def _pagina_toda_conocida(rows: list, conocidos: set) -> bool:
    """True si TODOS los cod_mensaje de la página ya están en BD (conocidos).
    Página vacía → True. Requiere página ENTERA conocida: un mensaje viejo
    intercalado NO detiene el barrido (zAlerta-46, correctitud > velocidad)."""
    cods = [str(_cod_de_row(r)) for r in rows
            if isinstance(r, dict) and _cod_de_row(r)]
    if not cods:
        return True
    return all(c in conocidos for c in cods)


def listar_mensajes(api: APIRequestContext, visor_base: str,
                    tipo_msj: int, cod_carpeta: str = "00",
                    max_paginas: int = 200, conocidos: set | None = None) -> list:
    """Lista mensajes de una bandeja PAGINANDO (zAlerta-34 Paso 1).

    Recorre `1..total` (SUNAT devuelve `total`=nº de páginas). Sleep corto entre
    páginas para no martillear.

    INCREMENTAL (zAlerta-46): si se pasa `conocidos` (set de cod_mensaje ya en
    BD para este contribuyente), PARA de paginar en cuanto una PÁGINA COMPLETA no
    trae ningún mensaje nuevo (todos conocidos). NO para al primer conocido: exige
    la página entera conocida (un mensaje reactivado/reordenado no engaña). Sin
    `conocidos` → barrido COMPLETO (como antes).
    """
    def _pagina(page: int):
        url = (f"{visor_base}/visor/listNotiMenPag"
               f"?tipoMsj={tipo_msj}&codCarpeta={cod_carpeta}&codEtiqueta="
               f"&page={page}&des_asunto=&codMensaje=&tipoOrden=NADA")
        return fetch_json(api, url, f"listNotiMenPag(t={tipo_msj},c={cod_carpeta},p={page})")

    d1 = _pagina(1)
    if not d1:
        return []
    rows = list(d1.get("rows", d1) if isinstance(d1, dict) else d1)
    total_pags = 1
    if isinstance(d1, dict):
        try:
            total_pags = max(1, int(d1.get("total") or 1))
        except (TypeError, ValueError):
            total_pags = 1
    total_pags = min(total_pags, max_paginas)
    incremental = conocidos is not None

    # Página 1 ya toda conocida (buzón sin novedades) → no seguir.
    if incremental and _pagina_toda_conocida(rows, conocidos):
        log(f"  Bandeja t={tipo_msj} c={cod_carpeta}: pág.1 toda conocida "
            f"→ sin novedades ({len(rows)} filas, incremental).", "OK")
        return rows

    for pg in range(2, total_pags + 1):
        time.sleep(random.uniform(0.4, 0.9))
        dp = _pagina(pg)
        if not dp:
            continue
        pagina = list(dp.get("rows", dp) if isinstance(dp, dict) else dp)
        rows += pagina
        if incremental and _pagina_toda_conocida(pagina, conocidos):
            log(f"  Bandeja t={tipo_msj} c={cod_carpeta}: pág.{pg} toda conocida "
                f"→ paro incremental ({len(rows)} filas leídas).", "OK")
            break

    log(f"  Bandeja tipoMsj={tipo_msj} carp={cod_carpeta}: "
        f"{len(rows)} mensajes{' (incremental)' if incremental else f' ({total_pags} pág.)'}", "OK")
    return rows


def obtener_detalle(api: APIRequestContext, visor_base: str,
                    codigo_mensaje: str, tipo_msj: int) -> dict | None:
    # Path real: /visor/obtenerDetalleNotiMen?codigoMensaje=...&tipoMsj=...
    url = (f"{visor_base}/visor/obtenerDetalleNotiMen"
           f"?codigoMensaje={codigo_mensaje}&tipoMsj={tipo_msj}")
    return fetch_json(api, url, "obtenerDetalleNotiMen")


# ─────────────────────────────────────────────────────────────────────
# Descarga de PDFs
# ─────────────────────────────────────────────────────────────────────
def descargar_adjuntos(api: APIRequestContext, visor_base: str, detalle: dict,
                       cod_mensaje: str, ruc: str) -> tuple[list, bool]:
    """Descarga los adjuntos de un mensaje. Devuelve (guardados, sunat_vacio).

    sunat_vacio (zAlerta-86): True si HABÍA adjuntos declarados y SUNAT los sirvió
    TODOS vacíos (status 200, 0 bytes) — señal honesta de "no disponible", NO un
    error transitorio. Sirve para marcar sin re-intentar en loop. Si algún fallo
    fue por error/timeout (no 200-vacío), NO se marca vacío → se reintenta.

    Estructura real (confirmada): detalle['listAttach'] = [
       {'codArchivo': 1078219469, 'nomArchivo': '..._CRONOGRAMA.pdf',
        'cntTamarch': 138247, 'indMensaje': '1', ...}, ...]
    Solo descargamos los que tienen codArchivo + nomArchivo.
    """
    guardados = []
    attachs = detalle.get("listAttach") or []
    # Filtrar solo los que tienen archivo real
    attachs = [a for a in attachs if a.get("codArchivo") and a.get("nomArchivo")]
    if not attachs:
        return guardados, False

    destino = DESCARGAS / ruc / str(cod_mensaje)
    destino.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_vacio_200 = 0   # SUNAT respondió 200 pero con 0/casi 0 bytes (no-disponible)
    for a in attachs:
        cod_archivo = a["codArchivo"]
        nombre = a["nomArchivo"]
        if not nombre.lower().endswith(".pdf"):
            nombre += ".pdf"
        ind_msj = a.get("indMensaje", "1")

        # Endpoint REAL capturado: /visor/bajarArchivo/{codArchivo}/0/0/{ruc}
        urls = [
            f"{visor_base}/visor/bajarArchivo/{cod_archivo}/0/0/{ruc}",
            f"{visor_base}/visor/bajarArchivo/{cod_archivo}/0/0/{ruc}/",
        ]
        descargado = False
        ultimo_status = None
        ultimo_ct = None
        ultimo_len = 0
        for url in urls:
            try:
                resp = api.get(url, timeout=60_000)
                ct = (resp.headers.get("content-type") or "").lower()
                body = resp.body()
                ultimo_status, ultimo_ct, ultimo_len = resp.status, ct, len(body)
                if resp.status == 200 and len(body) > 1000 and (
                        "pdf" in ct or "octet-stream" in ct or body[:4] == b"%PDF"):
                    ruta = destino / nombre
                    ruta.write_bytes(body)
                    guardados.append(str(ruta))
                    log(f"    PDF guardado: {nombre} ({len(body)} bytes) "
                        f"[{url.split('?')[0].split('/')[-1]}]", "OK")
                    descargado = True
                    break
            except Exception:
                continue
        if descargado:
            n_ok += 1
        else:
            log(f"    No se pudo descargar {nombre} (codArchivo={cod_archivo}) — "
                f"status={ultimo_status} ct={ultimo_ct} bytes={ultimo_len}", "WARN")
            # 200 con 0/casi 0 bytes = SUNAT sirve vacío (no-disponible honesto).
            # status != 200 o excepción = fallo transitorio → NO cuenta como vacío.
            if ultimo_status == 200 and ultimo_len <= 1000:
                n_vacio_200 += 1
            guardados.append(f"PENDIENTE:codArchivo={cod_archivo}:{nombre}")
    # sunat_vacio: había adjuntos, ninguno bajó, y TODOS los fallos fueron 200-vacío.
    sunat_vacio = bool(attachs) and n_ok == 0 and n_vacio_200 == len(attachs)
    return guardados, sunat_vacio


# ─────────────────────────────────────────────────────────────────────
# 2º PDF de deuda (documento real) — zAlerta-34
# ─────────────────────────────────────────────────────────────────────
# El documento de deuda NO está en listAttach con codArchivo: aparece como una
# entrada con indMensaje="3" y `numId` (el constancia es indMensaje="2"). Se baja
# por POST a /visor/bajarArchivo (form-urlencoded) DENTRO de la sesión Playwright.
_RE_NUM_DOC = re.compile(r"\d{3}-\d{3}-\d{4,}")
_RE_MONTO = re.compile(r"S/\s*\d|importe|monto\s*total|deuda", re.I)


def _anio_de(fecha: str | None) -> int | None:
    """Año de una fecha SUNAT 'dd/MM/YYYY HH:MM:SS' (o None)."""
    if not fecha:
        return None
    m = re.search(r"/(\d{4})", str(fecha))
    return int(m.group(1)) if m else None


def _num_documento_de(asunto: str) -> str | None:
    """Extrae el nº de documento del asunto (ej. '123-001-0700325')."""
    m = _RE_NUM_DOC.search(asunto or "")
    if m:
        return m.group(0)
    m = re.search(r"[N|n][°ºoO]\s*([\dA-Z\-]{6,})", asunto or "")
    return m.group(1) if m else None


_RE_GOARCHIVO = re.compile(
    r"goArchivoDescarga\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
# zAlerta-88: el propio 'datos' de la URL de la carátula trae id_archivo/sistema/
# cod_mensaje. Las coactivas "fisca..." (iddoc=211, números SIN guiones) NO exponen
# goArchivoDescarga en el HTML, pero SÍ traen estos campos en la URL.
_RE_DATOS_IDARCH = re.compile(r'"id_archivo":"(\d+)"')
_RE_DATOS_SIS = re.compile(r'"sistema":"(\d+)"')
_RE_DATOS_CMSG = re.compile(r'"cod_mensaje":"(\d+)"')


def _id_deuda_de_url(car: str | None):
    """Extrae (idArchivo, sistema, codMensaje) del parámetro `datos` de la URL de
    la carátula (zAlerta-88). Fuente para el flujo "fisca..." que no expone
    goArchivoDescarga. Devuelve (idar, sis, cmsg) o (None, None, None)."""
    ida = _RE_DATOS_IDARCH.search(car or "")
    sis = _RE_DATOS_SIS.search(car or "")
    cm = _RE_DATOS_CMSG.search(car or "")
    if ida and sis and cm:
        return ida.group(1), sis.group(1), cm.group(1)
    return None, None, None


def _id_deuda_de_caratula(api: APIRequestContext, host: str, detalle: dict):
    """Obtiene (idArchivo, sistema, codMensaje) del documento de DEUDA desde la
    CARÁTULA. Fuente principal: el `goArchivoDescarga(idArchivo, sistema,
    codMensaje)` del HTML (zAlerta-35, SIN offset). Fallback (zAlerta-88): si el
    HTML NO lo expone (plantilla "fisca..." iddoc=211), se usa el id_archivo del
    propio `datos` de la URL de la carátula. Devuelve (idar, sis, cmsg) o
    (None, motivo, None)."""
    car = detalle.get("url")
    if not car:
        return None, "sin_caratula", None
    url = car if car.startswith("http") else host + ("" if car.startswith("/") else "/") + car
    try:
        html = api.get(url, timeout=40_000).text()
        m = _RE_GOARCHIVO.search(html or "")
        if m:
            return m.group(1), m.group(2), m.group(3)
    except Exception as e:
        log(f"    carátula no cargó: {e} (intento fallback datos-URL)", "WARN")
    # Fallback zAlerta-88: id_archivo del datos de la URL (flujo "fisca...").
    idar, sis, cmsg = _id_deuda_de_url(car)
    if idar:
        return idar, sis, cmsg
    return None, "sin_goarchivo", None


def descargar_documento_real(api: APIRequestContext, visor_base: str,
                             cod_mensaje: str, detalle: dict) -> tuple:
    """Baja el 2º PDF — el DOCUMENTO REAL de deuda (monto/periodo/tributo), NO la
    constancia. Devuelve (bytes|None, motivo).

    Mecanismo verificado (zAlerta-35), SIN offset:
      1) Carátula = campo `url` del detalle (gendocS01Alias?accion=genhtml).
      2) Parsear `goArchivoDescarga(idArchivo, sistema, codMensaje)` de la carátula
         → el idArchivo EXACTO del documento de deuda (puesto por SUNAT).
      3) POST {visor_base}/visor/bajarArchivo (form, DENTRO de la sesión Playwright)
         accion=archivo & idMensaje & idArchivo & sistema. 3 reintentos (flakiness).
    """
    m = re.match(r"(https?://[^/]+)", visor_base)
    host = m.group(1) if m else "https://ww1.sunat.gob.pe"
    idar, sis_or_motivo, cmsg = _id_deuda_de_caratula(api, host, detalle)
    if idar is None:
        return None, sis_or_motivo   # motivo: sin_caratula / caratula_error / sin_goarchivo
    form = {"accion": "archivo", "idMensaje": str(cmsg),
            "idArchivo": str(idar), "sistema": str(sis_or_motivo)}
    for intento in range(3):
        try:
            resp = api.post(f"{visor_base}/visor/bajarArchivo", form=form, timeout=60_000)
            body = resp.body()
            if resp.status == 200 and body[:4] == b"%PDF" and len(body) > 1000:
                return body, "ok"
        except Exception as e:
            log(f"    documento real cod={cod_mensaje}: intento {intento+1} error ({e})", "WARN")
        time.sleep(1.0)
    log(f"    documento real cod={cod_mensaje}: sin PDF de deuda tras 3 intentos", "WARN")
    return None, "post_sin_pdf"


# ─────────────────────────────────────────────────────────────────────
# Captura del CUERPO vía genhtml (zAlerta-94) — avisos cuyo cuerpo real y PDF
# se generan on-demand desde gendocS01Alias (no están en texto_html, que solo
# trae la cabecera JSON). Se PIDE a SUNAT el cuerpo (fiel) y el PDF por
# id_archivo (reusa goArchivoDescarga/id-URL de z-88).
# ─────────────────────────────────────────────────────────────────────
def _cuerpo_de_html(html_txt: str | None) -> str | None:
    """Extrae el cuerpo FIEL (bloque 'Estimada/o…Atentamente SUNAT') del HTML del
    generador. Limpia tags/entidades (doble-decode). Texto tal cual de SUNAT —
    no se reconstruye. Devuelve texto con saltos, o None si no hay cuerpo real."""
    if not html_txt:
        return None
    t = re.sub(r"(?is)<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", " ", html_txt)
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)</\s*(p|div|tr|li|h\d)\s*>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    try:
        t = _html.unescape(unquote(t))
    except Exception:
        t = _html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    low = t.lower()
    i = low.find("estimad")
    if i >= 0:
        t = t[i:]
        low = t.lower()
        j = low.rfind("atentamente")
        if j >= 0:
            k = low.find("sunat", j)
            t = t[:(k + 5)] if k >= 0 else t[:j].rstrip()
    lineas = [x.strip() for x in t.splitlines() if x.strip()]
    cuerpo = "\n".join(lineas).strip()
    # Sin marcador y muy corto → probablemente no es cuerpo (evita basura).
    if i < 0 and len(cuerpo) < 60:
        return None
    return cuerpo or None


def capturar_cuerpo_genhtml(api: APIRequestContext, host: str, visor_base: str,
                            url_gen: str | None) -> tuple:
    """Pide el generador (accion=genhtml) y devuelve (cuerpo_fiel|None,
    pdf_bytes|None, motivo). El PDF se obtiene por id_archivo: goArchivoDescarga
    del propio HTML, o el datos de la URL (z-88). No inventa nada."""
    if not url_gen:
        return None, None, "sin_url"
    u = url_gen if url_gen.startswith("http") else host + ("" if url_gen.startswith("/") else "/") + url_gen
    try:
        html_txt = api.get(u, timeout=40_000).text()
    except Exception as e:
        log(f"    genhtml no cargó: {e}", "WARN")
        return None, None, "genhtml_error"
    cuerpo = _cuerpo_de_html(html_txt)
    # PDF por id_archivo: primero goArchivoDescarga del HTML; si no, el datos-URL.
    idar = sis = cmsg = None
    m = _RE_GOARCHIVO.search(html_txt or "")
    if m:
        idar, sis, cmsg = m.group(1), m.group(2), m.group(3)
    else:
        idar, sis, cmsg = _id_deuda_de_url(u)
    pdf_bytes = None
    motivo = "solo_cuerpo"
    if idar:
        form = {"accion": "archivo", "idMensaje": str(cmsg or ""),
                "idArchivo": str(idar), "sistema": str(sis or "0")}
        for intento in range(3):
            try:
                resp = api.post(f"{visor_base}/visor/bajarArchivo", form=form, timeout=60_000)
                body = resp.body()
                if resp.status == 200 and body[:4] == b"%PDF" and len(body) > 1000:
                    pdf_bytes = body
                    motivo = "ok"
                    break
                if resp.status == 200 and len(body) <= 1000:
                    motivo = "pdf_vacio"   # SUNAT sirve vacío → no-disponible honesto
            except Exception:
                motivo = "pdf_error"
            time.sleep(1.0)
    return cuerpo, pdf_bytes, motivo


def capturar_pdf_por_id(api: APIRequestContext, visor_base: str,
                        id_archivo, sistema, cod_mensaje) -> tuple:
    """Captura GENERAL (zAlerta-95): pide el PDF por id_archivo (POST bajarArchivo,
    patrón z-88). Regla universal: si el JSON del mensaje trae id_archivo, hay un
    PDF descargable — sin importar el tipo/asunto. (pdf_bytes|None, motivo).
    Distinción honesta (z-86): 200-vacío = no-disponible; error = reintentar."""
    if not id_archivo:
        return None, "sin_id"
    form = {"accion": "archivo", "idMensaje": str(cod_mensaje or ""),
            "idArchivo": str(id_archivo), "sistema": str(sistema if sistema is not None else "0")}
    motivo = "pdf_error"
    for _intento in range(3):
        try:
            resp = api.post(f"{visor_base}/visor/bajarArchivo", form=form, timeout=60_000)
            body = resp.body()
            if resp.status == 200 and body[:4] == b"%PDF" and len(body) > 1000:
                return body, "ok"
            if resp.status == 200 and len(body) <= 1000:
                motivo = "pdf_vacio"      # SUNAT sirve vacío → no-disponible honesto
        except Exception:
            motivo = "pdf_error"
        time.sleep(1.0)
    return None, motivo


def texto_pdf(body: bytes) -> str:
    """Extrae texto (sin OCR) con pypdf. '' si no se puede."""
    try:
        import io
        import pypdf
        rd = pypdf.PdfReader(io.BytesIO(body))
        return "\n".join((pg.extract_text() or "") for pg in rd.pages)
    except Exception as e:
        log(f"    pypdf no pudo extraer texto: {e}", "WARN")
        return ""


def _tiene_monto(texto: str) -> bool:
    """Self-check: ¿el PDF trae un monto/ancla de deuda? (S/ + dígito, Importe…)."""
    return bool(_RE_MONTO.search(texto or ""))


# ─────────────────────────────────────────────────────────────────────
# Orquestación
# ─────────────────────────────────────────────────────────────────────
def scrapear_ruc(cfg: SunatConfig, conocidos: set | None = None,
                 anio_desde: int | None = None,
                 solo_censo: bool = False,
                 backfill: bool = False,
                 genhtml_pend: list | None = None,
                 pdf_pend: list | None = None) -> dict:
    # genhtml_pend (zAlerta-94): si se pasa una lista [{id, cod_mensaje, url,
    # num_documento}], el scraper SOLO hace login y captura el cuerpo (genhtml) +
    # PDF de esos avisos (familia gendocS01Alias), y sale. No barre el buzón.
    # pdf_pend (zAlerta-95): lista [{id, cod_mensaje, id_archivo, sistema,
    # num_documento}] → captura GENERAL del PDF por id_archivo (POST directo),
    # sin barrer. Regla universal "id_archivo en JSON → hay PDF".
    # conocidos (zAlerta-46): set de cod_mensaje ya en BD. Si se pasa → lectura
    # INCREMENTAL (para cuando una página completa ya es conocida y salta los
    # mensajes ya vistos). Si es None → barrido COMPLETO.
    # solo_censo (zAlerta-83 / Tandas CCPL): lista y CUENTA por año SIN descargar
    # nada (ni detalle ni PDFs). Es la "foto" barata previa a decidir las tandas.
    # backfill (zAlerta-84): trae el histórico pendiente sin filtro de año (el
    # filtro real es "conocidos" = lo que YA está completo en GCS, que se salta).
    # Cada corrida avanza de a MAX_DOCS docs (recientes primero) con throttle.
    resultado = {
        "ruc": cfg.ruc,
        "scrapeado_at": ahora_lima().isoformat(),
        "carpetas": [],
        "mensajes": [],
        "endpoints_detectados": [],
        "exito": False,
    }
    DESCARGAS.mkdir(parents=True, exist_ok=True)

    # Capturador de URLs reales que llama el visor (para descubrir los endpoints)
    endpoints_visor: list[str] = []

    def _on_request(req):
        u = req.url
        if "itvisornoti" in u and u not in endpoints_visor:
            endpoints_visor.append(u)
            log(f"   [red] {req.method} {u}")

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=cfg.headless,
            # --disable-gpu ayuda en entornos headless de servidor (Railway).
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        contexto = navegador.new_context(
            locale="es-PE",
            timezone_id="America/Lima",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        contexto.on("request", _on_request)
        page = contexto.new_page()

        try:
            if not login_sol(page, cfg):
                return resultado
            visor_frame = entrar_buzon(page)
            if not visor_frame:
                return resultado

            # ── FASE DESCUBRIMIENTO (breve) ──
            # Los endpoints ya están mapeados; solo damos un margen para que el
            # visor termine de cargar antes de las llamadas JSON.
            log("Esperando estabilización del visor...")
            page.wait_for_timeout(3_000)

            page.wait_for_timeout(1_000)

            # ── CAPTURA DEL ENDPOINT DE DESCARGA (solo si se pide explícitamente) ──
            # Ya conocemos el endpoint real: /visor/bajarArchivo/{cod}/0/0/{ruc}
            # Esta fase de descubrimiento queda desactivada por defecto.
            # Para re-descubrir, exportar SUNAT_DESCUBRIR_DESCARGA=true
            if os.getenv("SUNAT_DESCUBRIR_DESCARGA", "false").lower() == "true":
                log("Modo descubrimiento de descarga activado (clic manual 30s)...")
                descargas_capturadas: list[str] = []

                def _on_download_req(req):
                    u = req.url
                    if "bajararchivo" in u.lower() or "descarg" in u.lower():
                        if u not in descargas_capturadas:
                            descargas_capturadas.append(u)
                            log(f"   [descarga] {req.method} {u}")

                contexto.on("request", _on_download_req)
                if not cfg.headless:
                    page.wait_for_timeout(30_000)
                if descargas_capturadas:
                    resultado["endpoints_descarga"] = descargas_capturadas
                    (DESCARGAS / "endpoints_descarga.txt").write_text(
                        "\n".join(descargas_capturadas), encoding="utf-8")

            # Volcar TODOS los endpoints capturados
            resultado["endpoints_detectados"] = endpoints_visor
            log(f"Endpoints itvisornoti detectados: {len(endpoints_visor)}", "OK")
            for u in endpoints_visor:
                log(f"   → {u}")
            # Guardar también a archivo para análisis cómodo
            (DESCARGAS / "endpoints_visor.txt").write_text(
                "\n".join(endpoints_visor), encoding="utf-8")
            log("Endpoints guardados en descargas/endpoints_visor.txt", "OK")

            # Derivar la base del visor de la URL REAL del iframe
            m = re.match(r"(https://[^/]+/ol-ti-itvisornoti)", visor_frame.url)
            visor_base = m.group(1) if m else VISOR_BASE
            log(f"Base del visor: {visor_base}")

            # api_request_context comparte las cookies/sesión del contexto
            api = contexto.request

            # ── Captura genhtml (zAlerta-94): solo login + pedir el cuerpo/PDF de
            # los avisos de la familia gendocS01Alias, sin barrer el buzón. ──
            if genhtml_pend is not None:
                m = re.match(r"(https?://[^/]+)", visor_base)
                host = m.group(1) if m else "https://ww1.sunat.gob.pe"
                caps = []
                _tot = len(genhtml_pend)
                for _i, it in enumerate(genhtml_pend, 1):
                    if DESCARGA_PAUSA_S > 0:
                        time.sleep(DESCARGA_PAUSA_S)   # throttle anti-ban
                    cuerpo, pdf_bytes, motivo = capturar_cuerpo_genhtml(
                        api, host, visor_base, it.get("url"))
                    caps.append({
                        "id": it.get("id"), "cod_mensaje": it.get("cod_mensaje"),
                        "num_documento": it.get("num_documento"),
                        "cuerpo": cuerpo, "pdf_bytes": pdf_bytes, "motivo": motivo,
                    })
                    log(f"   genhtml {_i}/{_tot} cod={it.get('cod_mensaje')}: "
                        f"cuerpo={'sí' if cuerpo else 'no'} "
                        f"pdf={'sí' if pdf_bytes else 'no'} ({motivo})", "OK")
                resultado["genhtml_capturados"] = caps
                resultado["exito"] = True
                return resultado

            # ── Captura GENERAL por id_archivo (zAlerta-95): POST directo, sin
            # abrir genhtml. Regla universal "id_archivo en JSON → hay PDF". ──
            if pdf_pend is not None:
                caps = []
                _tot = len(pdf_pend)
                for _i, it in enumerate(pdf_pend, 1):
                    if DESCARGA_PAUSA_S > 0:
                        time.sleep(DESCARGA_PAUSA_S)   # throttle anti-ban
                    pdf_bytes, motivo = capturar_pdf_por_id(
                        api, visor_base, it.get("id_archivo"),
                        it.get("sistema"), it.get("cod_mensaje"))
                    caps.append({
                        "id": it.get("id"), "cod_mensaje": it.get("cod_mensaje"),
                        "num_documento": it.get("num_documento"),
                        "pdf_bytes": pdf_bytes, "motivo": motivo,
                    })
                    log(f"   pdf {_i}/{_tot} cod={it.get('cod_mensaje')}: "
                        f"{'sí' if pdf_bytes else 'no'} ({motivo})", "OK")
                resultado["pdf_capturados"] = caps
                resultado["exito"] = True
                return resultado

            # listarCarpetas (informativo; la estructura real la veremos en el JSON)
            carpetas = listar_carpetas(api, visor_base)
            resultado["carpetas"] = carpetas

            def _cod_de(msg: dict):
                return (msg.get("codigoMensaje") or msg.get("codMensaje")
                        or msg.get("codMensa") or msg.get("codigo"))

            # ── Índice carpeta→mensaje (zAlerta-28) ──
            # Barrido LIGERO por cada carpeta real (solo listados) para saber a
            # QUÉ carpeta pertenece cada mensaje (señal oficial de clasificación).
            #
            # zAlerta-48 FASE A: filtrar por codCarpeta específica es LENTO en SUNAT
            # (~17s/llamada vs ~3s con codCarpeta=00 → ~70s del total incremental).
            # En INCREMENTAL lo SALTAMOS: los pocos mensajes nuevos se clasifican
            # por ASUNTO (capa robusta de zAlerta-32); el FULL nocturno (con
            # per-carpeta) es la red de seguridad que re-etiqueta por carpeta.
            carpeta_de: dict[tuple[int, str], dict] = {}
            for c in ([] if conocidos is not None else carpetas):
                if not isinstance(c, dict):
                    continue
                cod_carp = str(c.get("codCarpeta") or "").strip()
                nom_carp = limpiar_html_entities(c.get("nomCarpeta") or "") or None
                if not cod_carp or cod_carp == "00":
                    continue
                for tipo_msj in (1, 2):
                    for m in listar_mensajes(api, visor_base, tipo_msj,
                                             cod_carpeta=cod_carp, conocidos=conocidos):
                        if not isinstance(m, dict):
                            continue
                        cm = _cod_de(m)
                        if cm:
                            carpeta_de.setdefault(
                                (tipo_msj, str(cm)),
                                {"cod": cod_carp, "nom": nom_carp})

            # Estado del 2º PDF de deuda (zAlerta-34 Paso 2). zAlerta-62: deuda
            # HISTÓRICA — se baja desde ANIO_DEUDA_DESDE hasta el año actual (no
            # solo actual+anterior). Solo DEUDA; los informativos siguen sin bajar
            # (dos velocidades). El self-check del PRIMER documento decide si el
            # lote sigue o se aborta (no bajar 100 PDFs equivocados).
            anio_actual = ahora_lima().year
            # zAlerta-72: el año-desde viene POR BUZÓN (anio_desde). Si no se pasa,
            # default año_actual − 2 (arranque rápido). min() garantiza cubrir al
            # menos actual+anterior (nunca menos que el comportamiento base).
            desde = anio_desde if anio_desde else (anio_actual - 2)
            anios_descarga = set(range(min(desde, anio_actual - 1), anio_actual + 1))
            self_check = {"hecho": False, "ok": None, "abortado": False}
            resultado["valorados_intentados"] = 0
            resultado["valorados_descargados"] = 0
            resultado["valorados_pendientes"] = []      # carátula sin goArchivoDescarga / sin PDF
            resultado["valorados_integridad_error"] = []  # numdoc del PDF ≠ numdoc de la fila
            # ── Descarga CONTROLADA (zAlerta-83): censo + límite + throttle ──
            _t0 = time.time()
            ctrl = {"censo": {}, "censo_cods": {}, "docs_bajados": 0,
                    "pdf_bajados": 0, "sinpdf_marcados": 0, "peticiones": 0,
                    "senales_limite": 0, "limite_alcanzado": False}

            # ── Barrido AUTORITATIVO por bandeja (cod_carpeta=00 = todas) ──
            # Igual que antes: garantiza que NO se pierde ningún mensaje. Cada uno
            # se etiqueta con su carpeta (del índice de arriba); si no cae en
            # ninguna carpeta conocida, queda sin carpeta (la ingesta usa OTRO).
            for tipo_msj in (1, 2):
                mensajes = listar_mensajes(api, visor_base, tipo_msj,
                                           cod_carpeta="00", conocidos=conocidos)
                ctrl["peticiones"] += 1   # listar el índice (zAlerta-83 métricas)
                for msg in mensajes:
                    if not isinstance(msg, dict):
                        continue
                    cod_msg = _cod_de(msg)
                    if not cod_msg:
                        continue
                    # Incremental: los ya conocidos no se reprocesan (ni detalle ni
                    # descarga); solo se procesan los NUEVOS. La dedup es red final.
                    if conocidos is not None and str(cod_msg) in conocidos:
                        continue
                    carp = carpeta_de.get((tipo_msj, str(cod_msg)), {})
                    asunto = limpiar_html_entities(
                        msg.get("desAsunto") or msg.get("asunto") or "")
                    urgente = bool(msg.get("indUrg"))
                    fecha_pub = msg.get("fecPublica")
                    fecha_env = (msg.get("fecEnvio") or msg.get("fechaEnvio")
                                 or msg.get("fecPublica"))
                    n_adj = msg.get("cantidadArchAdj", 0)
                    # Censo (zAlerta-83): cuenta docs por año SIN descargar.
                    # zAlerta-85: además guarda los cod_mensaje por año, para que
                    # run_scraper cruce el índice contra BD/GCS (con/sin PDF).
                    _anio = _anio_de(fecha_pub or fecha_env)
                    if _anio:
                        ctrl["censo"][_anio] = ctrl["censo"].get(_anio, 0) + 1
                        ctrl["censo_cods"].setdefault(_anio, []).append(str(cod_msg))

                    # ── FASE 1 (zAlerta-45): DOS VELOCIDADES ──
                    # Clasificamos con el índice (carpeta+asunto), SIN abrir detalle.
                    # Solo la DEUDA (año actual/anterior) baja detalle+PDF ahora; los
                    # INFORMATIVOS se registran con su metadata y su PDF queda pendiente
                    # (se trae en background/bajo demanda). Esto lleva un buzón grande
                    # de 12+ min a ~1-2 min.
                    valorado_tipo = None
                    if _clasificar:
                        tipo_doc, _u, _f = _clasificar(carp.get("nom"), asunto, urgente)
                        vt = _TIPODOC_A_VALORADO.get(tipo_doc)
                        # backfill: la deuda de CUALQUIER año merece su 2º PDF
                        # (valorado); en barrido normal, solo la del rango cubierto.
                        if vt and (backfill
                                   or _anio_de(fecha_pub or fecha_env) in anios_descarga):
                            valorado_tipo = vt
                    es_deuda = valorado_tipo is not None and not self_check["abortado"]

                    # zAlerta-82/83: DESCARGAR TODO, pero CONTROLADO. Se baja el
                    # detalle (cuerpo fiel) + adjuntos SOLO si: (a) el año está en el
                    # rango cubierto del buzón (recientes primero) y (b) no se superó
                    # el límite de docs por barrido. Con THROTTLE entre peticiones.
                    # Lo que queda fuera → pdf_pendiente (se trae al ampliar el rango).
                    # backfill: sin filtro de año (todo el histórico pendiente);
                    # el skip lo hace "conocidos" (lo ya completo en GCS).
                    en_rango = backfill or (_anio is None) or (_anio in anios_descarga)
                    # ¿espera un PDF real (caro) o es solo cuerpo (barato)?
                    espera_pdf = bool((n_adj and int(n_adj) > 0) or es_deuda)
                    if backfill:
                        # Límites SEPARADOS (zAlerta-85): los PDF caros y el marcar
                        # "sin adjunto" barato avanzan su propio backlog por corrida.
                        if espera_pdf:
                            puede_bajar = ctrl["pdf_bajados"] < MAX_PDF_POR_CORRIDA
                        else:
                            puede_bajar = ctrl["sinpdf_marcados"] < MAX_SINPDF_POR_CORRIDA
                        if not puede_bajar:
                            ctrl["limite_alcanzado"] = True   # queda para otra corrida
                    else:
                        bajo_limite = ctrl["docs_bajados"] < MAX_DOCS_BARRIDO
                        # En barrido normal la DEUDA está exenta del límite (siempre
                        # baja; los informativos esperan). solo_censo: nada baja.
                        deuda_exenta = es_deuda
                        puede_bajar = (not solo_censo) and en_rango and (bajo_limite or deuda_exenta)
                        if not solo_censo and en_rango and not bajo_limite and not deuda_exenta:
                            ctrl["limite_alcanzado"] = True   # UI: "reduce años"

                    detalle = None
                    pdfs = []
                    adj_vacio = False   # SUNAT sirvió los adjuntos vacíos (zAlerta-86)
                    if puede_bajar:
                        if DESCARGA_PAUSA_S > 0:
                            time.sleep(DESCARGA_PAUSA_S)   # throttle anti-ban
                        detalle = obtener_detalle(api, visor_base, cod_msg, tipo_msj)
                        ctrl["peticiones"] += 1
                        if detalle:
                            pdfs, adj_vacio = descargar_adjuntos(api, visor_base, detalle, cod_msg, cfg.ruc)
                            ctrl["peticiones"] += 1
                            ctrl["docs_bajados"] += 1
                            if backfill:
                                if espera_pdf:
                                    ctrl["pdf_bajados"] += 1
                                else:
                                    ctrl["sinpdf_marcados"] += 1

                    item = {
                        "tipo_msj": tipo_msj,
                        "cod_mensaje": cod_msg,
                        "cod_carpeta": carp.get("cod"),
                        "nombre_carpeta": carp.get("nom"),
                        "asunto": asunto,
                        "fecha_envio": fecha_env,
                        "fecha_publica": fecha_pub,
                        "urgente": urgente,
                        "destacado": bool(msg.get("indDesta")),
                        "cant_adjuntos": n_adj,
                        "texto_html": (detalle or {}).get("msjMensaje"),
                        "raw": msg,
                        "detalle": detalle,
                        "pdfs": pdfs,
                        # Adjunto que existe pero NO se pudo bajar (zAlerta-82: se
                        # intenta bajar TODO; pendiente solo si falló la descarga).
                        "pdf_pendiente": bool(n_adj) and not pdfs,
                        "capturado_at": ahora_lima().isoformat(),
                    }

                    # ── 2º PDF de DEUDA (zAlerta-34/35) — solo para deuda ──
                    if es_deuda and detalle:
                        resultado["valorados_intentados"] += 1
                        num_doc = _num_documento_de(asunto)
                        body, motivo = descargar_documento_real(
                            api, visor_base, cod_msg, detalle)
                        ctrl["peticiones"] += 1
                        if not body:
                            # Sin PDF: NO adivinar. Marcar pendiente y seguir.
                            resultado["valorados_pendientes"].append(
                                {"cod_mensaje": cod_msg, "num_documento": num_doc,
                                 "motivo": motivo})
                            # zAlerta-86: distinguir NO-DISPONIBLE (carátula sin
                            # goArchivo → SUNAT no ofrece la resolución) de fallo
                            # transitorio (caratula_error/post_sin_pdf → reintentar).
                            if motivo in ("sin_caratula", "sin_goarchivo"):
                                item["valorado_no_disponible"] = True
                        else:
                            txt = texto_pdf(body)
                            # SELF-CHECK: solo para DEUDA con monto (OP/Multa/REC/
                            # Fracc/Determ). PAGO y ESQUELA no traen "monto de deuda"
                            # y NO deben abortar el lote (zAlerta-81).
                            _es_deuda_monto = valorado_tipo.value not in (
                                "pago", "esquela_omiso")
                            # SELF-CHECK obligatorio en el PRIMER documento de DEUDA.
                            if _es_deuda_monto and not self_check["hecho"]:
                                self_check["hecho"] = True
                                self_check["ok"] = _tiene_monto(txt)
                                resultado["self_check"] = {
                                    "ok": self_check["ok"], "cod_mensaje": cod_msg,
                                    "num_documento": num_doc, "texto_muestra": txt[:1500]}
                                if not self_check["ok"]:
                                    self_check["abortado"] = True
                                    log("SELF-CHECK FALLÓ: el 1er PDF de deuda no "
                                        "trae monto. Abortando el lote de valorados.",
                                        "ERROR")
                            # CHECK DE INTEGRIDAD: el nº de documento de la fila debe
                            # aparecer en el texto del PDF bajado. zAlerta-87: SUNAT
                            # usa el número CON y SIN guiones inconsistentemente
                            # (1240020011643 vs 124-002-0011643) → normalizar (solo
                            # dígitos) antes de comparar. NO debilita: mismos dígitos
                            # = mismo documento; solo tolera el formato.
                            _esp = _solo_digitos(num_doc)
                            hallados = _RE_NUM_DOC.findall(txt)
                            # Candidatos: secuencias de dígitos contiguas (con o sin
                            # guiones) en el PDF — cubre ambos formatos sin unir
                            # números separados por espacios (evita falsos positivos).
                            _cands = re.findall(r"\d[\d\-]{7,}\d", txt)
                            integ_ok = (
                                not num_doc
                                or (num_doc in txt)
                                or (bool(_esp) and any(
                                    _solo_digitos(x) == _esp for x in _cands)))
                            if not self_check["abortado"] and not integ_ok:
                                log(f"INTEGRIDAD: PDF cod={cod_msg} NO contiene "
                                    f"{num_doc}; hallados={hallados[:3]}. No se guarda.",
                                    "ERROR")
                                resultado["valorados_integridad_error"].append(
                                    {"cod_mensaje": cod_msg, "esperado": num_doc,
                                     "hallados": hallados[:3]})
                            elif not self_check["abortado"]:
                                item["valorado"] = {
                                    "tipo_valorado": valorado_tipo.value,
                                    "num_documento": num_doc,
                                    "pdf_bytes": body,
                                    "pdf_texto": txt,
                                }
                                resultado["valorados_descargados"] += 1

                    # Revisado SIN adjunto (zAlerta-85): se abrió el detalle y no
                    # hay nada más que bajar (SUNAT declara 0 adjuntos, no es deuda
                    # con 2º PDF pendiente). Se marca para que el backfill NO lo
                    # re-visite; el cuerpo fiel ya quedó capturado (texto_html).
                    # Marca si: no es deuda, se abrió el detalle, no hubo PDF, y
                    # SUNAT no tiene adjunto (n_adj=0) O lo sirvió vacío (adj_vacio).
                    item["revisado_sin_adjunto"] = bool(
                        detalle is not None and not pdfs and not item.get("valorado")
                        and not es_deuda
                        and (not (n_adj and int(n_adj) > 0) or adj_vacio))

                    resultado["mensajes"].append(item)
                    # Pausa SOLO tras descargas de deuda (no martillear). Los
                    # informativos no bajan binario → sin pausa → buzón rápido.
                    if es_deuda:
                        time.sleep(random.uniform(0.5, 1.5))

            resultado["exito"] = True
            # ── Métricas del barrido + censo (zAlerta-83/85) ──
            resultado["censo"] = {int(a): n for a, n in ctrl["censo"].items()}
            # cod_mensaje por año (para el censo detallado: cruce índice × BD/GCS).
            resultado["censo_cods"] = {int(a): c for a, c in ctrl["censo_cods"].items()}
            resultado["metricas"] = {
                "peticiones": ctrl["peticiones"],
                "duracion_seg": int(time.time() - _t0),
                "docs_procesados": len(resultado["mensajes"]),
                "pdfs_descargados": ctrl["docs_bajados"],
                # zAlerta-85: desglose PDF caro vs "sin adjunto" barato (backfill).
                "pdf_bajados": ctrl["pdf_bajados"],
                "sinpdf_marcados": ctrl["sinpdf_marcados"],
                "senales_limite": ctrl["senales_limite"],
                "limite_alcanzado": ctrl["limite_alcanzado"],
            }
            log(f"Scraping completo: {len(resultado['mensajes'])} mensajes; "
                f"{ctrl['docs_bajados']} PDF ({ctrl['pdf_bajados']} bf-pdf / "
                f"{ctrl['sinpdf_marcados']} bf-sinpdf), {ctrl['peticiones']} peticiones, "
                f"{resultado['metricas']['duracion_seg']}s"
                f"{' [LÍMITE alcanzado]' if ctrl['limite_alcanzado'] else ''}.", "OK")

        finally:
            contexto.close()
            navegador.close()

    return resultado


def main() -> None:
    log("═══ alerta.pe — scraper SUNAT (MVP Playwright) ═══")
    cfg = SunatConfig()
    cfg.validar()

    resultado = scrapear_ruc(cfg)

    # Guardar resultado en JSON (texto + metadata) para inspección
    DESCARGAS.mkdir(parents=True, exist_ok=True)
    salida = DESCARGAS / f"resultado_{cfg.ruc}_{ahora_lima().strftime('%Y%m%d_%H%M%S')}.json"
    salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Resultado guardado en: {salida}")

    if resultado["exito"]:
        log("✅ PROTOTIPO VALIDADO — Playwright resolvió el flujo OAuth de SUNAT.", "OK")
    else:
        log("❌ No se completó. Revisar logs y capturas en ./descargas/", "ERROR")
        sys.exit(2)


if __name__ == "__main__":
    main()