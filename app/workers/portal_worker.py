"""
workers/portal_worker.py — Celery task: updates estado_sistema every 5 minutes.

Pings SUNAT API, counts today's submissions, calculates uptime.

Decisiones tecnicas:
- La tarea se ejecuta cada 5 minutos via Celery Beat.
- El ping a SUNAT usa la URL publica de consulta RUC (no requiere credenciales).
- Si SUNAT no responde en 10 segundos, se marca como no disponible.
- El uptime se calcula como porcentaje de checks exitosos en las ultimas 24h.
- Se mantiene un solo registro activo en EstadoSistema (el mas reciente).
  Los anteriores se conservan para historial.
"""

import logging
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Optional

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# URL publica de SUNAT para verificar disponibilidad
# Consulta RUC basica que no requiere autenticacion
SUNAT_PING_URL = "https://e-consultaruc.sunat.gob.pe/cl-ti-itmrconsruc/jcrS00Alias"
SUNAT_TIMEOUT_SECONDS = 10


def _get_db_session():
    """Obtener sesion de BD para workers (fuera del contexto FastAPI)."""
    from app.core.deps import SessionLocal
    return SessionLocal()


@celery_app.task(
    name="app.workers.portal_worker.actualizar_estado_sistema",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def actualizar_estado_sistema(self):
    """
    Actualiza el estado del sistema cada 5 minutos.

    Pasos:
      1. Ping a SUNAT API y medir tiempo de respuesta.
      2. Contar envios del portal de hoy.
      3. Contar validaciones exitosas de hoy.
      4. Calcular uptime (porcentaje de checks exitosos en 24h).
      5. Insertar nuevo registro en EstadoSistema.

    Se ejecuta automaticamente via Celery Beat cada 5 minutos.
    """
    db = _get_db_session()
    try:
        from app.models.portal import EstadoSistema, EnvioPortal, EstadoValidacionPortal
        from sqlalchemy import select, func

        ahora = datetime.now(timezone.utc)
        hoy = date.today()

        # 1. Ping SUNAT
        sunat_disponible, tiempo_respuesta_ms = _ping_sunat()

        # 2. Contar envios de hoy
        envios_hoy = db.execute(
            select(func.count(EnvioPortal.id)).where(
                func.date(EnvioPortal.created_at) == hoy
            )
        ).scalar_one() or 0

        # 3. Contar validaciones exitosas de hoy
        validaciones_exitosas = db.execute(
            select(func.count(EnvioPortal.id)).where(
                func.date(EnvioPortal.created_at) == hoy,
                EnvioPortal.estado_validacion == EstadoValidacionPortal.VALIDO,
            )
        ).scalar_one() or 0

        # 4. Calcular uptime (ultimas 24h)
        hace_24h = ahora - timedelta(hours=24)
        uptime = _calcular_uptime(db, hace_24h, sunat_disponible)

        # 5. Detectar incidencias activas
        incidencias = _detectar_incidencias(
            sunat_disponible, tiempo_respuesta_ms, envios_hoy
        )

        # 6. Insertar nuevo registro
        estado = EstadoSistema(
            timestamp=ahora,
            sunat_disponible=sunat_disponible,
            sunat_tiempo_respuesta=tiempo_respuesta_ms,
            envios_hoy=envios_hoy,
            validaciones_exitosas_hoy=validaciones_exitosas,
            uptime_porcentaje=uptime,
            incidencias_activas=incidencias if incidencias else None,
        )
        db.add(estado)
        db.commit()

        logger.info(
            f"Estado sistema actualizado: SUNAT={'OK' if sunat_disponible else 'CAIDO'} "
            f"({tiempo_respuesta_ms}ms), envios_hoy={envios_hoy}, "
            f"validaciones={validaciones_exitosas}, uptime={uptime}%"
        )

        return {
            "sunat_disponible": sunat_disponible,
            "sunat_tiempo_respuesta_ms": tiempo_respuesta_ms,
            "envios_hoy": envios_hoy,
            "validaciones_exitosas_hoy": validaciones_exitosas,
            "uptime_porcentaje": str(uptime) if uptime else None,
        }

    except Exception as e:
        logger.exception(f"Error actualizando estado del sistema: {e}")
        db.rollback()
        # Reintentar si quedan intentos
        raise self.retry(exc=e)
    finally:
        db.close()


def _ping_sunat() -> tuple[bool, Optional[int]]:
    """
    Hace ping a la URL publica de SUNAT para verificar disponibilidad.

    Returns:
        Tupla (disponible: bool, tiempo_respuesta_ms: int o None).
    """
    try:
        import requests

        response = requests.get(
            SUNAT_PING_URL,
            timeout=SUNAT_TIMEOUT_SECONDS,
            allow_redirects=True,
            headers={
                "User-Agent": "alertape-monitor/1.0",
            },
        )

        tiempo_ms = int(response.elapsed.total_seconds() * 1000)

        # SUNAT responde 200 incluso con errores, verificar que hay contenido
        disponible = response.status_code == 200 and len(response.content) > 100

        if disponible:
            logger.debug(f"SUNAT ping OK: {tiempo_ms}ms")
        else:
            logger.warning(
                f"SUNAT respuesta inesperada: status={response.status_code}, "
                f"content_length={len(response.content)}"
            )

        return disponible, tiempo_ms

    except ImportError:
        logger.error("requests no instalado, no se puede hacer ping a SUNAT")
        return False, None
    except Exception as e:
        logger.warning(f"SUNAT no disponible: {e}")
        return False, None


def _calcular_uptime(
    db, desde: datetime, check_actual_ok: bool
) -> Optional[Decimal]:
    """
    Calcula el porcentaje de uptime basado en checks de las ultimas 24h.

    Formula: (checks_exitosos / total_checks) * 100
    Incluye el check actual en el calculo.
    """
    from app.models.portal import EstadoSistema
    from sqlalchemy import select, func

    try:
        total_checks = db.execute(
            select(func.count(EstadoSistema.id)).where(
                EstadoSistema.timestamp >= desde
            )
        ).scalar_one() or 0

        checks_ok = db.execute(
            select(func.count(EstadoSistema.id)).where(
                EstadoSistema.timestamp >= desde,
                EstadoSistema.sunat_disponible == True,  # noqa: E712
            )
        ).scalar_one() or 0

        # Incluir check actual
        total_checks += 1
        if check_actual_ok:
            checks_ok += 1

        if total_checks == 0:
            return Decimal("100.00")

        uptime = Decimal(str(checks_ok)) / Decimal(str(total_checks)) * Decimal("100")
        return uptime.quantize(Decimal("0.01"))

    except Exception as e:
        logger.warning(f"Error calculando uptime: {e}")
        return None


def _detectar_incidencias(
    sunat_disponible: bool,
    tiempo_respuesta_ms: Optional[int],
    envios_hoy: int,
) -> Optional[list[dict]]:
    """
    Detecta incidencias activas basandose en metricas actuales.

    Incidencias posibles:
    - SUNAT caido
    - SUNAT lento (>5000ms)
    - Sin envios (posible problema de conectividad)
    """
    incidencias = []

    if not sunat_disponible:
        incidencias.append({
            "tipo": "sunat_caido",
            "severidad": "alta",
            "mensaje": "SUNAT no responde. Las validaciones en linea no estan disponibles.",
        })

    if tiempo_respuesta_ms and tiempo_respuesta_ms > 5000:
        incidencias.append({
            "tipo": "sunat_lento",
            "severidad": "media",
            "mensaje": f"SUNAT responde lento: {tiempo_respuesta_ms}ms (umbral: 5000ms).",
        })

    return incidencias if incidencias else None
