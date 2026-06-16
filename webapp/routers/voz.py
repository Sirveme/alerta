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

# Palabras a ignorar al buscar el NOMBRE del cliente (formas jurídicas, verbos
# de consulta, conectores). Priorizamos la palabra distintiva del nombre.
_STOPWORDS = {
    "eirl", "sac", "srl", "sociedad", "anonima", "anonimas", "empresa",
    "empresas", "comercial", "servicios", "multiservicios", "negocios",
    "contribuyente", "cliente", "clientes", "ruc", "para", "sobre", "tiene",
    "tienes", "hay", "dime", "dile", "muestra", "muestrame", "busca", "buscar",
    "quiero", "saber", "ver", "novedad", "novedades", "como", "esta", "estan",
    "algo", "alguna", "alguno", "este", "esta", "este", "del", "las", "los",
    "una", "uno", "que", "con", "por", "mis", "todo", "todos", "general",
}
# Todas las keywords de intención (planas) — tampoco son nombre de cliente.
_INTENT_KW = set()
for _grp in (_KW_MULTA, _KW_VENCE, _KW_PAGO, _KW_NOVEDAD):
    for _kw in _grp:
        _INTENT_KW.update(_kw.split())


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


async def _candidatos(session, user: UsuarioActual):
    """Contribuyentes visibles para el usuario (multi-tenant + empresario)."""
    if user.es_empresario:
        cond = Contribuyente.cuenta_empresario_id == user.estudio_id
    else:
        cond = Contribuyente.estudio_id == user.estudio_id
    return list(await session.scalars(select(Contribuyente).where(cond)))


def _terminos_busqueda(norm: str) -> list[str]:
    """Tokens distintivos del nombre buscado (sin formas jurídicas ni verbos)."""
    palabras = re.findall(r"[a-z0-9]+", norm)
    return [p for p in palabras
            if len(p) >= 4 and p not in _STOPWORDS and p not in _INTENT_KW]


def _coincidencias(contribs, intencion: dict) -> list:
    """Match PARCIAL e insensible a may/min y acentos. RUC exacto tiene prioridad."""
    # RUC exacto o parcial (si dijo dígitos)
    if intencion["ruc"]:
        exactos = [c for c in contribs if c.ruc == intencion["ruc"]]
        if exactos:
            return exactos

    terminos = _terminos_busqueda(intencion["nombre_buscado"])
    # Términos numéricos: pueden ser parte del RUC.
    num = [t for t in terminos if t.isdigit()]
    txt = [t for t in terminos if not t.isdigit()]

    matches = []
    for c in contribs:
        rs = _normalizar(c.razon_social or "")
        ruc = c.ruc or ""
        if any(t in rs for t in txt) or any(t in ruc for t in num):
            matches.append(c)
    return matches


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
        contribs = await _candidatos(session, user)
        matches = _coincidencias(contribs, intencion)

        if not matches:
            return JSONResponse({
                "ok": True,
                "tarjeta": {
                    "titulo": "No identifiqué al cliente",
                    "respuesta": "No encontré ese cliente. Prueba con otra parte "
                                 "del nombre o el RUC.",
                    "urgencia": "informativa",
                    "color": urgencia_meta("informativa")["bg"],
                    "transcripcion": texto,
                },
            })

        if len(matches) > 1:
            opciones = [{"id": str(c.id), "nombre": c.razon_social or c.ruc,
                         "ruc": c.ruc} for c in matches[:6]]
            listado = " · ".join(o["nombre"] for o in opciones)
            return JSONResponse({"ok": True, "tarjeta": {
                "titulo": f"Encontré {len(matches)} clientes",
                "respuesta": f"Encontré {len(matches)}: {listado}. ¿Cuál de ellos?",
                "urgencia": "informativa",
                "color": urgencia_meta("informativa")["bg"],
                "opciones": opciones,
                "transcripcion": texto,
            }})

        contrib = matches[0]

        # Construir query según intención (estudio REAL del RUC → vale para
        # estudio y empresario, ya validado el acceso por _candidatos).
        q = (select(Notificacion).where(
                Notificacion.contribuyente_id == contrib.id,
                Notificacion.estudio_id == contrib.estudio_id))

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
                Adjunto.estudio_id == contrib.estudio_id).limit(1))

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
            "adjunto_url": (f"/adjuntos/{adj.id}/ver" if adj else None),
            "intencion": intencion["intencion"],
            "transcripcion": texto,
        }

    return JSONResponse({"ok": True, "tarjeta": tarjeta})
