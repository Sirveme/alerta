"""
webapp/routers/voz.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Consulta por VOZ → tarjeta inteligente (zAlerta-01 B.5).

El front (ia.js, barra de consulta IA) transcribe con Web Speech API (es-PE) y manda el texto a
POST /voz/consultar. Aquí:
  1. Parser por KEYWORDS interpreta la intención (qué contribuyente + qué
     busca: multa / vencimiento / novedades). NO requiere IA externa (MVP).
     `interpretar_intencion` queda como hook para enchufar un agente IA.
  2. Consulta la BD (NUNCA SUNAT) → respuesta instantánea.
  3. Devuelve una TARJETA INTELIGENTE (JSON) que el front renderiza.

Multi-tenant: todo filtra por user.estudio_id.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, desc

from db import get_session
from models import Contribuyente, Notificacion, Adjunto, Urgencia
from ..core import TZ_LIMA, urgencia_meta
from ..deps import UsuarioActual, usuario_actual

router = APIRouter(tags=["voz"])

# Keywords de intención
_KW_MULTA = ("multa", "multas", "sancion", "infraccion", "infracciones")
_KW_VENCE = ("vence", "vencimiento", "plazo", "vencer", "vencen", "fecha limite")
_KW_PAGO = ("orden de pago", "ordenes de pago", "deuda", "pagar", "monto", "debe", "cuanto")
_KW_NOVEDAD = ("novedad", "novedades", "nuevo", "nuevos", "ultimo", "ultima", "reciente")


def _normalizar(texto: str) -> str:
    """minúsculas + sin tildes (para matching robusto de voz)."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def interpretar_intencion(texto: str) -> dict:
    """Parser de intención por keywords (HOOK para agente IA futuro).

    Devuelve: {ruc, nombre_buscado, intencion}
      intencion ∈ {multa, vencimiento, pago, novedades}
    """
    norm = _normalizar(texto)

    # RUC explícito (11 dígitos) o DNI/numero largo
    m_ruc = re.search(r"\b(\d{11})\b", norm)
    ruc = m_ruc.group(1) if m_ruc else None

    if any(k in norm for k in _KW_MULTA):
        intencion = "multa"
    elif any(k in norm for k in _KW_VENCE):
        intencion = "vencimiento"
    elif any(k in norm for k in _KW_PAGO):
        intencion = "pago"
    else:
        intencion = "novedades"

    return {"ruc": ruc, "nombre_buscado": norm, "intencion": intencion}


async def _buscar_contribuyente(session, estudio_id, intencion: dict):
    """Encuentra el contribuyente por RUC exacto o por tokens del nombre."""
    if intencion["ruc"]:
        c = await session.scalar(
            select(Contribuyente).where(
                Contribuyente.estudio_id == estudio_id,
                Contribuyente.ruc == intencion["ruc"]))
        if c:
            return c

    # Match por nombre: tokens significativos del texto contra razón social
    contribs = (await session.scalars(
        select(Contribuyente).where(
            Contribuyente.estudio_id == estudio_id))).all()
    if not contribs:
        return None

    norm = intencion["nombre_buscado"]
    mejor, mejor_score = None, 0
    for c in contribs:
        rs = _normalizar(c.razon_social or "")
        if not rs:
            continue
        tokens = [t for t in rs.split() if len(t) >= 4]
        score = sum(1 for t in tokens if t in norm)
        if score > mejor_score:
            mejor, mejor_score = c, score
    return mejor if mejor_score > 0 else None


def _monto_de_texto(texto: str | None) -> str | None:
    """Extrae un monto S/ del asunto/texto si aparece (heurística)."""
    if not texto:
        return None
    m = re.search(r"(?:S/\.?\s?|soles?\s?)([\d.,]+)", texto, re.IGNORECASE)
    return f"S/ {m.group(1)}" if m else None


@router.post("/voz/consultar")
async def consultar_voz(request: Request,
                        user: UsuarioActual = Depends(usuario_actual)):
    body = await request.json()
    texto = (body.get("texto") or "").strip()
    if not texto:
        return JSONResponse({"ok": False, "mensaje": "No se escuchó nada."},
                            status_code=400)

    intencion = interpretar_intencion(texto)

    async with get_session() as session:
        contrib = await _buscar_contribuyente(session, user.estudio_id, intencion)
        if not contrib:
            return JSONResponse({
                "ok": True,
                "tarjeta": {
                    "titulo": "No identifiqué al cliente",
                    "respuesta": "No encontré ese contribuyente. Probá decir el "
                                 "RUC o el nombre completo.",
                    "urgencia": "informativa",
                    "color": urgencia_meta("informativa")["bg"],
                    "transcripcion": texto,
                },
            })

        # Construir query según intención
        q = (select(Notificacion).where(
                Notificacion.contribuyente_id == contrib.id,
                Notificacion.estudio_id == user.estudio_id))

        from models import TipoDocumento
        if intencion["intencion"] == "multa":
            q = q.where(Notificacion.tipo_documento_enum == TipoDocumento.MULTA)
        elif intencion["intencion"] == "pago":
            q = q.where(Notificacion.tipo_documento_enum.in_(
                [TipoDocumento.ORDEN_PAGO, TipoDocumento.COBRANZA_COACTIVA]))
        elif intencion["intencion"] == "vencimiento":
            q = q.where(Notificacion.plazo_vencimiento.is_not(None))

        notif = await session.scalar(
            q.order_by(desc(Notificacion.fecha_publica_sunat),
                       desc(Notificacion.creado_at)).limit(1))

        nombre = contrib.razon_social or contrib.ruc

        if not notif:
            etiqueta = {"multa": "multas", "pago": "órdenes de pago o deudas",
                        "vencimiento": "vencimientos próximos"}.get(
                            intencion["intencion"], "novedades")
            return JSONResponse({"ok": True, "tarjeta": {
                "titulo": nombre,
                "respuesta": f"No hay {etiqueta} registradas para {nombre}.",
                "urgencia": "al_dia",
                "color": urgencia_meta("al_dia")["bg"],
                "contribuyente_id": str(contrib.id),
                "transcripcion": texto,
                "intencion": intencion["intencion"],
            }})

        # Adjunto (PDF) si existe
        adj = await session.scalar(
            select(Adjunto).where(
                Adjunto.notificacion_id == notif.id,
                Adjunto.estudio_id == user.estudio_id).limit(1))

        urg = notif.urgencia.value if notif.urgencia else "sin_clasificar"
        monto = _monto_de_texto(notif.asunto) or _monto_de_texto(notif.texto_html)
        plazo = notif.plazo_vencimiento

        # Respuesta en lenguaje natural
        partes = [f"{nombre}:"]
        if intencion["intencion"] == "multa":
            partes.append("tiene una multa registrada." if notif else "sin multas.")
        elif intencion["intencion"] == "pago":
            partes.append("tiene una orden de pago / deuda.")
        else:
            partes.append("su notificación más reciente es:")
        if notif.asunto:
            partes.append(f"«{notif.asunto[:120]}».")
        if monto:
            partes.append(f"Monto: {monto}.")
        if plazo:
            partes.append(f"Vence el {plazo.astimezone(TZ_LIMA).strftime('%d/%m/%Y')}.")
        respuesta = " ".join(partes)

        tarjeta = {
            "titulo": nombre,
            "ruc": contrib.ruc,
            "respuesta": respuesta,
            "asunto": notif.asunto,
            "monto": monto,
            "tipo_documento": (notif.tipo_documento_enum.value
                               if notif.tipo_documento_enum else None),
            "tipo_documento_label": notif.tipo_documento,
            "plazo": plazo.astimezone(TZ_LIMA).strftime("%d/%m/%Y") if plazo else None,
            "fecha": notif.fecha_envio_sunat,
            "urgencia": urg,
            "color": urgencia_meta(urg)["bg"],
            "resumen_ia": notif.resumen_ia,
            "notificacion_id": str(notif.id),
            "contribuyente_id": str(contrib.id),
            "adjunto_url": (f"/notificaciones/{notif.id}/adjunto/{adj.id}"
                            if adj else None),
            "intencion": intencion["intencion"],
            "transcripcion": texto,
        }

    return JSONResponse({"ok": True, "tarjeta": tarjeta})
