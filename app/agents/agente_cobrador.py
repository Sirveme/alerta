"""
agents/agente_cobrador.py — Genera mensajes de cobranza personalizados (notificado.pro).

Escalamiento según días vencidos:
  Nivel 1 (1-7 días):   tono amable, recordatorio simple
  Nivel 2 (8-15 días):  tono firme, mencionar consecuencias
  Nivel 3 (>15 días):   tono formal, aviso de cobranza

El agente genera el mensaje; un humano lo aprueba antes de enviar
(o se configura como automático por tenant).

Canales: WhatsApp, SMS, Email. El mensaje se adapta al canal.
"""

import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.deudas import Deuda, EstadoDeuda

logger = logging.getLogger(__name__)

PROMPTS_POR_NIVEL = {
    1: """Genera un mensaje de recordatorio AMABLE para cobrar una deuda.
Tono: amigable, como un aviso entre conocidos. Sin amenazas.
Canal: {canal}. Máximo {max_chars} caracteres.
Datos: Deudor: {nombre}. Concepto: {concepto}. Monto: S/ {monto}. Vencimiento: {vencimiento}.
Empresa cobradora: {empresa}.
Responde SOLO el mensaje, sin explicaciones.""",

    2: """Genera un mensaje de cobranza FIRME pero respetuoso.
Tono: profesional, mencionando que el plazo venció y las consecuencias de no pagar.
Canal: {canal}. Máximo {max_chars} caracteres.
Datos: Deudor: {nombre}. Concepto: {concepto}. Monto: S/ {monto}. Días vencido: {dias_vencido}.
Empresa cobradora: {empresa}.
Responde SOLO el mensaje.""",

    3: """Genera un mensaje de AVISO FORMAL de cobranza.
Tono: muy formal, mencionar que se derivará a cobranza externa si no se regulariza.
Canal: {canal}. Máximo {max_chars} caracteres.
Datos: Deudor: {nombre}. Concepto: {concepto}. Monto: S/ {monto}. Días vencido: {dias_vencido}.
Empresa cobradora: {empresa}.
Responde SOLO el mensaje.""",
}

# Límites de caracteres por canal
MAX_CHARS = {
    "whatsapp": 500,
    "sms": 160,
    "email": 2000,
}


def generar_mensaje_cobranza(
    db: Session,
    deuda_id: int,
    canal: str = "whatsapp",
) -> dict:
    """
    Genera un mensaje de cobranza personalizado para una deuda.

    Returns: {
        mensaje: str,
        nivel: int,
        canal: str,
        requiere_aprobacion: bool,
    }
    """
    deuda = db.execute(
        select(Deuda).where(Deuda.id == deuda_id, Deuda.deleted_at == None)
    ).scalar_one_or_none()

    if not deuda:
        return {"error": "Deuda no encontrada"}

    # Calcular nivel de escalamiento por días vencidos
    hoy = date.today()
    dias_vencido = (hoy - deuda.fecha_vencimiento).days if hoy > deuda.fecha_vencimiento else 0

    if dias_vencido <= 7:
        nivel = 1
    elif dias_vencido <= 15:
        nivel = 2
    else:
        nivel = 3

    # Obtener nombre de la empresa cobradora
    from app.models.empresas import EmpresaCliente
    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == deuda.empresa_id)
    ).scalar_one_or_none()
    nombre_empresa = empresa.razon_social if empresa else "la empresa"

    max_chars = MAX_CHARS.get(canal, 500)
    prompt = PROMPTS_POR_NIVEL[nivel].format(
        canal=canal,
        max_chars=max_chars,
        nombre=deuda.deudor_nombre,
        concepto=deuda.concepto,
        monto=f"{deuda.monto_total - deuda.monto_pagado:,.2f}",
        vencimiento=deuda.fecha_vencimiento.strftime("%d/%m/%Y"),
        dias_vencido=dias_vencido,
        empresa=nombre_empresa,
    )

    try:
        from openai import OpenAI
        from app.core.config import settings

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        mensaje = response.choices[0].message.content.strip()

        # Truncar si excede el límite del canal
        if len(mensaje) > max_chars:
            mensaje = mensaje[:max_chars - 3] + "..."

    except Exception as e:
        logger.error(f"Error generando mensaje de cobranza: {e}")
        # Fallback: mensaje genérico
        mensaje = (
            f"Estimado/a {deuda.deudor_nombre}, le recordamos que tiene pendiente "
            f"el pago de {deuda.concepto} por S/ {deuda.monto_total - deuda.monto_pagado:,.2f}. "
            f"Agradecemos su pronta atención. — {nombre_empresa}"
        )

    return {
        "mensaje": mensaje,
        "nivel": nivel,
        "canal": canal,
        "dias_vencido": dias_vencido,
        "requiere_aprobacion": nivel <= 2,  # Nivel 3 podría ser automático
        "deuda_id": deuda_id,
    }


def generar_mensajes_lote(db: Session, empresa_id: int, canal: str = "whatsapp") -> list:
    """
    Genera mensajes para todas las deudas vencidas de una empresa.
    Para procesamiento en lote (cron nocturno).
    """
    deudas = db.execute(
        select(Deuda).where(
            Deuda.empresa_id == empresa_id,
            Deuda.estado.in_([EstadoDeuda.VENCIDO, EstadoDeuda.EN_GESTION]),
            Deuda.deleted_at == None,
        )
    ).scalars().all()

    mensajes = []
    for deuda in deudas:
        resultado = generar_mensaje_cobranza(db, deuda.id, canal)
        mensajes.append(resultado)

    return mensajes
