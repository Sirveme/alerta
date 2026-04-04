"""
parsers/pdf_parser.py — Extracción de datos de PDFs de comprobantes SUNAT.

Decisiones técnicas:
- Algunos PDFs de SUNAT embeben el XML UBL como adjunto (PDF/A-3).
  Se intenta extraer primero — si existe, es la fuente más confiable.
- Si no hay XML embebido, se extrae texto con pdfplumber (o PyPDF2 como fallback).
- El texto extraído se puede procesar con regex para obtener datos básicos.
- pdfplumber no está en requirements (sería dependencia adicional).
  Se usa PyPDF2 que viene con la stdlib de muchos entornos.
  Decisión: no instalar pdfplumber para mantener dependencias mínimas.
  Si se necesita extracción avanzada, se agrega después.
"""

import io
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def extraer_xml_de_pdf(pdf_bytes: bytes) -> Optional[bytes]:
    """
    Busca y extrae XML UBL embebido en un PDF (formato PDF/A-3 de SUNAT).
    Algunos emisores electrónicos embeben el XML como adjunto en el PDF.
    Retorna los bytes del XML o None si no se encuentra.
    """
    try:
        # PyPDF2 puede extraer archivos embebidos
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))

        # Buscar en archivos embebidos (EmbeddedFiles)
        if "/Names" in reader.trailer.get("/Root", {}):
            root = reader.trailer["/Root"]
            names = root.get("/Names", {})
            ef = names.get("/EmbeddedFiles", {})
            names_array = ef.get("/Names", [])

            for i in range(0, len(names_array), 2):
                nombre = str(names_array[i])
                if nombre.lower().endswith(".xml"):
                    file_spec = names_array[i + 1]
                    if hasattr(file_spec, "get_object"):
                        file_spec = file_spec.get_object()
                    ef_dict = file_spec.get("/EF", {})
                    stream = ef_dict.get("/F")
                    if stream:
                        if hasattr(stream, "get_object"):
                            stream = stream.get_object()
                        xml_data = stream.get_data()
                        if xml_data and b"<Invoice" in xml_data or b"<CreditNote" in xml_data:
                            logger.info(f"XML embebido extraído del PDF: {nombre}")
                            return xml_data
    except ImportError:
        logger.warning("PyPDF2 no disponible para extraer XML embebido")
    except Exception as e:
        logger.debug(f"No se pudo extraer XML embebido del PDF: {e}")

    return None


def extraer_texto_pdf(pdf_bytes: bytes) -> str:
    """
    Extrae texto de un PDF para procesamiento posterior.
    Usa PyPDF2 como fallback básico.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        texto = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                texto += page_text + "\n"
        return texto.strip()
    except ImportError:
        logger.warning("PyPDF2 no disponible para extraer texto")
        return ""
    except Exception as e:
        logger.error(f"Error extrayendo texto de PDF: {e}")
        return ""


def extraer_datos_basicos_pdf(texto: str) -> dict:
    """
    Extrae datos básicos de un comprobante a partir de texto extraído de PDF.
    Usa regex y heurísticas. Cada campo tiene nivel de confianza.
    """
    datos = {}

    # RUC emisor (11 dígitos precedidos de "RUC" o "R.U.C.")
    ruc_match = re.search(r"R\.?U\.?C\.?\s*:?\s*(\d{11})", texto, re.IGNORECASE)
    if ruc_match:
        datos["ruc_emisor"] = {"valor": ruc_match.group(1), "confianza": "alta"}

    # Serie y correlativo (ej: F001-12345, B001-00456)
    serie_match = re.search(r"([FBNE]\w{3})\s*[-–]\s*(\d+)", texto)
    if serie_match:
        datos["serie"] = {"valor": serie_match.group(1), "confianza": "alta"}
        datos["correlativo"] = {"valor": serie_match.group(2), "confianza": "alta"}

    # Fecha (formatos peruanos: dd/mm/yyyy, dd-mm-yyyy)
    fecha_match = re.search(r"(\d{2})[/\-](\d{2})[/\-](\d{4})", texto)
    if fecha_match:
        datos["fecha_emision"] = {
            "valor": f"{fecha_match.group(3)}-{fecha_match.group(2)}-{fecha_match.group(1)}",
            "confianza": "media",
        }

    # Monto total (buscar "TOTAL", "IMPORTE TOTAL", etc.)
    total_match = re.search(
        r"(?:TOTAL|IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR)\s*:?\s*S/?\.?\s*([\d,]+\.?\d*)",
        texto, re.IGNORECASE,
    )
    if total_match:
        monto_str = total_match.group(1).replace(",", "")
        datos["total"] = {"valor": monto_str, "confianza": "media"}

    # IGV
    igv_match = re.search(
        r"(?:I\.?G\.?V\.?|IGV)\s*(?:\(?\d+%?\)?)?\s*:?\s*S/?\.?\s*([\d,]+\.?\d*)",
        texto, re.IGNORECASE,
    )
    if igv_match:
        igv_str = igv_match.group(1).replace(",", "")
        datos["igv"] = {"valor": igv_str, "confianza": "media"}

    return datos
