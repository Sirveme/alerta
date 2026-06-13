"""Validación de RUC usando apis.net.pe."""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

URL_RUC = "https://api.apis.net.pe/v2/sunat/ruc"


async def validar_ruc(ruc: str) -> Optional[dict]:
    """Consulta apis.net.pe y devuelve datos del RUC.

    Returns:
        dict con: {ruc, razon_social, estado, condicion, direccion, ultimo_digito}
        o None si falla / RUC inválido.
    """
    ruc = (ruc or "").strip()
    if len(ruc) != 11 or not ruc.isdigit():
        return None

    if not settings.apis_net_pe_token:
        logger.warning("APIS_NET_PE_TOKEN no configurado, validación RUC desactivada")
        return None

    headers = {
        "Authorization": f"Bearer {settings.apis_net_pe_token}",
        "Accept": "application/json",
    }
    params = {"numero": ruc}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(URL_RUC, headers=headers, params=params)
            if resp.status_code != 200:
                logger.warning(f"apis.net.pe retornó {resp.status_code} para RUC {ruc}")
                return None
            data = resp.json()
    except Exception as exc:
        logger.exception(f"Error consultando RUC {ruc}: {exc}")
        return None

    return {
        "ruc": data.get("numeroDocumento") or ruc,
        "razon_social": (data.get("razonSocial") or "").strip(),
        "estado": (data.get("estado") or "").strip(),
        "condicion": (data.get("condicion") or "").strip(),
        "direccion": (data.get("direccion") or "").strip(),
        "ultimo_digito": ruc[-1],
    }
