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

import json
import os
import re
import sys
import time
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, Page, APIRequestContext, TimeoutError as PWTimeout

# Clasificación (para decidir qué documentos son DEUDA → 2º PDF, zAlerta-34).
# Import perezoso-seguro: si faltara (contexto sin DB), el scraper sigue
# funcionando para el índice; solo se omite la valoración.
try:
    from clasificacion import clasificar as _clasificar
    from models import TIPODOC_A_VALORADO as _TIPODOC_A_VALORADO
except Exception:   # pragma: no cover
    _clasificar = None
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
                       cod_mensaje: str, ruc: str) -> list:
    """Descarga los adjuntos de un mensaje.

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
        return guardados

    destino = DESCARGAS / ruc / str(cod_mensaje)
    destino.mkdir(parents=True, exist_ok=True)

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
        if not descargado:
            log(f"    No se pudo descargar {nombre} (codArchivo={cod_archivo}) — "
                f"status={ultimo_status} ct={ultimo_ct} bytes={ultimo_len}", "WARN")
            # Guardar la respuesta cruda para diagnosticar (¿HTML? ¿generador?)
            try:
                if ultimo_len:
                    raw_resp = api.get(urls[0], timeout=30_000).body()
                    (destino / f"RAW_{cod_archivo}.bin").write_bytes(raw_resp)
                    log(f"    Respuesta cruda guardada: RAW_{cod_archivo}.bin", "INFO")
            except Exception:
                pass
            guardados.append(f"PENDIENTE:codArchivo={cod_archivo}:{nombre}")
    return guardados


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


def _id_deuda_de_caratula(api: APIRequestContext, host: str, detalle: dict):
    """Obtiene (idArchivo, sistema, codMensaje) del documento de DEUDA desde la
    CARÁTULA (zAlerta-35). ÚNICA fuente: el `goArchivoDescarga(idArchivo, sistema,
    codMensaje)` que SUNAT pone en la carátula. SIN offset (el offset NO es
    constante: Multas −1, Órdenes de Pago −2, etc. → restar daría el archivo
    EQUIVOCADO). Devuelve (idar, sis, cmsg) o (None, motivo, None)."""
    car = detalle.get("url")
    if not car:
        return None, "sin_caratula", None
    url = car if car.startswith("http") else host + ("" if car.startswith("/") else "/") + car
    try:
        html = api.get(url, timeout=40_000).text()
    except Exception as e:
        log(f"    carátula no cargó: {e}", "WARN")
        return None, "caratula_error", None
    m = _RE_GOARCHIVO.search(html or "")
    if not m:
        return None, "sin_goarchivo", None
    return m.group(1), m.group(2), m.group(3)


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
                 solo_censo: bool = False) -> dict:
    # conocidos (zAlerta-46): set de cod_mensaje ya en BD. Si se pasa → lectura
    # INCREMENTAL (para cuando una página completa ya es conocida y salta los
    # mensajes ya vistos). Si es None → barrido COMPLETO.
    # solo_censo (zAlerta-83 / Tandas CCPL): lista y CUENTA por año SIN descargar
    # nada (ni detalle ni PDFs). Es la "foto" barata previa a decidir las tandas.
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
            ctrl = {"censo": {}, "docs_bajados": 0, "peticiones": 0,
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
                    _anio = _anio_de(fecha_pub or fecha_env)
                    if _anio:
                        ctrl["censo"][_anio] = ctrl["censo"].get(_anio, 0) + 1

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
                        if vt and _anio_de(fecha_pub or fecha_env) in anios_descarga:
                            valorado_tipo = vt
                    es_deuda = valorado_tipo is not None and not self_check["abortado"]

                    # zAlerta-82/83: DESCARGAR TODO, pero CONTROLADO. Se baja el
                    # detalle (cuerpo fiel) + adjuntos SOLO si: (a) el año está en el
                    # rango cubierto del buzón (recientes primero) y (b) no se superó
                    # el límite de docs por barrido. Con THROTTLE entre peticiones.
                    # Lo que queda fuera → pdf_pendiente (se trae al ampliar el rango).
                    en_rango = (_anio is None) or (_anio in anios_descarga)
                    bajo_limite = ctrl["docs_bajados"] < MAX_DOCS_BARRIDO
                    # solo_censo: NADA se baja (ni deuda); solo se lista y cuenta.
                    puede_bajar = (not solo_censo) and en_rango and (bajo_limite or es_deuda)
                    if not solo_censo and en_rango and not bajo_limite and not es_deuda:
                        ctrl["limite_alcanzado"] = True   # UI: "reduce años"
                    detalle = None
                    pdfs = []
                    if puede_bajar:
                        if DESCARGA_PAUSA_S > 0:
                            time.sleep(DESCARGA_PAUSA_S)   # throttle anti-ban
                        detalle = obtener_detalle(api, visor_base, cod_msg, tipo_msj)
                        ctrl["peticiones"] += 1
                        if detalle:
                            pdfs = descargar_adjuntos(api, visor_base, detalle, cod_msg, cfg.ruc)
                            ctrl["peticiones"] += 1
                            ctrl["docs_bajados"] += 1

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
                            # aparecer en el texto del PDF bajado.
                            integ_ok = (not num_doc) or (num_doc in txt)
                            if not self_check["abortado"] and not integ_ok:
                                hallados = _RE_NUM_DOC.findall(txt)
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

                    resultado["mensajes"].append(item)
                    # Pausa SOLO tras descargas de deuda (no martillear). Los
                    # informativos no bajan binario → sin pausa → buzón rápido.
                    if es_deuda:
                        time.sleep(random.uniform(0.5, 1.5))

            resultado["exito"] = True
            # ── Métricas del barrido + censo (zAlerta-83) ──
            resultado["censo"] = {int(a): n for a, n in ctrl["censo"].items()}
            resultado["metricas"] = {
                "peticiones": ctrl["peticiones"],
                "duracion_seg": int(time.time() - _t0),
                "docs_procesados": len(resultado["mensajes"]),
                "pdfs_descargados": ctrl["docs_bajados"],
                "senales_limite": ctrl["senales_limite"],
                "limite_alcanzado": ctrl["limite_alcanzado"],
            }
            log(f"Scraping completo: {len(resultado['mensajes'])} mensajes; "
                f"{ctrl['docs_bajados']} PDF, {ctrl['peticiones']} peticiones, "
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