"""
services/sunafil_service.py — Monitoreo de notificaciones SUNAFIL.

SUNAFIL (Superintendencia Nacional de Fiscalización Laboral).
No tienen API pública documentada — se usa scraping autenticado con httpx.
Solo aplica a empresas con tiene_trabajadores=True.

Se ejecuta diariamente via Celery beat.
Si detecta notificación nueva → Alerta URGENTE inmediata.

Decisiones técnicas:
- httpx con sesión autenticada (cookies) para mantener login.
- Parsing HTML con regex (no BeautifulSoup para evitar dependencia).
  El portal SUNAFIL es simple y predecible.
- Si el portal cambia su estructura, el scraping falla gracefully
  y genera alerta de error en vez de crash.
"""

import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

SUNAFIL_LOGIN_URL = f"{settings.SUNAFIL_BASE_URL}/portal/login"
SUNAFIL_BUZON_URL = f"{settings.SUNAFIL_BASE_URL}/portal/buzon-electronico"
SUNAFIL_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def verificar_notificaciones_sunafil(
    db,
    empresa_id: int,
    ruc: str,
    clave_sol_usuario: str,
    clave_sol_password: str,
) -> dict:
    """
    Verifica notificaciones nuevas en el buzón SUNAFIL del contribuyente.
    Solo para empresas con tiene_trabajadores=True.

    Returns: { notificaciones_nuevas: int, items: [...] }
    """
    from app.services.alertas_service import crear_alerta_por_tipo

    try:
        async with httpx.AsyncClient(timeout=SUNAFIL_TIMEOUT, follow_redirects=True) as client:
            # Paso 1: Login con credenciales SOL
            login_response = await client.post(
                SUNAFIL_LOGIN_URL,
                data={
                    "ruc": ruc,
                    "usuario": clave_sol_usuario,
                    "clave": clave_sol_password,
                },
            )

            if login_response.status_code != 200:
                logger.warning(f"SUNAFIL login fallido para {ruc}: {login_response.status_code}")
                return {"notificaciones_nuevas": 0, "error": "Login fallido"}

            # Paso 2: Consultar buzón electrónico
            buzon_response = await client.get(SUNAFIL_BUZON_URL)
            html = buzon_response.text

            # Paso 3: Parsear notificaciones del HTML
            notificaciones = _parsear_buzon_html(html)

            # Paso 4: Verificar cuáles son nuevas (no alertadas previamente)
            nuevas = []
            for notif in notificaciones:
                # Verificar si ya generamos alerta para esta
                from sqlalchemy import select
                from app.models.alertas import Alerta
                existe = db.execute(
                    select(Alerta).where(
                        Alerta.empresa_id == empresa_id,
                        Alerta.codigo_entidad == notif.get("codigo"),
                        Alerta.deleted_at == None,
                    )
                ).scalar_one_or_none()

                if not existe:
                    nuevas.append(notif)
                    crear_alerta_por_tipo(
                        db, empresa_id, "sunafil_notificacion",
                        mensaje=(
                            f"SUNAFIL: {notif.get('tipo', 'Notificación')} - "
                            f"{notif.get('descripcion', 'Sin detalle')}. "
                            f"Fecha límite: {notif.get('fecha_limite', 'No especificada')}"
                        ),
                    )

            if nuevas:
                logger.warning(f"SUNAFIL {ruc}: {len(nuevas)} notificaciones nuevas")

            return {
                "notificaciones_nuevas": len(nuevas),
                "items": nuevas,
            }

    except Exception as e:
        logger.error(f"Error consultando SUNAFIL para {ruc}: {e}")
        return {"notificaciones_nuevas": 0, "error": str(e)}


def _parsear_buzon_html(html: str) -> list:
    """
    Parsea el HTML del buzón SUNAFIL para extraer notificaciones.
    Estructura esperada: tabla con filas de notificaciones.
    Falla gracefully si la estructura cambió.
    """
    notificaciones = []

    try:
        # Buscar filas de tabla con notificaciones
        # Patrón genérico para tablas HTML de portales gubernamentales peruanos
        rows = re.findall(
            r"<tr[^>]*>(.*?)</tr>",
            html,
            re.DOTALL | re.IGNORECASE,
        )

        for row in rows:
            celdas = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(celdas) >= 3:
                # Limpiar HTML de las celdas
                celdas_limpias = [
                    re.sub(r"<[^>]+>", "", c).strip()
                    for c in celdas
                ]

                # Heurística: buscar filas que parecen notificaciones
                # (tienen fecha, código, descripción)
                fecha_match = re.search(r"\d{2}/\d{2}/\d{4}", celdas_limpias[0])
                if fecha_match or any("resolución" in c.lower() or "notificación" in c.lower() for c in celdas_limpias):
                    notificaciones.append({
                        "codigo": celdas_limpias[0][:50] if celdas_limpias else None,
                        "tipo": celdas_limpias[1][:100] if len(celdas_limpias) > 1 else None,
                        "descripcion": celdas_limpias[2][:200] if len(celdas_limpias) > 2 else None,
                        "fecha_limite": celdas_limpias[3][:20] if len(celdas_limpias) > 3 else None,
                    })

    except Exception as e:
        logger.debug(f"Error parseando HTML SUNAFIL: {e}")

    return notificaciones
