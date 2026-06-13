"""Pool en memoria de sesiones SUNAT activas, una por RUC.

Resuelve el problema crítico de SUNAT: NO permite 2 logins consecutivos
del mismo usuario en corto tiempo. Si reusamos la sesión, evitamos login
redundante y SUNAT no nos bloquea.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from app.services.sunat.client import SUNATClient

logger = logging.getLogger(__name__)


@dataclass
class SesionSunat:
    """Sesión SUNAT activa, lista para consultar buzón."""
    ruc: str
    cliente: SUNATClient
    creada_en: datetime
    ultima_actividad: datetime
    en_uso: bool = field(default=False)

    @property
    def edad_segundos(self) -> float:
        return (datetime.now(timezone.utc) - self.creada_en).total_seconds()

    @property
    def inactividad_segundos(self) -> float:
        return (datetime.now(timezone.utc) - self.ultima_actividad).total_seconds()

    def esta_vigente(self, ttl_segundos: int = 1500) -> bool:
        """Sesión válida si la inactividad es menor a TTL (default 25 min)."""
        return self.inactividad_segundos < ttl_segundos

    def marcar_actividad(self) -> None:
        self.ultima_actividad = datetime.now(timezone.utc)


class SunatSessionPool:
    """Pool thread-safe de sesiones SUNAT.

    Una sola instancia global. Cada sesión está keyed por RUC.
    """

    # TTL de sesión: SUNAT mata sesiones tras ~30 min sin actividad.
    # Conservamos margen y la matamos a los 25 min para evitar errores.
    TTL_SEGUNDOS = 25 * 60

    # Cooldown entre logins del mismo RUC. SUNAT bloquea si intentamos 2
    # logins demasiado seguidos.
    COOLDOWN_LOGIN_SEGUNDOS = 60

    def __init__(self) -> None:
        self._sesiones: dict[str, SesionSunat] = {}
        self._lock = Lock()
        self._ultimo_login_por_ruc: dict[str, datetime] = {}

    def _ahora(self) -> datetime:
        return datetime.now(timezone.utc)

    def obtener_o_crear(
        self,
        ruc: str,
        tipo_usuario: int,
        dni: str,
        usuario_sol: str,
        clave_sol: str,
        timeout_segundos: int = 30,
    ) -> tuple[SesionSunat, bool]:
        """Devuelve sesión vigente o crea nueva.

        Returns:
            (sesion, fue_creada_nueva)
            fue_creada_nueva=True si tuvo que loguear.

        Raises:
            RuntimeError: si cooldown impide nuevo login.
            Exception: cualquier error del cliente SUNAT en login.
        """
        with self._lock:
            sesion = self._sesiones.get(ruc)
            if sesion and sesion.esta_vigente(self.TTL_SEGUNDOS):
                # Reutilizar sesión existente
                if sesion.en_uso:
                    raise RuntimeError(
                        f"Sesión del RUC {ruc} está en uso por otra verificación. "
                        "Esperar a que termine."
                    )
                sesion.en_uso = True
                sesion.marcar_actividad()
                logger.info(f"Reusando sesión SUNAT del RUC {ruc} (edad: {sesion.edad_segundos:.0f}s)")
                return sesion, False

            # Verificar cooldown
            ultimo_login = self._ultimo_login_por_ruc.get(ruc)
            if ultimo_login:
                desde_ultimo = (self._ahora() - ultimo_login).total_seconds()
                if desde_ultimo < self.COOLDOWN_LOGIN_SEGUNDOS:
                    falta = self.COOLDOWN_LOGIN_SEGUNDOS - desde_ultimo
                    raise RuntimeError(
                        f"Cooldown activo para RUC {ruc}. Esperar {falta:.0f}s más antes de reintentar."
                    )

            # Limpiar sesión vieja si había
            if sesion:
                self._cerrar_sesion_interna(sesion)
                del self._sesiones[ruc]

            # Marcar el login antes de hacerlo (para cooldown)
            self._ultimo_login_por_ruc[ruc] = self._ahora()

        # Login fuera del lock (es lento, no queremos bloquear el pool)
        cliente = SUNATClient(
            ruc=ruc,
            tipo_usuario=tipo_usuario,
            dni=dni,
            usuario_sol=usuario_sol,
            clave_sol=clave_sol,
            timeout_segundos=timeout_segundos,
        )
        cliente.obtener_form_login()
        cliente.hacer_login()
        cliente.entrar_buzon()

        # Volver al lock para registrar la sesión
        with self._lock:
            sesion = SesionSunat(
                ruc=ruc,
                cliente=cliente,
                creada_en=self._ahora(),
                ultima_actividad=self._ahora(),
                en_uso=True,
            )
            self._sesiones[ruc] = sesion
            logger.info(f"Nueva sesión SUNAT creada para RUC {ruc}")
            return sesion, True

    def liberar(self, ruc: str) -> None:
        """Marca sesión como NO en uso (vuelve disponible para próxima verificación)."""
        with self._lock:
            sesion = self._sesiones.get(ruc)
            if sesion:
                sesion.en_uso = False
                sesion.marcar_actividad()

    def descartar(self, ruc: str) -> None:
        """Elimina una sesión del pool (ej: si dio error y debe reloguear).

        Hace logout en SUNAT antes de descartar.
        """
        with self._lock:
            sesion = self._sesiones.pop(ruc, None)
            if sesion:
                self._cerrar_sesion_interna(sesion)
                logger.info(f"Sesión SUNAT descartada para RUC {ruc}")

    def _cerrar_sesion_interna(self, sesion: SesionSunat) -> None:
        """Cierra la sesión en SUNAT y libera el client HTTP."""
        try:
            sesion.cliente.hacer_logout()
        except Exception:
            pass
        try:
            sesion.cliente.cerrar()
        except Exception:
            pass

    def limpiar_vencidas(self) -> int:
        """Cierra sesiones vencidas. Retorna cantidad limpiada."""
        with self._lock:
            rucs_vencidos = [
                ruc for ruc, s in self._sesiones.items()
                if not s.esta_vigente(self.TTL_SEGUNDOS) and not s.en_uso
            ]
            for ruc in rucs_vencidos:
                sesion = self._sesiones.pop(ruc)
                self._cerrar_sesion_interna(sesion)
            return len(rucs_vencidos)

    def cerrar_todas(self) -> None:
        """Cierra TODAS las sesiones (usar al shutdown)."""
        with self._lock:
            for ruc, sesion in list(self._sesiones.items()):
                self._cerrar_sesion_interna(sesion)
            self._sesiones.clear()
            logger.info("Pool SUNAT: todas las sesiones cerradas")

    def estado(self) -> list[dict]:
        """Snapshot del estado del pool (para debug/admin)."""
        with self._lock:
            return [
                {
                    "ruc": s.ruc,
                    "edad_segundos": s.edad_segundos,
                    "inactividad_segundos": s.inactividad_segundos,
                    "vigente": s.esta_vigente(self.TTL_SEGUNDOS),
                    "en_uso": s.en_uso,
                }
                for s in self._sesiones.values()
            ]


# Singleton global
sunat_pool = SunatSessionPool()
