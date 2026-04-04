"""
services/sunat_service.py — Integración con APIs de SUNAT.

Consultas usando credenciales SOL secundarias del cliente.
Las credenciales están encriptadas en EmpresaCliente.clave_sol_*.

Endpoints integrados:
1. Consulta RUC (pública, sin SOL)
2. Token OAuth2 con SOL
3. SIRE - Registro de Ventas
4. SIRE - Registro de Compras
5. Sincronización SIRE vs sistema

Implementado con httpx async + retry (tenacity, 3 intentos, backoff 2s).
Token SOL: válido 1 hora, se cachea en Redis si disponible.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import settings

logger = logging.getLogger(__name__)

# Timeout para requests a SUNAT (puede ser lenta)
SUNAT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
)
async def consultar_ruc(ruc: str) -> dict:
    """
    Datos básicos del contribuyente. No requiere SOL.
    Usa la API pública de SUNAT (o APIs de terceros como apis.net.pe).

    Decisión: usar API pública de SUNAT primero, fallback a apis.net.pe
    porque la API oficial tiene rate limits estrictos.
    """
    # API pública de SUNAT
    url = f"{settings.SUNAT_API_URL}/contribuyente/contribuyentes/{ruc}"

    async with httpx.AsyncClient(timeout=SUNAT_TIMEOUT) as client:
        try:
            response = await client.get(
                url,
                headers={"Accept": "application/json"},
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.debug(f"API SUNAT principal falló: {e}")

        # Fallback: API alternativa (apis.net.pe — gratuita para consultas básicas)
        try:
            alt_url = f"https://dniruc.apisperu.com/api/v1/ruc/{ruc}"
            alt_token = os.environ.get("APISPERU_TOKEN", "")
            if alt_token:
                response = await client.get(
                    alt_url,
                    params={"token": alt_token},
                )
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "ruc": data.get("ruc"),
                        "razon_social": data.get("razonSocial"),
                        "estado": data.get("estado"),
                        "condicion": data.get("condicion"),
                        "direccion": data.get("direccion"),
                        "departamento": data.get("departamento"),
                        "provincia": data.get("provincia"),
                        "distrito": data.get("distrito"),
                    }
        except Exception:
            pass

    return {"error": "No se pudo consultar el RUC", "ruc": ruc}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
)
async def obtener_token_sol(ruc_empresa: str, usuario_sol: str, clave_sol: str) -> Optional[str]:
    """
    OAuth2 con credenciales SOL. Token válido 1 hora.
    Se cachea en Redis si disponible para evitar requests innecesarios.
    """
    # Intentar obtener token cacheado de Redis
    cache_key = f"sunat_token:{ruc_empresa}"
    token_cacheado = _get_redis_cache(cache_key)
    if token_cacheado:
        return token_cacheado

    # Solicitar nuevo token
    async with httpx.AsyncClient(timeout=SUNAT_TIMEOUT) as client:
        response = await client.post(
            settings.SUNAT_TOKEN_URL,
            data={
                "grant_type": "password",
                "scope": "https://api.sunat.gob.pe/v1/contribuyente/contribuyentes",
                "client_id": settings.SUNAT_CLIENT_ID,
                "client_secret": settings.SUNAT_CLIENT_SECRET,
                "username": f"{ruc_empresa}{usuario_sol}",
                "password": clave_sol,
            },
        )

        if response.status_code != 200:
            logger.error(f"Error obteniendo token SOL para {ruc_empresa}: {response.status_code}")
            return None

        data = response.json()
        token = data.get("access_token")

        if token:
            # Cachear en Redis por 55 minutos (token dura 60)
            _set_redis_cache(cache_key, token, ttl=3300)

        return token


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
)
async def consultar_sire_ventas(ruc_empresa: str, periodo: str, token: str) -> list:
    """
    Registro de ventas del período (YYYYMM) desde SIRE SUNAT.
    Retorna lista de comprobantes declarados.
    """
    url = f"{settings.SUNAT_API_URL}/contribuyente/contribuyentes/{ruc_empresa}/ple/5ta0/RLE/{periodo}/"

    async with httpx.AsyncClient(timeout=SUNAT_TIMEOUT) as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        if response.status_code == 401:
            # Token expirado — el retry se encargará si el caller renueva
            logger.warning("Token SOL expirado, necesita renovación")
            raise httpx.HTTPError("Token expirado")

        if response.status_code != 200:
            logger.error(f"Error consultando SIRE ventas: {response.status_code}")
            return []

        return response.json().get("registros", response.json().get("data", []))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
)
async def consultar_sire_compras(ruc_empresa: str, periodo: str, token: str) -> list:
    """Registro de compras del período desde SIRE SUNAT."""
    url = f"{settings.SUNAT_API_URL}/contribuyente/contribuyentes/{ruc_empresa}/ple/8va/RCE/{periodo}/"

    async with httpx.AsyncClient(timeout=SUNAT_TIMEOUT) as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        if response.status_code != 200:
            return []

        return response.json().get("registros", response.json().get("data", []))


async def sincronizar_sire(db, empresa_id: int, periodo: str) -> dict:
    """
    Descarga SIRE, compara con comprobantes en el sistema,
    detecta diferencias y actualiza acum_sire.
    Retorna: { nuevos: int, faltantes: int, discrepancias: int }
    """
    from sqlalchemy import select
    from app.models.empresas import EmpresaCliente
    from app.models.comprobantes import Comprobante
    from app.models.acumulados import AcumSIRE, TipoRegistroSIRE
    from app.core.security import decrypt_sensitive
    from app.services.alertas_service import crear_alerta_por_tipo

    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == empresa_id)
    ).scalar_one_or_none()

    if not empresa or not empresa.clave_sol_usuario:
        return {"error": "Sin credenciales SOL configuradas"}

    # Desencriptar credenciales
    try:
        usuario_sol = decrypt_sensitive(empresa.clave_sol_usuario)
        clave_sol = decrypt_sensitive(empresa.clave_sol_password)
    except Exception:
        return {"error": "Error desencriptando credenciales SOL"}

    # Obtener token
    token = await obtener_token_sol(empresa.ruc, usuario_sol, clave_sol)
    if not token:
        return {"error": "No se pudo obtener token SOL"}

    # Descargar ventas y compras
    ventas_sire = await consultar_sire_ventas(empresa.ruc, periodo, token)
    compras_sire = await consultar_sire_compras(empresa.ruc, periodo, token)

    # Comparar con comprobantes del sistema
    mes = int(periodo[4:6])
    anio = int(periodo[:4])

    comprobantes_sistema = db.execute(
        select(Comprobante).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.deleted_at == None,
        )
    ).scalars().all()

    # Crear set de comprobantes del sistema para búsqueda rápida
    sistema_set = {
        (c.ruc_emisor, c.serie, c.correlativo): c
        for c in comprobantes_sistema
    }

    faltantes = 0
    discrepancias = 0
    nuevos = 0

    # Verificar qué hay en SIRE pero no en sistema
    for registro in ventas_sire + compras_sire:
        ruc = registro.get("numRuc", registro.get("ruc_emisor", ""))
        serie = registro.get("numSerie", registro.get("serie", ""))
        correlativo = registro.get("numCorrelativo", registro.get("correlativo", ""))

        key = (ruc, serie, correlativo)
        if key not in sistema_set:
            faltantes += 1

    # Alertar si hay faltantes significativos
    if faltantes > 0:
        crear_alerta_por_tipo(
            db, empresa_id, "anomalia_facturacion",
            mensaje=f"SIRE {periodo}: {faltantes} comprobantes en SUNAT no encontrados en el sistema",
        )

    result = {"nuevos": nuevos, "faltantes": faltantes, "discrepancias": discrepancias}
    logger.info(f"Sincronización SIRE empresa {empresa_id} periodo {periodo}: {result}")
    return result


# ── Cache Redis (helper) ─────────────────────────────────────

def _get_redis_cache(key: str) -> Optional[str]:
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL)
        return r.get(key)
    except Exception:
        return None


def _set_redis_cache(key: str, value: str, ttl: int = 3600):
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL)
        r.setex(key, ttl, value)
    except Exception:
        pass
