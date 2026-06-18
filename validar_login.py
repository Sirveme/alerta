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

from playwright.sync_api import sync_playwright

# Reutilizamos el motor validado SIN tocarlo: misma config y mismo login.
import scraper_sunat_playwgth as scraper


def validar_login_sync(ruc: str, usuario_sol: str, clave_sol: str) -> bool:
    """Login-only contra SUNAT. Devuelve True si las credenciales entran.

    Replica EXACTAMENTE el arranque del navegador de `scraper.scrapear_ruc`
    (mismos args headless, locale, timezone, user-agent) para que el login se
    comporte igual, pero se detiene apenas `login_sol` retorna. Cualquier
    excepción se trata como "no concluyente" → la relanza para que el worker
    la registre como ERROR (no como NO_CONECTA).
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
