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


# ─────────────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────────────
def _evidencia(page: Page, etiqueta: str) -> None:
    """Vuelca screenshot + HTML + URL actual para diagnóstico."""
    DESCARGAS.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(DESCARGAS / f"diag_{etiqueta}.png"), full_page=True)
        (DESCARGAS / f"diag_{etiqueta}.html").write_text(page.content(), encoding="utf-8")
        log(f"   [diag] URL actual: {page.url}")
        log(f"   [diag] evidencia: diag_{etiqueta}.png / diag_{etiqueta}.html")
    except Exception as e:
        log(f"   [diag] no se pudo capturar evidencia: {e}", "WARN")


def login_sol(page: Page, cfg: SunatConfig) -> bool:
    """Loguea en SOL. Chromium sigue toda la cadena OAuth automáticamente.

    Versión diagnóstica: reporta la URL real en cada paso en vez de
    fallar a ciegas, para poder ajustar selectores y regex.
    """
    log("Abriendo portal de login SOL...")
    page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=60_000)
    log(f"   URL tras goto inicial: {page.url}")
    _evidencia(page, "01_login_inicial")

    # El formulario de SOL pide RUC + Usuario + Clave.
    # Probamos varios selectores candidatos porque SUNAT cambia los IDs.
    sel_ruc = ["#txtRuc", "input[name='txtRuc']", "#ruc", "input[name='ruc']"]
    sel_usr = ["#txtUsuario", "input[name='txtUsuario']", "#usuario", "input[name='usuario']"]
    sel_clave = ["#txtContrasena", "input[name='txtContrasena']", "#clave",
                 "input[name='clave']", "input[type='password']"]
    sel_btn = ["#btnAceptar", "button[type='submit']", "input[type='submit']", "#submit"]

    def llenar(selectores, valor, nombre) -> bool:
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

    ok_ruc = llenar(sel_ruc, cfg.ruc, "RUC")
    ok_usr = llenar(sel_usr, cfg.usuario_sol, "usuario")
    ok_clave = llenar(sel_clave, cfg.clave_sol, "clave")

    if not (ok_ruc and ok_usr and ok_clave):
        log("No se pudieron llenar todos los campos del login.", "ERROR")
        _evidencia(page, "02_campos_fallidos")
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


def listar_mensajes(api: APIRequestContext, visor_base: str,
                    tipo_msj: int, cod_carpeta: str = "00") -> list:
    """Lista mensajes de una bandeja. Path real: /visor/listNotiMenPag

    SUNAT usa tipoMsj para distinguir bandejas:
      tipoMsj=1  → una bandeja (mensajes)
      tipoMsj=2  → otra bandeja (notificaciones)
    codCarpeta=00 = todas las carpetas de esa bandeja.
    """
    url = (f"{visor_base}/visor/listNotiMenPag"
           f"?tipoMsj={tipo_msj}&codCarpeta={cod_carpeta}&codEtiqueta="
           f"&page=1&des_asunto=&codMensaje=&tipoOrden=NADA")
    data = fetch_json(api, url, f"listNotiMenPag(tipoMsj={tipo_msj})")
    if not data:
        return []
    mensajes = data.get("rows", data) if isinstance(data, dict) else data
    log(f"  Bandeja tipoMsj={tipo_msj}: {len(mensajes)} mensajes", "OK")
    return mensajes


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
# Orquestación
# ─────────────────────────────────────────────────────────────────────
def scrapear_ruc(cfg: SunatConfig) -> dict:
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
            args=["--no-sandbox", "--disable-dev-shm-usage"],
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

            # Iterar las DOS bandejas que usa el visor: tipoMsj=1 y tipoMsj=2
            for tipo_msj in (1, 2):
                mensajes = listar_mensajes(api, visor_base, tipo_msj, cod_carpeta="00")
                for msg in mensajes:
                    if not isinstance(msg, dict):
                        continue
                    cod_msg = (msg.get("codigoMensaje") or msg.get("codMensaje")
                               or msg.get("codMensa") or msg.get("codigo"))
                    if not cod_msg:
                        continue
                    detalle = obtener_detalle(api, visor_base, cod_msg, tipo_msj)
                    pdfs = []
                    if detalle:
                        pdfs = descargar_adjuntos(api, visor_base, detalle, cod_msg, cfg.ruc)
                    resultado["mensajes"].append({
                        "tipo_msj": tipo_msj,
                        "cod_mensaje": cod_msg,
                        "asunto": limpiar_html_entities(
                            msg.get("desAsunto") or msg.get("asunto") or ""),
                        "fecha_envio": (msg.get("fecEnvio") or msg.get("fechaEnvio")
                                        or msg.get("fecPublica")),
                        "fecha_publica": msg.get("fecPublica"),
                        "urgente": bool(msg.get("indUrg")),
                        "destacado": bool(msg.get("indDesta")),
                        "cant_adjuntos": msg.get("cantidadArchAdj", 0),
                        "texto_html": (detalle or {}).get("msjMensaje"),
                        "raw": msg,
                        "detalle": detalle,
                        "pdfs": pdfs,
                        "capturado_at": ahora_lima().isoformat(),
                    })
                    time.sleep(random.uniform(0.5, 1.5))

            resultado["exito"] = True
            log(f"Scraping completo: {len(resultado['mensajes'])} mensajes capturados.", "OK")

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