"""
services/alertas_service.py — Generador centralizado de alertas.

Cada alerta tiene nivel (urgente/importante/info) y tipo predefinido.
Se puede publicar opcionalmente en Redis para WebSocket en tiempo real.
"""

import logging
import os
from typing import Optional

from sqlalchemy.orm import Session

from app.models.alertas import Alerta, OrigenAlerta, EstadoAlerta

logger = logging.getLogger(__name__)

# Mapeo tipo → (nivel, origen)
# nivel: urgente | importante | info
TIPOS_ALERTA = {
    "pago_sin_comprobante":    ("importante", OrigenAlerta.SISTEMA),
    "duplicado_exacto":        ("importante", OrigenAlerta.SISTEMA),
    "posible_duplicado":       ("importante", OrigenAlerta.SISTEMA),
    "cruce_exitoso":           ("info",       OrigenAlerta.SISTEMA),
    "comprobante_nuevo":       ("info",       OrigenAlerta.SISTEMA),
    "pago_parcial":            ("importante", OrigenAlerta.SISTEMA),
    "xml_parse_error":         ("urgente",    OrigenAlerta.SISTEMA),
    "correo_sin_empresa":      ("importante", OrigenAlerta.SISTEMA),
    "sunat_coactiva":          ("urgente",    OrigenAlerta.SUNAT),
    "sunafil_notificacion":    ("urgente",    OrigenAlerta.SUNAFIL),
    "anomalia_facturacion":    ("importante", OrigenAlerta.SISTEMA),
}


def crear_alerta(
    db: Session,
    empresa_id: Optional[int],
    origen: OrigenAlerta,
    nivel: str,
    titulo: str,
    descripcion: str,
    comprobante_id: Optional[int] = None,
    pago_id: Optional[int] = None,
) -> Alerta:
    """Crea una alerta directamente con todos los parámetros."""
    estado_map = {"urgente": EstadoAlerta.ACTIVA, "importante": EstadoAlerta.ACTIVA, "info": EstadoAlerta.ACTIVA}

    alerta = Alerta(
        empresa_id=empresa_id or 0,
        origen=origen,
        estado=estado_map.get(nivel, EstadoAlerta.ACTIVA),
        titulo=titulo[:255],
        descripcion=descripcion,
        comprobante_id=comprobante_id,
        pago_id=pago_id,
    )
    db.add(alerta)
    db.commit()
    db.refresh(alerta)

    # Publicar en Redis para WebSocket (si disponible)
    _publicar_redis(empresa_id, nivel, titulo, descripcion)

    logger.info(f"Alerta creada #{alerta.id}: [{nivel}] {titulo}")
    return alerta


def crear_alerta_por_tipo(
    db: Session,
    empresa_id: Optional[int],
    tipo: str,
    mensaje: str,
    referencia_id: Optional[int] = None,
    referencia_tabla: Optional[str] = None,
) -> Optional[Alerta]:
    """
    Crea una alerta usando un tipo predefinido de TIPOS_ALERTA.
    El nivel y origen se determinan automáticamente por el tipo.
    """
    config = TIPOS_ALERTA.get(tipo)
    if not config:
        logger.warning(f"Tipo de alerta desconocido: {tipo}")
        return None

    nivel, origen = config

    # Título legible según tipo
    titulos = {
        "pago_sin_comprobante": "Pago sin comprobante",
        "duplicado_exacto": "Comprobante duplicado detectado",
        "posible_duplicado": "Posible comprobante duplicado",
        "cruce_exitoso": "Pago cruzado exitosamente",
        "comprobante_nuevo": "Nuevo comprobante registrado",
        "pago_parcial": "Posible pago parcial",
        "xml_parse_error": "Error procesando XML",
        "correo_sin_empresa": "Correo sin empresa identificada",
        "sunat_coactiva": "Coactiva SUNAT",
        "sunafil_notificacion": "Notificación SUNAFIL",
        "anomalia_facturacion": "Anomalía en facturación",
    }

    titulo = titulos.get(tipo, tipo.replace("_", " ").title())

    # Mapear referencia a pago_id o comprobante_id
    pago_id = referencia_id if referencia_tabla == "pagos" else None
    comprobante_id = referencia_id if referencia_tabla == "comprobantes" else None

    return crear_alerta(
        db=db,
        empresa_id=empresa_id,
        origen=origen,
        nivel=nivel,
        titulo=titulo,
        descripcion=mensaje,
        comprobante_id=comprobante_id,
        pago_id=pago_id,
    )


def _publicar_redis(empresa_id: Optional[int], nivel: str, titulo: str, mensaje: str):
    """
    Publica alerta en Redis channel para WebSocket en tiempo real.
    Fallo silencioso si Redis no está disponible.
    """
    if not empresa_id:
        return

    try:
        import json
        import redis as redis_lib

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis_lib.from_url(redis_url)
        r.publish(
            f"alertas:{empresa_id}",
            json.dumps({
                "tipo": "alerta",
                "nivel": nivel,
                "titulo": titulo,
                "mensaje": mensaje,
            }),
        )
    except Exception:
        # Redis no disponible — no es fatal
        pass
