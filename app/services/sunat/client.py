"""Cliente HTTP para SUNAT con login OAuth2 + manejo de sesión."""
import re
import urllib.parse
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from rich.console import Console

from app.config import settings

console = Console()


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
        "Chrome/120.0.0.0 Safari/537.36"
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
            tipo_usuario: 1=usuario SOL, 2=DNI titular.
            dni: DNI (solo si tipo_usuario=2).
            usuario_sol: usuario SOL alfanumérico (solo si tipo_usuario=1).
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
        self.client = httpx.Client(
            timeout=timeout_segundos,
            follow_redirects=False,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
            },
        )
        self.autenticado = False
        self.state_token: Optional[str] = None
        self.jwt_code: Optional[str] = None
        self.url_menu_principal: Optional[str] = None
        self.url_visor_completa: Optional[str] = None
        self.hc_token: Optional[str] = None
        self.visor_token: Optional[str] = None

    @property
    def j_username(self) -> str:
        """Devuelve el username correcto según tipo."""
        return self.dni if self.tipo_usuario == 2 else self.usuario_sol

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

            actual = self.client.get(url_siguiente)
        return actual

    def obtener_form_login(self) -> str:
        """Paso 1: GET a authen con state pre-conocido."""
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
        respuesta = self.client.get(url_authen, params=params)
        respuesta = self._seguir_redirects_manual(respuesta)
        respuesta.raise_for_status()

        with open("debug_login_html.html", "w", encoding="utf-8") as f:
            f.write(respuesta.text)

        state = self._extraer_state(str(respuesta.url), respuesta.text)
        if not state or state == "null":
            state = self.STATE_CONOCIDO
        self.state_token = state
        self._log(f"      ✓ State capturado, cookies: {len(self.client.cookies)}", "green")
        return state

    def _extraer_state(self, url_final: str, html: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url_final)
        params_query = urllib.parse.parse_qs(parsed.query)
        if "state" in params_query:
            return params_query["state"][0]
        soup = BeautifulSoup(html, "html.parser")
        state_input = soup.find("input", {"name": "state"})
        if state_input and state_input.get("value"):
            return state_input["value"]
        return None

    def hacer_login(self) -> None:
        """Paso 2: POST login + seguir todos los redirects."""
        if not self.state_token:
            self.obtener_form_login()

        url_post = self.URL_LOGIN_POST.format(client_id=self.CLIENT_ID)
        form_data = {
            "tipo": str(self.tipo_usuario),
            "dni": self.dni if self.tipo_usuario == 2 else "",
            "custom_ruc": self.ruc,
            "j_username": self.j_username,
            "j_password": self.clave_sol,
            "captcha": "",
            "originalUrl": self.URL_MENU,
            "lang": "es-PE",
            "state": self.state_token,
        }
        headers_post = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://api-seguridad.sunat.gob.pe",
            "Referer": (
                f"https://api-seguridad.sunat.gob.pe/v1/clientessol/"
                f"{self.CLIENT_ID}/oauth2/loginMenuSol?lang=es-PE"
            ),
        }

        self._log(f"[2/6] POST j_security_check ...", "cyan")
        respuesta = self.client.post(url_post, data=form_data, headers=headers_post)

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
            self._log(
                f"      ⚠️ NO se capturaron hc/token del visor. "
                f"Revisar debug_buzon.html y/o ajustar params_buzon.",
                "red",
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