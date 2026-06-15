"""
webapp/core.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Configuración compartida de la WebApp: instancia Jinja2Templates con
filtros en español (fechas dd/MM/YYYY, hora Lima) y helpers de presentación
(color/etiqueta de urgencia y tipo de documento).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

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


templates.env.filters["fecha_lima"] = fecha_lima
templates.env.filters["fecha_hora_lima"] = fecha_hora_lima
templates.env.filters["urgencia_meta"] = urgencia_meta
templates.env.filters["etiqueta_tipo_doc"] = etiqueta_tipo_doc
templates.env.globals["ETIQUETA_TIPO_DOCUMENTO"] = ETIQUETA_TIPO_DOCUMENTO
templates.env.globals["WHATSAPP_SOPORTE"] = WHATSAPP_SOPORTE
