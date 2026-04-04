"""
agents/agente_clasificador.py — Clasifica líneas de comprobantes automáticamente.

Corre en background (Celery) después de ingresar un comprobante.
Por cada línea de detalle sin clasificar:
1. Verifica reglas de ProductoNoDeducible de la empresa
2. Si no hay regla → llama a GPT-4o-mini para clasificar
3. Actualiza: categoria_ia, es_deducible, clasificado_por='ia'

Costo: ~$0.0001 por línea (prompt corto + respuesta JSON mínima)
"""

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.comprobantes import DetalleComprobante, ClasificadoPor
from app.models.documentos import ProductoNoDeducible
from app.models.configuracion import ConfigEmpresa

logger = logging.getLogger(__name__)

PROMPT_CLASIFICACION = """Empresa del rubro {ciiu_descripcion}.
¿El gasto '{descripcion}' es deducible como gasto del negocio?
Responde solo JSON: {{"es_deducible": true|false, "categoria": "string", "razon": "string corta"}}"""


def clasificar_comprobante(db: Session, comprobante_id: int, empresa_id: int):
    """
    Clasifica todas las líneas sin clasificar de un comprobante.
    Primero aplica reglas locales, luego IA para el resto.
    """
    detalles = db.execute(
        select(DetalleComprobante).where(
            DetalleComprobante.comprobante_id == comprobante_id,
            DetalleComprobante.es_deducible == None,  # Solo sin clasificar
        )
    ).scalars().all()

    if not detalles:
        return

    # Cargar reglas de la empresa
    config = db.execute(
        select(ConfigEmpresa).where(ConfigEmpresa.empresa_id == empresa_id)
    ).scalar_one_or_none()

    palabras_no_deducibles = []
    if config and config.palabras_clave_no_deducibles:
        palabras_no_deducibles = config.palabras_clave_no_deducibles

    # Cargar productos no deducibles
    productos_nd = db.execute(
        select(ProductoNoDeducible).where(ProductoNoDeducible.empresa_id == empresa_id)
    ).scalars().all()

    palabras_nd = [p.palabra_clave.lower() for p in productos_nd]
    if isinstance(palabras_no_deducibles, list):
        palabras_nd.extend([p.lower() for p in palabras_no_deducibles])

    pendientes_ia = []

    for detalle in detalles:
        desc_lower = detalle.descripcion.lower()

        # Paso 1: Verificar reglas locales
        matched_nd = any(palabra in desc_lower for palabra in palabras_nd)
        if matched_nd:
            detalle.es_deducible = False
            detalle.categoria_ia = "no_deducible_por_regla"
            detalle.clasificado_por = ClasificadoPor.REGLA
            continue

        # Verificar palabras clave deducibles
        palabras_deducibles = []
        if config and config.palabras_clave_deducibles:
            if isinstance(config.palabras_clave_deducibles, list):
                palabras_deducibles = [p.lower() for p in config.palabras_clave_deducibles]

        matched_ded = any(palabra in desc_lower for palabra in palabras_deducibles)
        if matched_ded:
            detalle.es_deducible = True
            detalle.categoria_ia = "deducible_por_regla"
            detalle.clasificado_por = ClasificadoPor.REGLA
            continue

        # Sin regla → pendiente para IA
        pendientes_ia.append(detalle)

    # Paso 2: Clasificar con IA las pendientes
    if pendientes_ia:
        ciiu = config.ciiu if config else "general"
        _clasificar_lote_ia(db, pendientes_ia, ciiu)

    db.commit()
    logger.info(f"Clasificado comprobante #{comprobante_id}: {len(detalles)} líneas")


def _clasificar_lote_ia(db: Session, detalles: list, ciiu: str):
    """Clasifica un lote de líneas con GPT-4o-mini."""
    try:
        from openai import OpenAI
        from app.core.config import settings

        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY no configurada, saltando clasificación IA")
            return

        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        for detalle in detalles:
            try:
                prompt = PROMPT_CLASIFICACION.format(
                    ciiu_descripcion=ciiu,
                    descripcion=detalle.descripcion[:200],
                )

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.1,
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )

                texto = response.choices[0].message.content.strip()
                texto = texto.replace("```json", "").replace("```", "").strip()
                resultado = json.loads(texto)

                detalle.es_deducible = resultado.get("es_deducible", None)
                detalle.categoria_ia = resultado.get("categoria", "sin_categoria")
                detalle.clasificado_por = ClasificadoPor.IA

            except Exception as e:
                logger.debug(f"Error clasificando línea '{detalle.descripcion[:50]}': {e}")
                # No fallar — dejar sin clasificar

    except ImportError:
        logger.warning("OpenAI no disponible para clasificación")
