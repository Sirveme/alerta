"""
webapp/core.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Configuración compartida de la WebApp: instancia Jinja2Templates con
filtros en español (fechas dd/MM/YYYY, hora Lima) y helpers de presentación
(color/etiqueta de urgencia y tipo de documento).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from models import ETIQUETA_TIPO_DOCUMENTO

TZ_LIMA = ZoneInfo("America/Lima")
_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _DIR / "templates"
STATIC_DIR = _DIR / "static"

# WhatsApp de SOPORTE (Perú Sistemas Pro) al que el empresario escribe para
# pedir su clave (zAlerta-06 C.3). CONFIGURABLE por env, no hardcodeado disperso.
WHATSAPP_SOPORTE = os.getenv("WHATSAPP_SOPORTE", "51967317946")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ─────────────────────────────────────────────────────────────────────
# Filtros Jinja
# ─────────────────────────────────────────────────────────────────────
def fecha_lima(valor: datetime | None, con_hora: bool = False) -> str:
    """datetime → 'dd/MM/YYYY' (o con hora) en zona Lima."""
    if not valor:
        return "—"
    if isinstance(valor, str):
        return valor  # fechas SUNAT ya vienen como dd/MM/YYYY
    try:
        v = valor.astimezone(TZ_LIMA) if valor.tzinfo else valor.replace(tzinfo=TZ_LIMA)
    except Exception:
        v = valor
    return v.strftime("%d/%m/%Y %H:%M") if con_hora else v.strftime("%d/%m/%Y")


def fecha_hora_lima(valor: datetime | None) -> str:
    return fecha_lima(valor, con_hora=True)


# Paleta de urgencia (zAlerta-01 B.2): color pleno, no pastel.
COLOR_URGENCIA = {
    "critica":   {"bg": "#7C1D1D", "fg": "#FFFFFF", "label": "Crítica"},
    "urgente":   {"bg": "#DC2626", "fg": "#FFFFFF", "label": "Urgente"},
    "importante": {"bg": "#D97706", "fg": "#FFFFFF", "label": "Importante"},
    "informativa": {"bg": "#6B7280", "fg": "#FFFFFF", "label": "Informativa"},
    "sin_clasificar": {"bg": "#9CA3AF", "fg": "#1F2937", "label": "Sin clasificar"},
    "al_dia":    {"bg": "#1F9D55", "fg": "#FFFFFF", "label": "Al día"},
}


def urgencia_meta(valor) -> dict:
    clave = valor.value if hasattr(valor, "value") else (valor or "sin_clasificar")
    return COLOR_URGENCIA.get(clave, COLOR_URGENCIA["sin_clasificar"])


def etiqueta_tipo_doc(valor) -> str:
    clave = valor.value if hasattr(valor, "value") else valor
    return ETIQUETA_TIPO_DOCUMENTO.get(clave, "Otros")


# ─────────────────────────────────────────────────────────────────────
# Cuerpo de notificación: presentar legible, NUNCA JSON crudo (zAlerta-08 #3)
# ─────────────────────────────────────────────────────────────────────
_CAMPOS_CUERPO = ("contenido", "mensaje", "texto", "cuerpo", "desMensaje",
                  "msjMensaje", "descripcion", "observacion", "detalle")
_CAMPOS_NUMERO = ("numero", "nroDocumento", "numeroDocumento", "nro", "documento")


def _fmt_numero(valor) -> str | None:
    if not valor:
        return None
    s = str(valor)
    for viejo in ("No:", "N°:", "Nro:", "nro:", "N �:"):
        s = s.replace(viejo, "N° ")
    return s.strip()


def cuerpo_notificacion(texto_html) -> dict:
    """Presenta el cuerpo del mensaje de forma legible. Si `texto_html` es un
    JSON crudo (algunos mensajes de SUNAT lo son), extrae número de documento,
    cuerpo y deja el resto como 'detalles técnicos' — nunca vuelca el JSON.

    Devuelve {numero, cuerpo (Markup|None), tecnicos [(k,v)], es_json}.
    """
    raw = (texto_html or "").strip()
    if not raw:
        return {"numero": None, "cuerpo": None, "tecnicos": [], "es_json": False}

    if raw[:1] in "{[":
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            numero = next((data.get(k) for k in _CAMPOS_NUMERO if data.get(k)), None)
            cuerpo = next((data.get(k).strip() for k in _CAMPOS_CUERPO
                           if isinstance(data.get(k), str) and data.get(k).strip()),
                          None)
            tecnicos = [(k, v) for k, v in data.items()
                        if not isinstance(v, (dict, list)) and k not in _CAMPOS_CUERPO]
            return {"numero": _fmt_numero(numero),
                    "cuerpo": Markup(cuerpo) if cuerpo else None,
                    "tecnicos": tecnicos, "es_json": True}

    # No es JSON: contenido HTML normal del visor SUNAT.
    return {"numero": None, "cuerpo": Markup(raw), "tecnicos": [], "es_json": False}


# Escalera de precios Fundador 2026: la fuente ÚNICA vive en precios.py
# (raíz), compartida por landing, candado de precio y cobro (zAlerta-23/24).
from precios import escalera_para as _escalera_para  # noqa: E402


def escalera_precios() -> dict:
    """Escalera para la landing, calculada con la fecha del servidor (Lima)."""
    return _escalera_para(datetime.now(TZ_LIMA))


templates.env.filters["fecha_lima"] = fecha_lima
templates.env.filters["fecha_hora_lima"] = fecha_hora_lima
templates.env.filters["urgencia_meta"] = urgencia_meta
templates.env.filters["etiqueta_tipo_doc"] = etiqueta_tipo_doc
templates.env.filters["cuerpo_notificacion"] = cuerpo_notificacion
templates.env.globals["ETIQUETA_TIPO_DOCUMENTO"] = ETIQUETA_TIPO_DOCUMENTO
templates.env.globals["WHATSAPP_SOPORTE"] = WHATSAPP_SOPORTE
templates.env.globals["escalera_precios"] = escalera_precios
