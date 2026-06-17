"""Cliente HTTP para SUNAT con login OAuth2 + manejo de sesión."""
import re
import secrets
import time
import urllib.parse
from typing import Any, Optional

import httpx
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from rich.console import Console

from app.config import settings

console = Console()

# ============================================================
# DIAGNÓSTICO DE TIMEOUTS — zClaude-13e
# ============================================================
# Timeouts granulares:
#   connect: 20s (TCP handshake)
#   read:    90s (espera de respuesta SUNAT)
#   write:   20s (envío)
#   pool:    20s (espera de conexión libre)
#
# Justificación: latencia Railway USA → SUNAT Perú + SUNAT puede
# tardar 30-60s en responder POST de login bajo carga.
#
# Logging detallado de tiempos en cada request para identificar
# en qué punto exacto se producen timeouts.
# ============================================================


class SUNATClient:
    URL_LOGIN_POST = (
        "https://api-seguridad.sunat.gob.pe"
        "/v1/clientessol/{client_id}/oauth2/j_security_check"
    )
    URL_MENU = (
        "https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"
    )
    URL_MENU_PRINCIPAL = (
        "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
    )
    CLIENT_ID = "4f3b88b3-d9d6-402a-b85d-6a0bc857746a"

    URL_BUZON_BASE = "https://ww1.sunat.gob.pe/ol-ti-itvisornoti/visor"
    URL_VISOR_MASTER = f"{URL_BUZON_BASE}/master"
    URL_LISTAR_CARPETAS = f"{URL_BUZON_BASE}/ajax/listarCarpetas"
    URL_LIST_MENSAJES = f"{URL_BUZON_BASE}/listNotiMenPag"
    URL_DETALLE_MENSAJE = f"{URL_BUZON_BASE}/obtenerDetalleNotiMen"

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )

    STATE_CONOCIDO = (
        "rO0ABXNyABFqYXZhLnV0aWwuSGFzaE1hcAUH2sHDFmDRAwACRg"
        "AKbG9hZEZhY3RvckkACXRocmVzaG9sZHhwP0AAAAAAAAx3CAAA"
        "ABAAAAADdAADZXhlcHQABnBhcmFtc3QASyomKiYvY2wtdGktaX"
        "RtZW51L01lbnVJbnRlcm5ldC5odG0mYjY0ZDI2YThiNWFmMDkx"
        "OTIzYjIzYjY0MDdhMWMxZGI0MWU3MzNhNnQABGV4ZWNweA=="
    )

    def __init__(
        self,
        ruc: str,
        tipo_usuario: int = 2,
        dni: str = "",
        usuario_sol: str = "",
        clave_sol: str = "",
        timeout_segundos: int = 30,
    ) -> None:
        """Cliente SUNAT con credenciales explícitas (no de settings global).

        Args:
            ruc: RUC de 11 dígitos.
            tipo_usuario: 1=DNI titular, 2=RUC + Usuario SOL alfanumérico.
            dni: DNI (solo si tipo_usuario=1).
            usuario_sol: usuario SOL alfanumérico (solo si tipo_usuario=2).
            clave_sol: clave SOL (siempre obligatoria).
            timeout_segundos: timeout HTTP.
        """
        self.ruc = ruc.strip()
        self.tipo_usuario = tipo_usuario
        self.dni = (dni or "").strip()
        self.usuario_sol = (usuario_sol or "").strip()
        self.clave_sol = clave_sol
        self.timeout_segundos = timeout_segundos

        # NOTA: follow_redirects DEBE quedar en False — el flujo captura los
        # tokens (jwt_code, hc, visor) siguiendo los redirects manualmente.
        # Usar curl_cffi con impersonate Chrome para evitar TLS fingerprinting de F5
        self.client = cffi_requests.Session(impersonate="chrome146")
        self.client.timeout = 90

        # Forzar HTTP/1.1 — F5 BIG-IP SUNAT rutea HTTP/2 a pool defectuoso
        # WinHTTP usa HTTP/1.1 y funciona, curl_cffi default es h2 y falla
        from curl_cffi import CurlOpt, CurlHttpVersion
        self.client.curl.setopt(CurlOpt.HTTP_VERSION, CurlHttpVersion.V1_1)
        self._log("[init] Forzando HTTP/1.1", "yellow")

        # Aplicar los MISMOS headers que tenía la versión httpx (COPIAR EXACTOS)
        self.client.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "es-419,es;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })
        self.autenticado = False
        self.state_token: Optional[str] = None
        self.jwt_code: Optional[str] = None
        self.url_menu_principal: Optional[str] = None
        self.url_visor_completa: Optional[str] = None
        self.hc_token: Optional[str] = None
        self.visor_token: Optional[str] = None

    @property
    def j_username(self) -> str:
        """Username según tipo SUNAT:
        - tipo=1 (DNI): username = DNI del titular
        - tipo=2 (RUC+Usuario SOL): username = Usuario SOL alfanumérico
        """
        return self.dni if self.tipo_usuario == 1 else self.usuario_sol

    def _log(self, mensaje: str, color: str = "white") -> None:
        if settings.debug:
            console.print(f"[{color}]{mensaje}[/{color}]")

    def _seguir_redirects_manual(
        self,
        respuesta: httpx.Response,
        max_redirects: int = 15,
    ) -> httpx.Response:
        """Sigue redirects manualmente capturando código JWT y tokens del visor."""
        actual = respuesta
        for i in range(max_redirects):
            if actual.status_code not in (301, 302, 303, 307, 308):
                break
            location = actual.headers.get("location")
            if not location:
                break

            url_siguiente = str(httpx.URL(actual.url).join(location))
            self._log(f"        ↳ redirect {i+1}: {actual.status_code} → {url_siguiente[:100]}", "dim")

            parsed = urllib.parse.urlparse(url_siguiente)
            params = urllib.parse.parse_qs(parsed.query)

            # Capturar JWT 'code' del Location
            if "code" in params:
                self.jwt_code = params["code"][0]
                self._log(f"        🔑 JWT: {self.jwt_code[:40]}...", "green")

            # Capturar tokens del visor (hc + token)
            if "hc" in params:
                self.hc_token = params["hc"][0]
                self._log(f"        🎫 hc: {self.hc_token[:40]}...", "green")
            if "token" in params and "/visor/" in url_siguiente:
                self.visor_token = params["token"][0]
                self._log(f"        🎫 token visor: {self.visor_token[:40]}...", "green")

            inicio_req = time.time()
            self._log(f"  → GET redirect: {url_siguiente[:80]}...", "cyan")
            actual = self.client.get(url_siguiente, allow_redirects=False)
            duracion = round(time.time() - inicio_req, 2)
            self._log(f"  ← Status {actual.status_code} en {duracion}s", "cyan")
            self._log(f"[debug-redirect] URL siguiente: {url_siguiente[:400]}", "magenta")

            self._log(f"  ← Response Status: {actual.status_code}", "magenta")

            # Mostrar TODOS los Set-Cookie de esta respuesta
            set_cookies_list = []
            if hasattr(actual.headers, 'get_list'):
                set_cookies_list = actual.headers.get_list("set-cookie")
            else:
                # fallback para httpx
                set_cookies_raw = actual.headers.get("set-cookie", "")
                set_cookies_list = [set_cookies_raw] if set_cookies_raw else []

            if set_cookies_list:
                self._log(f"  ← Esta respuesta tiene {len(set_cookies_list)} Set-Cookie headers:", "magenta")
                for sc in set_cookies_list:
                    if sc:
                        self._log(f"      Set-Cookie: {sc[:250]}", "magenta")
            else:
                self._log(f"  ← Esta respuesta NO tiene Set-Cookie headers", "magenta")

            # Mostrar TOTAL de cookies en el jar después de esta respuesta
            total_cookies = len(list(self.client.cookies.jar))
            self._log(f"  ← Total cookies en jar después de esta response: {total_cookies}", "magenta")
        self._log(f"[debug-final] URL final del redirect chain: {str(actual.url)[:400]}", "magenta")
        return actual

    def obtener_form_login(self) -> str:
        """GET inicial a authen para acumular cookies F5 y capturar state."""

        url_authen = (
            f"https://api-seguridad.sunat.gob.pe"
            f"/v1/clientessol/{self.CLIENT_ID}/oauth2/authen"
        )
        params = {
            "redirect_uri": self.URL_MENU,
            "state": self.STATE_CONOCIDO,
            "client_id": self.CLIENT_ID,
            "response_type": "code",
        }

        self._log(f"[1/6] GET authen ...", "cyan")
        respuesta = self.client.get(url_authen, params=params, allow_redirects=False)

        respuesta = self._seguir_redirects_manual(respuesta)
        respuesta.raise_for_status()

        with open("debug_login_html.html", "w", encoding="utf-8") as f:
            f.write(respuesta.text)

        state = self._extraer_state(str(respuesta.url), respuesta.text)
        if not state or state == "null" or state == "-":
            state = self.STATE_CONOCIDO
        self.state_token = state
        self._log(f"      ✓ State capturado, cookies: {len(self.client.cookies)}", "green")
        return state

    def _extraer_state(self, url_final: str, html: str) -> Optional[str]:
        self._log(f"[_extraer_state] URL final: {url_final[:200]}", "yellow")

        # OPCIÓN A: state en query string de la URL final
        parsed = urllib.parse.urlparse(url_final)
        params_query = urllib.parse.parse_qs(parsed.query)
        if "state" in params_query:
            state_url = params_query["state"][0]
            self._log(f"[_extraer_state] State desde URL: {state_url[:80]}", "yellow")
            if len(state_url) > 20:
                return state_url

        # OPCIÓN B: input hidden con name="state"
        soup = BeautifulSoup(html, "html.parser")
        state_input = soup.find("input", {"name": "state"})
        if state_input:
            valor = state_input.get("value", "")
            self._log(f"[_extraer_state] State desde input hidden: {valor[:80]}", "yellow")
            if len(valor) > 20:
                return valor

        # OPCIÓN C: buscar en cualquier atributo value de inputs
        inputs = soup.find_all("input")
        self._log(f"[_extraer_state] Total inputs en HTML: {len(inputs)}", "yellow")
        for inp in inputs:
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                self._log(f"[_extraer_state]   input name='{name}' value='{value[:60]}...'", "cyan")

        # OPCIÓN D: regex en el HTML por si está en JavaScript
        import re
        match = re.search(r'state["\'\s:=]+([A-Za-z0-9+/=_-]{50,})', html)
        if match:
            state_re = match.group(1)
            self._log(f"[_extraer_state] State desde regex: {state_re[:80]}", "yellow")
            return state_re

        self._log(f"[_extraer_state] NO se encontró state válido", "red")
        return None

    def hacer_login(self) -> None:
        """Paso 2: POST login + seguir todos los redirects."""
        if not self.state_token:
            self.obtener_form_login()

        self._log(f"[hacer_login] tipo_usuario={self.tipo_usuario} (1=DNI, 2=RUC+Usuario SOL)", "yellow")
        if self.tipo_usuario == 1 and not self.dni:
            raise RuntimeError("tipo_usuario=1 requiere DNI")
        if self.tipo_usuario == 2 and not self.usuario_sol:
            raise RuntimeError("tipo_usuario=2 requiere usuario_sol")

        url_post = self.URL_LOGIN_POST.format(client_id=self.CLIENT_ID)
        form_data = {
            "tipo": str(self.tipo_usuario),
            "dni": self.dni if self.tipo_usuario == 1 else "",
            "custom_ruc": self.ruc,
            "j_username": self.j_username,
            "j_password": self.clave_sol,
            "captcha": "",
            "originalUrl": self.URL_MENU,
            "lang": "es-PE",
            "state": self.state_token,
        }
        # FIX 2 — Referer dinámico completo (imita el loginMenuSol real de Chrome)
        referer_url = (
            f"https://api-seguridad.sunat.gob.pe/v1/clientessol/"
            f"{self.CLIENT_ID}/oauth2/loginMenuSol?lang=es-PE"
            f"&showDni=true&showLanguages=false"
            f"&originalUrl={self.URL_MENU}"
            f"&state={self.state_token}"
        )
        headers_post = {
            "Content-Type": "application/x-www-form-urlencoded",
            # FIX 1 — Origin del POST de login
            "Origin": "https://api-seguridad.sunat.gob.pe",
            "Referer": referer_url,
        }

        self._log(f"[hacer_login] Cookies actuales antes del POST: {len(self.client.cookies)} total", "yellow")
        for cookie in self.client.cookies.jar:
            valor_corto = cookie.value[:40] + "..." if len(cookie.value) > 40 else cookie.value
            self._log(f"[hacer_login]   Cookie: {cookie.name} = {valor_corto} (domain: {cookie.domain})", "cyan")

        # Cookie compuesta RUC+USR sin separadores - Chrome la setea cuando hay Recuérdame
        ruc_dni = f"{self.ruc}0{self.j_username}"
        self.client.cookies.set(ruc_dni, "1", domain=".sunat.gob.pe", path="/")

        # MENU-SOL-LANGUAGE (Chrome la envía en el POST)
        self.client.cookies.set("MENU-SOL-LANGUAGE", "es-PE", domain=".sunat.gob.pe", path="/")

        # Log para verificar
        self._log(f"[hacer_login] Cookies para POST: MENU-SOL-LANGUAGE, TS019e7fc2 (auto), {ruc_dni}", "yellow")

        self._log(f"[2/6] POST j_security_check ...", "cyan")
        # [LOGGING DETALLADO PARA DIAGNÓSTICO]
        self._log(f"[hacer_login] Iniciando POST a {url_post}", "yellow")
        self._log(f"[hacer_login] form_data keys: {list(form_data.keys())}", "yellow")

        # Construir el body manualmente y enviarlo como string explícito
        import urllib.parse

        body_string = urllib.parse.urlencode(form_data)
        self._log(f"[DEBUG] Body raw (longitud {len(body_string)}): {body_string[:300]}", "magenta")

        # Agregar Content-Length explícito y deshabilitar chunked
        headers_post["Content-Length"] = str(len(body_string))
        headers_post["Transfer-Encoding"] = ""  # vacío para deshabilitar

        # FIX 2 — POST con reintento automático y backoff exponencial ante 5xx/timeout
        respuesta = None
        max_intentos = 3
        for intento in range(1, max_intentos + 1):
            inicio_post = time.time()
            try:
                respuesta = self.client.post(
                    url_post,
                    data=body_string,  # string en lugar de dict
                    headers=headers_post,
                    allow_redirects=False
                )
                duracion = round(time.time() - inicio_post, 2)

                if respuesta.status_code >= 500:
                    self._log(f"[hacer_login] Intento {intento}: status {respuesta.status_code} en {duracion}s", "red")
                    if intento < max_intentos:
                        espera = 5 * (2 ** (intento - 1))  # 5, 10, 20
                        self._log(f"[hacer_login] Esperando {espera}s antes de reintentar...", "yellow")
                        time.sleep(espera)
                        continue
                    raise RuntimeError(f"SUNAT 5xx después de {max_intentos} intentos")

                # Status OK (2xx o 3xx), salir del loop
                self._log(f"[hacer_login] POST OK en {duracion}s, status={respuesta.status_code} (intento {intento})", "green")
                break

            except Exception as exc:
                # curl_cffi puede tirar varias excepciones, capturamos genérico
                if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
                    duracion = round(time.time() - inicio_post, 2)
                    self._log(f"[hacer_login] Intento {intento}: timeout {type(exc).__name__} en {duracion}s", "red")
                    if intento < max_intentos:
                        espera = 5 * (2 ** (intento - 1))
                        self._log(f"[hacer_login] Esperando {espera}s antes de reintentar...", "yellow")
                        time.sleep(espera)
                        continue
                    raise
                else:
                    raise

        # FIX 1 — validar status code del POST antes de procesar la respuesta
        if respuesta.status_code >= 500:
            raise RuntimeError(
                f"SUNAT respondió {respuesta.status_code} en login. "
                f"Es probable saturación de SUNAT o latencia red. "
                f"Reintentar en unos minutos."
            )
        if respuesta.status_code >= 400 and respuesta.status_code != 302:
            raise RuntimeError(
                f"SUNAT rechazó login con status {respuesta.status_code}. "
                f"Verificar credenciales."
            )

        location = respuesta.headers.get("location", "")
        if location:
            parsed = urllib.parse.urlparse(location)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                self.jwt_code = params["code"][0]
                self._log(f"      🔑 JWT capturado: {self.jwt_code[:50]}...", "green")

        if respuesta.status_code == 200 and "incorrecta" in respuesta.text.lower():
            raise RuntimeError("Credenciales inválidas")

        respuesta = self._seguir_redirects_manual(respuesta)
        self.url_menu_principal = str(respuesta.url)

        self._log(f"      ✓ Login OK", "green")
        self._log(f"      ✓ Cookies totales: {len(self.client.cookies)}", "green")
        self.autenticado = True

    def entrar_buzon(self) -> None:
        """Paso 3: Llamar a MenuInternet.htm?action=buzon para que SUNAT genere
        los tokens del visor (hc + token) y nos redirija a /visor/master.
        """
        if not self.autenticado:
            raise RuntimeError("Debe llamar a hacer_login() primero")

        # IMPORTANTE: estos query params probablemente necesitan ajuste según
        # lo que veas en F12. Por defecto probamos solo action=buzon.
        params_buzon = {
            "action": "buzon",
            "s": "ww1",
        }

        self._log(f"[3/6] GET MenuInternet.htm?action=buzon ...", "cyan")
        respuesta = self.client.get(
            self.URL_MENU_PRINCIPAL,
            params=params_buzon,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Referer": self.url_menu_principal or self.URL_MENU,
                "X-Requested-With": "XMLHttpRequest",
            },
            allow_redirects=False,
        )

        self._log(f"      📁 Status inicial: {respuesta.status_code}", "yellow")
        location = respuesta.headers.get("location", "")
        if location:
            self._log(f"      📁 Location: {location[:120]}", "yellow")

        # Seguir redirects capturando hc + token
        respuesta = self._seguir_redirects_manual(respuesta)

        with open("debug_buzon.html", "w", encoding="utf-8") as f:
            f.write(respuesta.text)

        self._log(f"      📁 Status final: {respuesta.status_code}", "yellow")
        self._log(f"      📁 URL final: {str(respuesta.url)[:120]}", "yellow")
        self._log(f"      📁 Tamaño: {len(respuesta.text)} bytes", "yellow")

        self.url_visor_completa = str(respuesta.url)

        cookies_ww1 = [c for c in self.client.cookies.jar if "ww1" in c.domain]
        self._log(f"      📁 Cookies ww1: {len(cookies_ww1)}", "yellow")

        if not self.hc_token or not self.visor_token:
            # FIX 3 — abortar si no se capturaron los tokens del visor del buzón
            raise RuntimeError(
                "No se capturaron tokens del visor del buzón. "
                "Posible causa: login no estableció sesión correctamente "
                "(SUNAT respondió 504 al POST) o SUNAT cambió flujo. "
                f"hc={bool(self.hc_token)}, token={bool(self.visor_token)}"
            )
        else:
            self._log(f"      ✓ Tokens del visor capturados", "green")

    def listar_carpetas(self) -> list[dict[str, Any]]:
        """Paso 4: GET listarCarpetas (con Referer del visor real)."""
        if not self.autenticado:
            raise RuntimeError("Debe llamar a hacer_login() primero")

        referer = self.url_visor_completa or f"{self.URL_BUZON_BASE}/master"

        self._log(f"[4/6] GET listarCarpetas ...", "cyan")
        respuesta = self.client.get(
            self.URL_LISTAR_CARPETAS,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer,
            },
            allow_redirects=False,
        )

        with open("debug_carpetas.html", "w", encoding="utf-8") as f:
            f.write(respuesta.text)

        self._log(f"      📁 Status: {respuesta.status_code}", "yellow")
        self._log(f"      📁 Content-Type: {respuesta.headers.get('content-type', '?')}", "yellow")
        self._log(f"      📁 Tamaño: {len(respuesta.text)} bytes", "yellow")
        if respuesta.text:
            self._log(f"      📁 Primeros 300 chars: {respuesta.text[:300]}", "yellow")

        ctype = respuesta.headers.get("content-type", "")
        if "json" not in ctype.lower() or not respuesta.text:
            raise RuntimeError(
                f"listarCarpetas devolvió {ctype or 'vacío'} ({len(respuesta.text)} bytes). "
                f"Revisar debug_carpetas.html"
            )

        carpetas = respuesta.json()
        self._log(f"      ✓ Carpetas: {len(carpetas)}", "green")
        return carpetas

    def listar_mensajes(
        self,
        cod_carpeta: str = "00",
        tipo_msj: int = 2,
        page: int = 1,
    ) -> dict[str, Any]:
        if not self.autenticado:
            raise RuntimeError("Debe llamar a hacer_login() primero")

        referer = self.url_visor_completa or f"{self.URL_BUZON_BASE}/master"

        self._log(f"[5/6] GET listNotiMenPag ...", "cyan")
        respuesta = self.client.get(
            self.URL_LIST_MENSAJES,
            params={"tipoMsj": tipo_msj, "codCarpeta": cod_carpeta, "page": page},
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer,
            },
            allow_redirects=False,
        )

        self._log(f"      📁 Status: {respuesta.status_code}", "yellow")
        self._log(f"      📁 Content-Type: {respuesta.headers.get('content-type', '?')}", "yellow")
        self._log(f"      📁 Tamaño: {len(respuesta.text)} bytes", "yellow")

        ctype = respuesta.headers.get("content-type", "")
        if "json" not in ctype.lower() or not respuesta.text:
            raise RuntimeError(f"listNotiMenPag devolvió {ctype or 'vacío'}")

        data = respuesta.json()
        self._log(
            f"      ✓ Total: {data.get('records', 0)} mensajes",
            "green",
        )
        return data

    def obtener_detalle(self, codigo_mensaje: int) -> dict[str, Any]:
        if not self.autenticado:
            raise RuntimeError("Debe llamar a hacer_login() primero")

        referer = self.url_visor_completa or f"{self.URL_BUZON_BASE}/master"

        respuesta = self.client.get(
            self.URL_DETALLE_MENSAJE,
            params={"codigoMensaje": codigo_mensaje},
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer,
            },
            allow_redirects=False,
        )
        respuesta.raise_for_status()
        return respuesta.json()

    def hacer_logout(self) -> None:
        """Cierra sesión en SUNAT para liberar el slot del usuario.

        Hace GET a la URL de logout. Tolerante a errores (best effort).
        """
        if not self.autenticado:
            return
        try:
            # Endpoint de logout estándar de SUNAT
            self.client.get(
                "https://e-menu.sunat.gob.pe/cl-ti-itmenu/CerrarSesionInternet.htm",
                timeout=10,
                allow_redirects=False,
            )
        except Exception:
            pass
        self.autenticado = False
        self.state_token = None
        self.jwt_code = None
        self.hc_token = None
        self.visor_token = None

    def cerrar(self) -> None:
        self.client.close()  