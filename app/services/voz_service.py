"""
services/voz_service.py — Motor de voz completo para alerta.pe.

Flujo:
  1. Usuario habla → Web Speech API transcribe en el navegador (gratis)
  2. Texto → POST /api/voz/consulta
  3. Agente IA interpreta intención + extrae parámetros
  4. Ejecuta función/query correspondiente
  5. Formatea respuesta en lenguaje natural
  6. Retorna texto → frontend usa Web Speech Synthesis para leer en voz alta

El sistema siempre sabe qué empresa está activa (del JWT).
Nunca pedir el RUC — usar empresa_activa_id del contexto.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.voz import ConsultaVoz

logger = logging.getLogger(__name__)

# Comandos rápidos que no necesitan pasar por el agente IA
COMANDOS_RAPIDOS = {
    "alertas": "consultar_alertas_activas",
    "resumen": "resumen_empresa",
    "pendientes": "consultar_comprobantes_pendientes",
    "pagos": "consultar_pagos_periodo",
}


async def procesar_consulta_voz(
    db: Session,
    usuario_id: str,
    empresa_id: int,
    texto: str,
    nombre_empresa: str = "",
    ruc_empresa: str = "",
    nombre_usuario: str = "",
    rol: str = "contador",
    tono: str = "directo",
) -> dict:
    """
    Procesa una consulta de voz: interpreta, ejecuta y formatea respuesta.

    Returns: {
        respuesta_texto: str,
        respuesta_display: str,
        accion: str | None,
        datos: dict | None,
        empresa_cambiada: bool,
        nueva_empresa_id: int | None,
        confianza_interpretacion: str,
    }
    """
    inicio = datetime.now(timezone.utc)

    # Registrar consulta
    consulta = ConsultaVoz(
        usuario_id=usuario_id,
        empresa_activa_id=empresa_id,
        transcripcion_original=texto,
    )
    db.add(consulta)
    db.flush()

    try:
        from app.agents.agente_consultor import ejecutar_consulta

        resultado = await ejecutar_consulta(
            db=db,
            empresa_id=empresa_id,
            texto=texto,
            nombre_empresa=nombre_empresa,
            ruc_empresa=ruc_empresa,
            nombre_usuario=nombre_usuario,
            rol=rol,
            tono=tono,
        )

        # Actualizar registro de consulta
        elapsed_ms = int((datetime.now(timezone.utc) - inicio).total_seconds() * 1000)
        consulta.intencion_detectada = resultado.get("intencion")
        consulta.parametros_extraidos = resultado.get("parametros")
        consulta.respuesta_entregada = resultado.get("respuesta_texto", "")
        consulta.tiempo_respuesta_ms = elapsed_ms
        db.commit()

        return resultado

    except Exception as e:
        logger.error(f"Error procesando consulta de voz: {e}")
        consulta.error = str(e)[:5000]
        consulta.tiempo_respuesta_ms = int((datetime.now(timezone.utc) - inicio).total_seconds() * 1000)
        db.commit()

        return {
            "respuesta_texto": "Lo siento, hubo un error procesando tu consulta. Intenta de nuevo.",
            "respuesta_display": f"Error: {str(e)[:200]}",
            "accion": None,
            "datos": None,
            "empresa_cambiada": False,
            "nueva_empresa_id": None,
            "confianza_interpretacion": "baja",
        }


async def ejecutar_comando_rapido(
    db: Session,
    empresa_id: int,
    comando: str,
) -> dict:
    """Ejecuta un comando rápido sin pasar por el agente IA."""
    from app.agents.agente_consultor import ejecutar_funcion

    nombre_funcion = COMANDOS_RAPIDOS.get(comando.lower())
    if not nombre_funcion:
        return {
            "respuesta_texto": f"Comando '{comando}' no reconocido",
            "datos": None,
        }

    resultado = await ejecutar_funcion(nombre_funcion, {}, db, empresa_id)
    return {
        "respuesta_texto": resultado.get("resumen_texto", ""),
        "datos": resultado.get("datos"),
    }
