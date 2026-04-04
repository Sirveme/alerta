"""
parsers/banco_parser.py — Parsers de notificaciones bancarias por email.

Cada banco peruano tiene su formato de email de notificación.
Se implementan detectores y extractores robustos con regex que toleran
variaciones de formato.

Si un campo no se puede extraer con confianza → None, nunca inventar datos.

Decisiones técnicas:
- Regex compilados para performance (se ejecutan en cada correo).
- HTML se limpia con regex básico (no se usa BeautifulSoup para evitar
  dependencia adicional). El HTML de bancos es simple y predecible.
- Montos: se normalizan removiendo separadores de miles y convirtiendo
  coma decimal a punto. Perú usa formato: 1,234.56 o 1.234,56
"""

import re
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PagoRecibidoData:
    """Datos extraídos de una notificación bancaria."""
    monto: Optional[Decimal] = None
    moneda: str = "PEN"
    fecha: Optional[datetime] = None
    origen: Optional[str] = None       # Nombre del pagador
    destino: Optional[str] = None      # Nombre del receptor
    referencia: Optional[str] = None   # Número de operación
    concepto: Optional[str] = None


# ── Detección de banco ───────────────────────────────────────

# Patrones de remitente/asunto por banco
_BANCO_PATTERNS = {
    "yape": [
        re.compile(r"yape", re.I),
        re.compile(r"yapeo", re.I),
        re.compile(r"@bcp\.com\.pe.*yape", re.I),
    ],
    "plin": [
        re.compile(r"plin", re.I),
        re.compile(r"@bbva\.pe.*plin", re.I),
    ],
    "bcp": [
        re.compile(r"@bcp\.com\.pe", re.I),
        re.compile(r"viabcp", re.I),
        re.compile(r"Banco de Cr.dito", re.I),
    ],
    "bbva": [
        re.compile(r"@bbva\.pe", re.I),
        re.compile(r"bbva\s*continental", re.I),
        re.compile(r"bbva\.pe", re.I),
    ],
    "interbank": [
        re.compile(r"@interbank\.pe", re.I),
        re.compile(r"interbank", re.I),
    ],
    "scotiabank": [
        re.compile(r"@scotiabank\.com\.pe", re.I),
        re.compile(r"scotiabank", re.I),
    ],
    "bnacion": [
        re.compile(r"@bn\.com\.pe", re.I),
        re.compile(r"banco\s*de\s*la\s*naci.n", re.I),
    ],
}


def detectar_banco(remitente: str, asunto: str) -> Optional[str]:
    """
    Detecta el banco emisor de la notificación.
    Retorna: 'bcp'|'bbva'|'interbank'|'scotiabank'|'bnacion'|'yape'|'plin'|None

    Prioridad: Yape/Plin primero (son sub-servicios de BCP/BBVA),
    luego bancos por remitente y asunto.
    """
    texto = f"{remitente} {asunto}"

    # Yape y Plin primero (antes que BCP/BBVA genérico)
    for banco in ["yape", "plin"]:
        for pattern in _BANCO_PATTERNS[banco]:
            if pattern.search(texto):
                return banco

    # Otros bancos
    for banco in ["bcp", "bbva", "interbank", "scotiabank", "bnacion"]:
        for pattern in _BANCO_PATTERNS[banco]:
            if pattern.search(texto):
                return banco

    return None


# ── Parser genérico ──────────────────────────────────────────

def parsear_notificacion(banco: str, html: str) -> Optional[dict]:
    """
    Dispatcher: llama al parser específico del banco.
    Retorna dict con campos de PagoRecibidoData o None si falla.
    """
    parsers = {
        "yape": parsear_notificacion_yape,
        "plin": parsear_notificacion_plin,
        "bcp": parsear_notificacion_bcp,
        "bbva": parsear_notificacion_bbva,
        "interbank": parsear_notificacion_interbank,
        "scotiabank": parsear_notificacion_scotiabank,
        "bnacion": parsear_notificacion_bnacion,
    }

    parser = parsers.get(banco)
    if not parser:
        logger.warning(f"No hay parser para banco: {banco}")
        return None

    try:
        resultado = parser(html)
        if resultado:
            return {
                "monto": resultado.monto,
                "moneda": resultado.moneda,
                "fecha": resultado.fecha,
                "origen": resultado.origen,
                "destino": resultado.destino,
                "referencia": resultado.referencia,
                "concepto": resultado.concepto,
            }
    except Exception as e:
        logger.error(f"Error parseando notificación {banco}: {e}")

    return None


# ── Parsers por banco ────────────────────────────────────────

def parsear_notificacion_yape(html: str) -> Optional[PagoRecibidoData]:
    """
    Yape envía notificaciones con:
    - "Te yaparon S/ XX.XX"
    - "Recibiste un Yape de NOMBRE por S/ XX.XX"
    - Número de operación
    """
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    # Monto
    monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    # Origen (nombre del pagador)
    origen_match = re.search(r"(?:de|desde)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)", texto)
    if origen_match:
        data.origen = origen_match.group(1).strip()

    # Referencia
    ref_match = re.search(r"(?:operaci[oó]n|referencia|c[oó]digo)\s*:?\s*(\w+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    # Fecha
    data.fecha = _extraer_fecha(texto)

    return data if data.monto else None


def parsear_notificacion_plin(html: str) -> Optional[PagoRecibidoData]:
    """Parser de notificaciones Plin (BBVA)."""
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    origen_match = re.search(r"(?:de|desde)\s+([A-ZÁÉÍÓÚÑ][\w\s]+?)(?:\s+te|\s+ha|\s+por|$)", texto)
    if origen_match:
        data.origen = origen_match.group(1).strip()

    ref_match = re.search(r"(?:operaci[oó]n|referencia)\s*:?\s*(\w+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    data.fecha = _extraer_fecha(texto)
    return data if data.monto else None


def parsear_notificacion_bcp(html: str) -> Optional[PagoRecibidoData]:
    """
    BCP envía notificaciones de transferencias con:
    - Monto, cuenta destino, nombre del ordenante
    - Número de operación
    """
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    monto_match = re.search(r"(?:monto|importe)\s*:?\s*S/?\s*\.?\s*([\d,.]+)", texto, re.I)
    if not monto_match:
        monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    # Moneda USD
    if re.search(r"US\$|USD|D[OÓ]LARES", texto, re.I):
        data.moneda = "USD"

    origen_match = re.search(r"(?:ordenante|origen|de)\s*:?\s*(.+?)(?:\n|$)", texto, re.I)
    if origen_match:
        data.origen = origen_match.group(1).strip()[:100]

    ref_match = re.search(r"(?:operaci[oó]n|n[uú]mero|ref)\s*:?\s*(\d[\d-]+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    data.fecha = _extraer_fecha(texto)
    return data if data.monto else None


def parsear_notificacion_bbva(html: str) -> Optional[PagoRecibidoData]:
    """Parser de notificaciones BBVA Continental."""
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    monto_match = re.search(r"(?:monto|importe)\s*:?\s*S/?\s*\.?\s*([\d,.]+)", texto, re.I)
    if not monto_match:
        monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    if re.search(r"US\$|USD", texto, re.I):
        data.moneda = "USD"

    origen_match = re.search(r"(?:ordenante|origen)\s*:?\s*(.+?)(?:\n|$)", texto, re.I)
    if origen_match:
        data.origen = origen_match.group(1).strip()[:100]

    ref_match = re.search(r"(?:operaci[oó]n|referencia)\s*:?\s*(\d[\d-]+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    data.fecha = _extraer_fecha(texto)
    return data if data.monto else None


def parsear_notificacion_interbank(html: str) -> Optional[PagoRecibidoData]:
    """Parser de notificaciones Interbank."""
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    ref_match = re.search(r"(?:operaci[oó]n|CIP|referencia)\s*:?\s*(\d[\d-]+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    data.fecha = _extraer_fecha(texto)
    return data if data.monto else None


def parsear_notificacion_scotiabank(html: str) -> Optional[PagoRecibidoData]:
    """Parser de notificaciones Scotiabank Perú."""
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    ref_match = re.search(r"(?:operaci[oó]n|referencia)\s*:?\s*(\d[\d-]+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    data.fecha = _extraer_fecha(texto)
    return data if data.monto else None


def parsear_notificacion_bnacion(html: str) -> Optional[PagoRecibidoData]:
    """Parser de notificaciones Banco de la Nación."""
    texto = _limpiar_html(html)
    data = PagoRecibidoData()

    monto_match = re.search(r"S/?\s*\.?\s*([\d,.]+)", texto)
    if monto_match:
        data.monto = _parsear_monto(monto_match.group(1))

    ref_match = re.search(r"(?:operaci[oó]n|referencia)\s*:?\s*(\d[\d-]+)", texto, re.I)
    if ref_match:
        data.referencia = ref_match.group(1)

    data.fecha = _extraer_fecha(texto)
    return data if data.monto else None


# ── Utilidades ───────────────────────────────────────────────

def _limpiar_html(html: str) -> str:
    """Remueve tags HTML para obtener texto plano."""
    if not html:
        return ""
    # Remover tags
    texto = re.sub(r"<[^>]+>", " ", html)
    # Decodificar entidades comunes
    texto = texto.replace("&nbsp;", " ").replace("&amp;", "&")
    texto = texto.replace("&lt;", "<").replace("&gt;", ">")
    # Normalizar whitespace
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _parsear_monto(texto: str) -> Optional[Decimal]:
    """
    Parsea un monto de texto a Decimal.
    Maneja formatos peruanos: 1,234.56 y 1.234,56
    """
    if not texto:
        return None

    texto = texto.strip()

    # Si tiene coma como último separador decimal (formato europeo/peruano alternativo)
    # 1.234,56 → 1234.56
    if re.match(r"^\d{1,3}(\.\d{3})+,\d{2}$", texto):
        texto = texto.replace(".", "").replace(",", ".")
    else:
        # Formato estándar: 1,234.56 → 1234.56
        texto = texto.replace(",", "")

    try:
        monto = Decimal(texto)
        return monto if monto > 0 else None
    except InvalidOperation:
        return None


def _extraer_fecha(texto: str) -> Optional[datetime]:
    """Intenta extraer fecha/hora del texto de una notificación."""
    patterns = [
        # dd/mm/yyyy HH:MM
        (r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})\s+(\d{2}):(\d{2})",
         lambda m: datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            int(m.group(4)), int(m.group(5)))),
        # dd/mm/yyyy
        (r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})",
         lambda m: datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))),
    ]

    for pat, builder in patterns:
        m = re.search(pat, texto)
        if m:
            try:
                return builder(m)
            except (ValueError, IndexError):
                continue

    return None
