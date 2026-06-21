"""
servicios/pagook_client.py — alerta.pe (zAlerta-14)
═══════════════════════════════════════════════════════════════════════
Cliente de la API de PagoOK (https://pagook.pro/api/v1) para validar pagos
Yape/Plin de la suscripción. CONSUMO BACKEND-A-BACKEND: la PAGOOK_API_KEY
vive SOLO aquí (env), NUNCA llega al navegador.

  - listar_pagos(monto, desde, hasta, ...) → GET /pagos (filtra por monto/fecha).
  - reclamar_pago(id)                       → POST /pagos/{id}/reclamar (atómico,
        idempotente: 200 reclamado, 409 ya reclamado por otra cuenta).

Errores de red/timeout se normalizan a {"ok": False, "error": ...} como en los
clientes de Facturalo (CCPL/QueVendi). NUNCA se loguea la API Key ni PII.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import httpx

logger = logging.getLogger("alertape.pagook")

PAGOOK_BASE_URL = os.getenv("PAGOOK_BASE_URL", "https://pagook.pro/api/v1")
TIMEOUT = float(os.getenv("PAGOOK_TIMEOUT", "15"))


def _api_key() -> str:
    return os.getenv("PAGOOK_API_KEY", "")


def _headers() -> dict:
    return {"X-API-Key": _api_key(), "Accept": "application/json"}


def _fmt(dt) -> str | None:
    """Fecha a string ISO (PagoOK acepta ISO/`YYYY-MM-DD HH:MM:SS`)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


def _extraer_lista(data) -> list:
    """Normaliza la respuesta a una lista de pagos, sea cual sea su envoltura."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("pagos", "data", "results", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


async def listar_pagos(monto=None, desde=None, hasta=None,
                       limit: int = 50, offset: int = 0) -> dict:
    """GET /pagos con filtros. Devuelve {"ok": True, "pagos": [...]} o
    {"ok": False, "error": ...}. No loguea la key ni datos sensibles."""
    if not _api_key():
        return {"ok": False, "error": "PAGOOK_API_KEY no configurada."}
    params: dict = {"limit": limit, "offset": offset}
    if monto is not None:
        params["monto"] = f"{float(monto):.2f}"
    if desde is not None:
        params["desde"] = _fmt(desde)
    if hasta is not None:
        params["hasta"] = _fmt(hasta)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.get(f"{PAGOOK_BASE_URL}/pagos", params=params,
                              headers=_headers())
        if r.status_code == 200:
            return {"ok": True, "pagos": _extraer_lista(r.json())}
        logger.warning("PagoOK listar_pagos status %s", r.status_code)
        return {"ok": False, "error": f"PagoOK respondió {r.status_code}."}
    except httpx.TimeoutException:
        return {"ok": False, "error": "PagoOK no respondió a tiempo."}
    except httpx.RequestError as e:
        logger.warning("PagoOK listar_pagos red: %s", e)
        return {"ok": False, "error": "No se pudo conectar con PagoOK."}
    except Exception as e:
        logger.warning("PagoOK listar_pagos error: %s", e)
        return {"ok": False, "error": "Error consultando PagoOK."}


async def reclamar_pago(pago_id) -> dict:
    """POST /pagos/{id}/reclamar (atómico e idempotente).
      200 → {"ok": True}
      409 → {"ok": False, "ya_reclamado": True}
      otro/red → {"ok": False, "error": ...}
    """
    if not _api_key():
        return {"ok": False, "error": "PAGOOK_API_KEY no configurada."}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(f"{PAGOOK_BASE_URL}/pagos/{pago_id}/reclamar",
                               headers=_headers())
        if r.status_code == 200:
            return {"ok": True}
        if r.status_code == 409:
            return {"ok": False, "ya_reclamado": True}
        logger.warning("PagoOK reclamar status %s", r.status_code)
        return {"ok": False, "error": f"PagoOK respondió {r.status_code}."}
    except httpx.TimeoutException:
        return {"ok": False, "error": "PagoOK no respondió a tiempo."}
    except httpx.RequestError as e:
        logger.warning("PagoOK reclamar red: %s", e)
        return {"ok": False, "error": "No se pudo conectar con PagoOK."}
    except Exception as e:
        logger.warning("PagoOK reclamar error: %s", e)
        return {"ok": False, "error": "Error reclamando en PagoOK."}
