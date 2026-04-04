"""
routers/publico.py — Endpoints públicos sin autenticación, con rate limiting.

/api/ruc/{ruc}: consulta RUC en SUNAT (cache 24h en Redis).
/api/verificar-comprobante: verificar existencia de comprobante.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db

router = APIRouter(prefix="/api", tags=["público"])


@router.get("/ruc/{ruc}")
async def consultar_ruc(ruc: str):
    """
    Datos básicos de un RUC. Público, sin login.
    Cache 24h en Redis (datos de RUC no cambian frecuentemente).
    """
    if not ruc or len(ruc) != 11 or not ruc.isdigit():
        raise HTTPException(status_code=400, detail="RUC debe ser 11 dígitos")

    # Intentar cache Redis
    import os
    cache_key = f"ruc:{ruc}"
    try:
        import redis as redis_lib
        r = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        cached = r.get(cache_key)
        if cached:
            import json
            return json.loads(cached)
    except Exception:
        pass

    # Consultar SUNAT
    from app.services.sunat_service import consultar_ruc
    datos = await consultar_ruc(ruc)

    # Cachear en Redis 24h
    try:
        import json
        r.setex(cache_key, 86400, json.dumps(datos, default=str))
    except Exception:
        pass

    return datos


@router.get("/verificar-comprobante/{ruc_emisor}/{serie}/{correlativo}")
async def verificar_comprobante_api(
    ruc_emisor: str, serie: str, correlativo: str,
    db: Session = Depends(get_db),
):
    """
    Verifica si un comprobante existe y es válido.
    Público, sin registro. Retorna solo datos no privados.
    """
    from sqlalchemy import select
    from app.models.portal import EnvioPortal

    # Buscar en nuestro sistema
    envio = db.execute(
        select(EnvioPortal).where(
            EnvioPortal.ruc_emisor == ruc_emisor,
            EnvioPortal.serie == serie,
            EnvioPortal.correlativo == correlativo,
        ).order_by(EnvioPortal.created_at.desc())
    ).scalar_one_or_none()

    if envio:
        return {
            "encontrado": True,
            "fuente": "reenviame.pe",
            "estado": envio.estado_validacion.value,
            "tipo": envio.tipo_comprobante,
            "fecha_emision": str(envio.fecha_emision) if envio.fecha_emision else None,
            "total": float(envio.total) if envio.total else None,
        }

    # Buscar en comprobantes internos
    from app.models.comprobantes import Comprobante
    comp = db.execute(
        select(Comprobante).where(
            Comprobante.ruc_emisor == ruc_emisor,
            Comprobante.serie == serie,
            Comprobante.correlativo == correlativo,
            Comprobante.deleted_at == None,
        )
    ).scalar_one_or_none()

    if comp:
        return {
            "encontrado": True,
            "fuente": "alerta.pe",
            "estado": comp.estado.value,
            "tipo": comp.tipo.value,
            "fecha_emision": str(comp.fecha_emision),
            "total": float(comp.total),
        }

    return {"encontrado": False, "mensaje": "Comprobante no encontrado"}
