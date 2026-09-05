"""
validar_login.py — alerta.pe   (C:\\alertape\\validar_login.py)
═══════════════════════════════════════════════════════════════════════
Modo "solo validar login" para el botón "Comprobar conexión" (zAlerta-10 B).

La WEB es liviana y NO tiene Playwright. El WORKER (que sí lo tiene) usa este
módulo para hacer un login REAL a SUNAT con unas credenciales y responder
solo True/False — SIN entrar al buzón, SIN listar mensajes, SIN descargar.

REGLA (zAlerta-10 RESTRICCIONES): NO se modifica la lógica de login del motor.
Aquí solo se REUTILIZA `scraper.login_sol` (la misma función validada que usa
el scraper completo) y se DETIENE en cuanto el login termina. Si login_sol
devuelve True, las credenciales entran; si False, no.

Uso (desde el worker):
    from validar_login import validar_login_sync
    conecta = await asyncio.to_thread(validar_login_sync, ruc, usuario, clave)
"""

from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

# Reutilizamos el motor validado SIN tocarlo: misma config y mismo login.
import scraper_sunat_playwgth as scraper

# Señales de que la URL es del HOST del menú SOL. OJO: la propia PÁGINA DE
# LOGIN vive en .../cl-ti-itmenu/MenuInternet.htm, así que estas marcas NO
# distinguen por sí solas "logueado" de "formulario de login". El discriminador
# fuerte es la AUSENCIA del formulario de login (ver _hay_formulario_login).
_MARCAS_MENU = ("menuinternet", "itmenu", "cl-ti-itmenu")
# Señales de credencial rechazada o sesión NO iniciada (cualquiera ⇒ ✗).
_MARCAS_RECHAZO = ("clave incorrect", "usuario incorrect", "inválid",
                   "no coincide", "captcha", "bloquead", "intentos",
                   "no se ha podido", "vuelva a intentar")
# Campos del FORMULARIO de login. Si CUALQUIERA está visible, NO entró: SUNAT
# re-renderiza el form (en la MISMA URL MenuInternet.htm) cuando falla el login.
_SEL_FORM_LOGIN = ("#txtContrasena", "input[name='txtContrasena']",
                   "input[type='password']", "#txtRuc",
                   "input[name='txtRuc']", "#txtUsuario",
                   "input[name='txtUsuario']")


def _hay_formulario_login(page) -> bool:
    """True si la página AÚN muestra el formulario de login (clave/RUC/usuario).

    Es la prueba decisiva del BUG 1: tras un login FALLIDO, SUNAT vuelve a
    mostrar el formulario en la misma URL (.../MenuInternet.htm) que contiene
    'menuinternet'/'itmenu' — por eso la URL sola daba falso positivo. El menú
    autenticado NO tiene campo de clave; el formulario de login SÍ.
    """
    for sel in _SEL_FORM_LOGIN:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        except Exception:
            continue
    return False


def _sesion_confirmada(page) -> bool:
    """¿La página quedó en el área autenticada de SOL, de verdad?

    BUG 1 (falso positivo): `login_sol` devuelve True con solo ver 'menuinternet'
    en la URL — pero la PÁGINA DE LOGIN vive en esa misma URL, así que un login
    fallido (RUC/clave equivocados) que re-renderiza el formulario pasaba como ✔.
    Aquí exigimos prueba POSITIVA e independiente de sesión iniciada:
      1) URL en el host del menú SOL,
      2) el formulario de login YA NO está visible (lo decisivo),
      3) sin mensaje de credencial rechazada en el contenido.
    Si algo no se cumple ⇒ ✗. Nunca ✔ "por las dudas".
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if not any(m in url for m in _MARCAS_MENU):
        return False
    # Lo decisivo: si sigue el formulario de login, el login NO entró.
    if _hay_formulario_login(page):
        return False
    try:
        contenido = (page.content() or "").lower()
    except Exception:
        # En la URL del menú, sin formulario, pero no pudimos leer el DOM: no
        # afirmamos (preferimos ✗ a un falso positivo).
        return False
    if any(t in contenido for t in _MARCAS_RECHAZO):
        return False
    return True


def validar_login_sync(ruc: str, usuario_sol: str, clave_sol: str) -> bool:
    """Login-only contra SUNAT. Devuelve True si el LOGIN entró (sesión iniciada).

    Replica EXACTAMENTE el arranque del navegador de `scraper.scrapear_ruc`
    (mismos args headless, locale, timezone, user-agent) para que el login se
    comporte igual, pero se detiene apenas `login_sol` retorna. Cualquier
    excepción se trata como "no concluyente" → la relanza para que el worker
    la registre como ERROR (no como NO_CONECTA).

    FIX (zAlerta-fix-noconecta): el RESULTADO DE `login_sol` ES AUTORITATIVO. Ese
    motor ya exige llegar a la URL del menú autenticado Y descarta credencial
    inválida (su propia heurística) antes de devolver True ("Login OK — sesión
    autenticada"). El segundo chequeo `_sesion_confirmada` (DOM post-login) era
    FRÁGIL: dependía de elementos/texto del menú que SUNAT cambió, y rechazaba
    logins genuinos ("Login OK" → "NO conecta"), bloqueando el alta de RUCs
    nuevos. Volvemos al criterio que funcionaba: si login_sol entró, conecta.
    NO se toca el motor de login (selectores ni autenticación).
    """
    cfg = scraper.SunatConfig(
        ruc=ruc, usuario_sol=usuario_sol, clave_sol=clave_sol, headless=True)

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        contexto = navegador.new_context(
            locale="es-PE",
            timezone_id="America/Lima",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        page = contexto.new_page()
        try:
            # login_sol devuelve True solo si llegó al menú autenticado y no
            # detectó rechazo de credenciales: ese resultado basta para CONECTA.
            return bool(scraper.login_sol(page, cfg))
        finally:
            # Cerrar siempre, pase lo que pase (no dejar Chromium colgado).
            try:
                contexto.close()
            except Exception:
                pass
            try:
                navegador.close()
            except Exception:
                pass


def _peek_ultimo_aviso(contexto, page) -> str | None:
    """Trae SOLO el 1er mensaje del buzón como evidencia de lectura real.

    Best-effort y READ-ONLY: entra al buzón (visor) y hace UNA llamada JSON a
    listNotiMenPag(page=1) — NO pagina el buzón entero, NO abre mensajes, NO
    pulsa acuse. Devuelve "dd/mm · asunto" del primer registro, o None si algo
    falla (buzón vacío, timeout, cambio de DOM). NUNCA lanza: un fallo del peek
    no debe convertir un login válido en "no conecta" (login es la verdad).

    Costo medido: +~4 s sobre el login-only (entrar_buzon ~3 s + 1 API ~1 s).
    """
    try:
        visor_frame = scraper.entrar_buzon(page)
        if not visor_frame:
            return None
        m = re.search(r"(https?://[^/]+/ol-ti-itvisornoti)",
                      visor_frame.url or "")
        visor_base = m.group(1) if m else scraper.VISOR_BASE
        url = (f"{visor_base}/visor/listNotiMenPag?tipoMsj=1&codCarpeta=00"
               f"&codEtiqueta=&page=1&des_asunto=&codMensaje=&tipoOrden=NADA")
        data = scraper.fetch_json(contexto.request, url, "peek-ultimo")
        rows = (data.get("rows") if isinstance(data, dict) else data) or []
        if not rows:
            return None
        r0 = rows[0]
        # fecEnvio viene "dd/mm/yyyy" → nos quedamos con "dd/mm".
        fecha = str(r0.get("fecEnvio") or r0.get("fecPublica") or "")[:5]
        asunto = str(r0.get("desAsunto") or "").strip()
        if not asunto:
            return None
        if len(asunto) > 70:
            asunto = asunto[:69].rstrip() + "…"
        etiqueta = f"{fecha} · {asunto}" if fecha else asunto
        return etiqueta[:140]
    except Exception:
        return None


def comprobar_conexion_sync(ruc: str, usuario_sol: str,
                            clave_sol: str) -> dict:
    """Login-only + peek del último aviso, en UNA sola sesión de navegador.

    Devuelve {"conecta": bool, "ultimo": str | None}. `conecta` sale
    EXCLUSIVAMENTE de `login_sol` (autoritativo, ver validar_login_sync); el
    peek del buzón es un extra best-effort que solo se intenta si el login
    entró, y su fallo deja "ultimo": None sin afectar "conecta".

    Reutiliza el MISMO arranque de navegador que validar_login_sync para que
    el login se comporte idéntico. Cualquier excepción del LOGIN se relanza
    (el worker la marca ERROR); el peek nunca lanza.
    """
    cfg = scraper.SunatConfig(
        ruc=ruc, usuario_sol=usuario_sol, clave_sol=clave_sol, headless=True)

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        contexto = navegador.new_context(
            locale="es-PE",
            timezone_id="America/Lima",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        page = contexto.new_page()
        try:
            conecta = bool(scraper.login_sol(page, cfg))
            ultimo = _peek_ultimo_aviso(contexto, page) if conecta else None
            return {"conecta": conecta, "ultimo": ultimo}
        finally:
            try:
                contexto.close()
            except Exception:
                pass
            try:
                navegador.close()
            except Exception:
                pass
