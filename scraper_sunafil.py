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

# Las 6 categorías del buzón SUNAFIL (reconocimiento). El parseo es homogéneo.
CATEGORIAS_SUNAFIL = [
    "Fiscalización Laboral", "Cobranza Ordinaria", "Acciones Previas",
    "Alertas de Formalización", "Seguridad y Salud en el Trabajo", "Orientaciones",
]

# Mapa NOMBRE-de-columna (normalizado) → clave de salida. El parser lee el header
# de la tabla y ubica cada celda por su título, sin depender de IDs.
_COL_MAP = {
    "tipo de requerimiento": "tipo_requerimiento",
    "registro": "expediente",
    "fecha de deposito": "fecha_deposito",
    "fecha de acuse de recibo": "fecha_acuse",
    "fecha de acuse": "fecha_acuse",
    "fecha de notificacion": "fecha_notificacion",
    "plazo": "plazo_dias",
    "estado": "estado",
}


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    return "".join(c for c in __import__("unicodedata").normalize("NFD", s)
                   if __import__("unicodedata").category(c) != "Mn")


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


def _parsear_tabla(page, categoria: str, diag: bool) -> list:
    """Parsea la tabla de notificaciones de la categoría activa POR NOMBRE de
    columna (robusto a IDs). Devuelve items con expediente/fechas/plazo/estado."""
    items = []
    tablas = page.query_selector_all("table")
    for t in tablas:
        heads = [_norm(th.inner_text()) for th in t.query_selector_all("thead th, tr th")]
        if not heads:
            continue
        # ¿Es la tabla de notificaciones? (tiene 'registro'/'requerimiento'/'plazo')
        if not any(h in ("registro", "plazo") or "requerimiento" in h for h in heads):
            continue
        col_idx = {}
        for i, h in enumerate(heads):
            clave = _COL_MAP.get(h)
            if not clave and "requerimiento" in h:
                clave = "tipo_requerimiento"
            if clave and clave not in col_idx:
                col_idx[clave] = i
        if diag:
            log(f"   [sunafil-diag] '{categoria}' headers={heads} map={col_idx}", "INFO")
        for tr in t.query_selector_all("tbody tr"):
            tds = tr.query_selector_all("td")
            if not tds:
                continue
            def cel(clave):
                i = col_idx.get(clave)
                return (tds[i].inner_text().strip() if i is not None and i < len(tds) else "")
            exp = cel("expediente")
            if not exp:
                continue
            estado_txt = _norm(cel("estado")) or _norm(tr.inner_text())
            items.append({
                "cod_mensaje": exp,                     # expediente = id único
                "tipo_msj": 2,                          # notificación
                "fuente": "sunafil",
                "categoria": categoria,
                "asunto": cel("tipo_requerimiento") or categoria,
                "fecha_envio": cel("fecha_deposito"),
                "fecha_notificacion": cel("fecha_notificacion"),
                "plazo_dias": _plazo_a_dias(cel("plazo_dias")),
                "no_leida": ("no leido" in estado_txt or "no leído" in estado_txt),
                "_row_handle": tr,                      # para expandir y bajar el PDF
            })
        if items:
            break
    return items


def leer_casilla_sunafil(cfg: SunatConfig, conocidos: set | None = None,
                         categorias: list | None = None, diag: bool = False) -> dict:
    """Lee el buzón SUNAFIL del RUC. Reusa el login SOL. Devuelve un resultado con
    el MISMO shape que el scraper SUNAT (mensajes[]) para que `ingestar_resultado`
    lo guarde igual (con fuente='sunafil'). `diag=True` vuelca el DOM y NO exige
    que el parseo sea perfecto (sirve para confirmar selectores en el worker)."""
    resultado = {"ruc": cfg.ruc, "fuente": "sunafil",
                 "scrapeado_at": ahora_lima().isoformat(), "mensajes": [],
                 "exito": False}
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
            page.wait_for_timeout(1500)
            # VUELCA EL INBOX REAL (clave para afinar selectores de tabla/categorías).
            if diag:
                _evidencia(page, "sunafil_inbox")
                log(f"   [sunafil-diag] DENTRO del inbox: {page.url}", "INFO")
                log(f"   [sunafil-diag] tablas en el inbox: "
                    f"{len(page.query_selector_all('table'))}", "INFO")

            # 2) Recorrer categorías. Cada una: click en su pestaña/enlace dentro del
            #    inbox (app JS), esperar la tabla, parsear por encabezado.
            for cat in cats:
                try:
                    tab = page.query_selector(f"a:has-text('{cat}'), button:has-text('{cat}'), "
                                              f"li:has-text('{cat}'), span:has-text('{cat}')")
                    if tab:
                        try:
                            tab.click()
                        except Exception:
                            pass
                        page.wait_for_timeout(2000)
                    _cerrar_modales(page, diag)
                    if diag:
                        _evidencia(page, f"sunafil_cat_{_norm(cat)[:20].replace(' ', '_')}")
                    filas = _parsear_tabla(page, cat, diag)
                    log(f"SUNAFIL '{cat}': {len(filas)} notificación(es).", "OK")
                    for f in filas:
                        if conocidos is not None and str(f["cod_mensaje"]) in conocidos:
                            continue
                        f.pop("_row_handle", None)   # no serializable
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
