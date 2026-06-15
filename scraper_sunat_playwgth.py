"""
scraper_sunat_hibrido.py — alerta.pe Buzón (HÍBRIDO, ARCHIVO ÚNICO)
═══════════════════════════════════════════════════════════════════════
Todo en un solo archivo, sin imports entre módulos.

  FASE 1 (login)    → Playwright (Chromium real). Pasa el F5 BIG-IP.
                      Login + entrada al buzón. Exporta cookies + visor base.
  FASE 2 (scraping) → httpx con esas cookies. Rápido, sin navegador.

En producción: la sesión (cookies) se reusa ~30-40 min; cada consulta
extra es solo FASE 2 (httpx puro, segundos).

Uso:
  pip install playwright httpx python-dotenv
  playwright install chromium
  python scraper_sunat_hibrido.py
  (.env con SUNAT_RUC, SUNAT_USUARIO, SUNAT_CLAVE, SUNAT_HEADLESS)

Zona horaria: SIEMPRE America/Lima.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────
TZ_LIMA = ZoneInfo("America/Lima")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def log(msg: str, nivel: str = "INFO") -> None:
    print(f"[{ahora_lima().strftime('%d/%m/%Y %H:%M:%S')}] [{nivel}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────
@dataclass
class SunatConfig:
    ruc: str = field(default_factory=lambda: os.getenv("SUNAT_RUC", ""))
    usuario_sol: str = field(default_factory=lambda: os.getenv("SUNAT_USUARIO", ""))
    clave_sol: str = field(default_factory=lambda: os.getenv("SUNAT_CLAVE", ""))
    headless: bool = field(default_factory=lambda: os.getenv("SUNAT_HEADLESS", "true").lower() == "true")

    def validar(self) -> None:
        faltan = [k for k, v in {
            "SUNAT_RUC": self.ruc, "SUNAT_USUARIO": self.usuario_sol,
            "SUNAT_CLAVE": self.clave_sol,
        }.items() if not v]
        if faltan:
            log(f"Faltan variables de entorno: {', '.join(faltan)}", "ERROR")
            sys.exit(1)


URL_LOGIN = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
VISOR_BASE_DEFAULT = "https://ww1.sunat.gob.pe/ol-ti-itvisornoti"
DESCARGAS = Path("./descargas_hibrido")

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/120.0.0.0 Safari/537.36")


# ═════════════════════════════════════════════════════════════════════
# FASE 1 — Login + buzón con Playwright (contexto validado), exporta cookies
# ═════════════════════════════════════════════════════════════════════
@dataclass
class SunatSession:
    ruc: str
    cookies: dict
    visor_base: str
    creada_at: datetime


def _evidencia(page: Page, etiqueta: str) -> None:
    DESCARGAS.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(DESCARGAS / f"diag_{etiqueta}.png"), full_page=True)
        log(f"   [diag] URL: {page.url[:90]}")
    except Exception:
        pass


def _login_playwright(page: Page, cfg: SunatConfig) -> bool:
    """Login SOL. Diagnóstico + espera escalonada (plan ChatGPT)."""
    # Espía de navegación: ver exactamente en qué paso del OAuth queda
    page.on("framenavigated",
            lambda fr: log(f"   NAV: {fr.url[:95]}") if fr == page.main_frame else None)

    log("Abriendo portal de login SOL...")
    page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=60_000)

    # Esperar a que el formulario de login cargue
    campo_listo = False
    for sel_espera in ["#txtRuc", "input[name='txtRuc']", "input[type='password']"]:
        try:
            page.wait_for_selector(sel_espera, timeout=20_000, state="visible")
            campo_listo = True
            break
        except PWTimeout:
            continue
    if not campo_listo:
        log("El formulario de login no cargó a tiempo.", "ERROR")
        _evidencia(page, "form_no_cargo")
        return False

    sel_ruc = ["#txtRuc", "input[name='txtRuc']"]
    sel_usr = ["#txtUsuario", "input[name='txtUsuario']"]
    sel_clave = ["#txtContrasena", "input[name='txtContrasena']", "input[type='password']"]
    sel_btn = ["#btnAceptar", "button[type='submit']", "input[type='submit']"]

    def llenar(selectores, valor) -> bool:
        for s in selectores:
            try:
                el = page.query_selector(s)
                if el:
                    el.fill(valor)
                    return True
            except Exception:
                continue
        return False

    if not (llenar(sel_ruc, cfg.ruc) and llenar(sel_usr, cfg.usuario_sol)
            and llenar(sel_clave, cfg.clave_sol)):
        log("No se pudieron llenar los campos de login.", "ERROR")
        _evidencia(page, "campos_fallidos")
        return False
    log(f"Credenciales ingresadas (RUC {cfg.ruc}, usuario {cfg.usuario_sol})")

    # CLAVE (diagnóstico ChatGPT): envolver el click en expect_navigation.
    # Sin esto, click() retorna de inmediato y wait_for_url se ejecuta sobre
    # una navegación a medio inicializar → race condition → página fallback.
    boton = None
    for s in sel_btn:
        try:
            el = page.query_selector(s)
            if el:
                boton = el
                break
        except Exception:
            continue
    if not boton:
        log("No se encontró el botón de login.", "ERROR")
        return False

    try:
        # expect_navigation espera a que la navegación disparada por el click
        # (POST + cadena de redirects OAuth) se complete antes de seguir.
        with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
            boton.click()
        log(f"   navegación tras click completa. URL: {page.url[:80]}...")
    except PWTimeout:
        log("   expect_navigation timeout; continuando con esperas...", "WARN")
    except Exception as e:
        log(f"   expect_navigation: {e}; continuando...", "WARN")

    # Puede que la cadena OAuth siga con más redirects. Esperar el code y dar
    # margen para que el navegador complete el canje (sin forzar nada).
    try:
        page.wait_for_url(re.compile(r"code=", re.I), timeout=15_000)
        log("   code recibido; esperando canje automático...")
    except PWTimeout:
        pass

    # Dump de cookies para diagnóstico (lo pidió ChatGPT)
    try:
        ck = page.context.cookies()
        nombres = sorted({c["name"] for c in ck})
        log(f"   cookies actuales ({len(ck)}): {nombres}")
        # Guardar cookies completas a JSON para comparar con el caso éxito
        import json as _json
        with open("cookies_FAIL.json", "w", encoding="utf-8") as _f:
            _json.dump(ck, _f, ensure_ascii=False, indent=2)
        log("   [diag] cookies guardadas en cookies_FAIL.json", "INFO")
    except Exception:
        pass

    # Esperar el canje automático del code (sin forzar). Damos margen amplio.
    try:
        page.wait_for_url(re.compile(r"(AutenticaMenuInternet|MenuInternet)", re.I),
                          timeout=10_000)
    except PWTimeout:
        pass

    # ── ÚLTIMA IDEA: forzar el GET a e-menu con el code ──
    # En el script puro, el navegador hace solo el GET a
    #   e-menu/AutenticaMenuInternet.htm?state=...&code=...
    # que cruza el code de api-seguridad → e-menu y crea la sesión.
    # En el híbrido ese GET no se dispara. Lo forzamos a mano.
    if "code=" in page.url.lower() and "api-seguridad" in page.url.lower():
        log("   Canje no disparó solo; forzando GET a e-menu con el code...", "WARN")
        # Extraer state y code de la URL actual
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(page.url).query)
        code = qs.get("code", [""])[0]
        state = qs.get("state", [""])[0]
        if code:
            url_canje = (f"https://e-menu.sunat.gob.pe/cl-ti-itmenu/"
                         f"AutenticaMenuInternet.htm?state={state}&code={code}")
            try:
                page.goto(url_canje, wait_until="domcontentloaded", timeout=30_000)
                log(f"   GET canje hecho. URL: {page.url[:80]}...")
            except Exception as e:
                log(f"   error en GET canje: {e}", "WARN")

    # Validar sesión por el elemento REAL del menú autenticado
    try:
        page.wait_for_selector("#aOpcionBuzon", timeout=30_000, state="attached")
        log("Login OK — sesión válida (#aOpcionBuzon presente).", "OK")
        return True
    except PWTimeout:
        log("Login falló — #aOpcionBuzon no apareció.", "ERROR")
        if "code=" in page.url.lower():
            DESCARGAS.mkdir(parents=True, exist_ok=True)
            try:
                (DESCARGAS / "oauth_stuck.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
        _evidencia(page, "login_sin_sesion")
        return False


def _entrar_buzon(page: Page):
    """Clic real en #aOpcionBuzon, devuelve el frame del visor."""
    log("Entrando al buzón (#aOpcionBuzon)...")
    try:
        page.wait_for_selector("#aOpcionBuzon", timeout=20_000, state="attached")
    except PWTimeout:
        log("No se encontró #aOpcionBuzon.", "ERROR")
        _evidencia(page, "sin_aOpcionBuzon")
        return None

    try:
        page.eval_on_selector("#aOpcionBuzon", "el => el.click()")
    except Exception:
        try:
            page.evaluate("ejecuta('MenuInternet.htm?action=buzon', false, '', '', '')")
        except Exception as e:
            log(f"No se pudo disparar el buzón: {e}", "ERROR")
            return None

    log("Esperando el iframe del visor (itvisornoti)...")
    visor_frame = None
    for _ in range(30):
        page.wait_for_timeout(1_000)
        for fr in page.frames:
            if "itvisornoti" in (fr.url or "") or "visor/master" in (fr.url or ""):
                visor_frame = fr
                break
        if visor_frame:
            break

    if not visor_frame:
        log("El iframe del visor no apareció.", "ERROR")
        _evidencia(page, "sin_iframe_visor")
        return None

    log(f"Visor cargado: {visor_frame.url[:80]}...", "OK")
    page.wait_for_timeout(2_000)
    return visor_frame


def login_y_exportar_cookies(cfg: SunatConfig) -> SunatSession | None:
    """FASE 1: login + buzón con contexto Playwright, exporta cookies."""
    log("FASE 1: Login con Playwright...")
    DESCARGAS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=cfg.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        contexto = navegador.new_context(
            locale="es-PE", timezone_id="America/Lima", user_agent=UA_CHROME,
        )
        page = contexto.new_page()
        try:
            if not _login_playwright(page, cfg):
                return None
            visor_frame = _entrar_buzon(page)
            if not visor_frame:
                return None

            page.wait_for_timeout(2_000)
            m = re.match(r"(https://[^/]+/ol-ti-itvisornoti)", visor_frame.url)
            visor_base = m.group(1) if m else VISOR_BASE_DEFAULT

            cookies = {c["name"]: c["value"] for c in contexto.cookies()}
            log(f"{len(cookies)} cookies exportadas. Visor: {visor_base}", "OK")
            return SunatSession(cfg.ruc, cookies, visor_base, ahora_lima())
        finally:
            contexto.close()
            navegador.close()


# ═════════════════════════════════════════════════════════════════════
# FASE 2 — Scraping con httpx (rápido)
# ═════════════════════════════════════════════════════════════════════
def _client_desde_sesion(sesion: SunatSession) -> httpx.Client:
    client = httpx.Client(follow_redirects=True, timeout=30.0)
    for nombre, valor in sesion.cookies.items():
        try:
            client.cookies.set(nombre, valor, domain=".sunat.gob.pe")
        except Exception:
            pass
    return client


def _headers(base: str) -> dict:
    return {
        "User-Agent": UA_CHROME,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-PE,es;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base}/visor/master",
    }


def _fetch_json(client, url, base, etiqueta):
    try:
        r = client.get(url, headers=_headers(base))
        if r.status_code != 200:
            log(f"   {etiqueta}: status {r.status_code}", "WARN")
            return None
        return r.json()
    except Exception as e:
        log(f"   {etiqueta}: error {e}", "WARN")
        return None


def _listar_mensajes(client, base, tipo_msj):
    ts = int(time.time() * 1000)
    url = (f"{base}/visor/listNotiMenPag?tipoMsj={tipo_msj}&codCarpeta=00"
           f"&codEtiqueta=&page=1&des_asunto=&codMensaje=&tipoOrden=NADA&_={ts}")
    data = _fetch_json(client, url, base, f"listNotiMenPag(tipoMsj={tipo_msj})")
    if not data:
        return []
    msgs = data.get("rows", data) if isinstance(data, dict) else data
    log(f"   bandeja tipoMsj={tipo_msj}: {len(msgs)} mensajes", "OK")
    return msgs


def _obtener_detalle(client, base, cod, tipo_msj):
    ts = int(time.time() * 1000)
    url = (f"{base}/visor/obtenerDetalleNotiMen?codigoMensaje={cod}"
           f"&tipoMsj={tipo_msj}&_={ts}")
    return _fetch_json(client, url, base, "obtenerDetalleNotiMen")


def _descargar_pdf(client, base, cod_archivo, ruc, nombre, cod_mensaje):
    url = f"{base}/visor/bajarArchivo/{cod_archivo}/0/0/{ruc}"
    try:
        r = client.get(url, headers=_headers(base))
        body = r.content
        if r.status_code == 200 and len(body) > 1000 and body[:4] == b"%PDF":
            destino = DESCARGAS / ruc / str(cod_mensaje)
            destino.mkdir(parents=True, exist_ok=True)
            if not nombre.lower().endswith(".pdf"):
                nombre += ".pdf"
            (destino / nombre).write_bytes(body)
            log(f"     PDF guardado: {nombre} ({len(body)} bytes)", "OK")
            return str(destino / nombre)
        log(f"     PDF {nombre}: status {r.status_code}, {len(body)}b, no válido", "WARN")
    except Exception as e:
        log(f"     error descargando {nombre}: {e}", "WARN")
    return None


def scrapear_con_sesion(sesion: SunatSession) -> dict:
    t0 = time.time()
    resultado = {
        "ruc": sesion.ruc,
        "scrapeado_at": ahora_lima().isoformat(),
        "mensajes": [],
        "exito": False,
    }
    base = sesion.visor_base
    client = _client_desde_sesion(sesion)
    try:
        for tipo_msj in (1, 2):
            for msg in _listar_mensajes(client, base, tipo_msj):
                if not isinstance(msg, dict):
                    continue
                cod = msg.get("codMensaje") or msg.get("codigoMensaje")
                if not cod:
                    continue
                detalle = _obtener_detalle(client, base, cod, tipo_msj)
                pdfs = []
                if detalle:
                    for a in (detalle.get("listAttach") or []):
                        if a.get("codArchivo") and a.get("nomArchivo"):
                            p = _descargar_pdf(client, base, a["codArchivo"],
                                               sesion.ruc, a["nomArchivo"], cod)
                            if p:
                                pdfs.append(p)
                resultado["mensajes"].append({
                    "tipo_msj": tipo_msj,
                    "cod_mensaje": cod,
                    "asunto": msg.get("desAsunto") or msg.get("asunto"),
                    "fecha_envio": msg.get("fecEnvio") or msg.get("fecPublica"),
                    "cant_adjuntos": msg.get("cantidadArchAdj", 0),
                    "texto_html": (detalle or {}).get("msjMensaje"),
                    "pdfs": pdfs,
                    "capturado_at": ahora_lima().isoformat(),
                })
        resultado["exito"] = True
        resultado["segundos_fase2"] = round(time.time() - t0, 1)
        log(f"FASE 2 completa: {len(resultado['mensajes'])} mensajes "
            f"en {resultado['segundos_fase2']}s (httpx).", "OK")
    finally:
        client.close()
    return resultado


# ═════════════════════════════════════════════════════════════════════
def main():
    log("═══ alerta.pe — scraper SUNAT HÍBRIDO (archivo único) ═══")
    cfg = SunatConfig()
    cfg.validar()

    t_login = time.time()
    sesion = login_y_exportar_cookies(cfg)
    if not sesion:
        log("❌ FASE 1 (login) falló.", "ERROR")
        sys.exit(2)
    seg_login = round(time.time() - t_login, 1)

    resultado = scrapear_con_sesion(sesion)
    resultado["segundos_login"] = seg_login

    DESCARGAS.mkdir(parents=True, exist_ok=True)
    salida = DESCARGAS / f"resultado_{cfg.ruc}_{ahora_lima().strftime('%Y%m%d_%H%M%S')}.json"
    salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Resultado guardado en: {salida}")

    if resultado["exito"]:
        log(f"✅ HÍBRIDO OK — login {seg_login}s (Playwright) + "
            f"scraping {resultado.get('segundos_fase2','?')}s (httpx)", "OK")
        log("   En producción la sesión se reusa; cada consulta extra = solo FASE 2.", "INFO")
    else:
        log("❌ FASE 2 (scraping) falló.", "ERROR")
        sys.exit(2)


if __name__ == "__main__":
    main()