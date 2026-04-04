"""
parsers/ocr_parser.py — OCR con OpenAI GPT-4o Vision.

Reemplaza Tesseract (eliminado en sesión 4).

Ventajas sobre Tesseract:
- Entiende comprobantes peruanos: sellos, firmas, fondos con textura
- Extrae datos ESTRUCTURADOS directamente, no texto crudo
- Maneja ángulos, distorsión, iluminación irregular
- Retorna JSON con nivel de confianza por campo
- Sin instalación de sistema operativo requerida
- Una sola llamada API vs. regex + heurísticas post-Tesseract

Estrategia de modelo (costo-eficiencia):
1. Intentar con gpt-4o-mini (~$0.003/imagen, ~1300 tokens input imagen low)
2. Si confianza global == 'baja' → reintentar con gpt-4o (~$0.01/imagen)
3. Retornar el mejor resultado

Costo estimado por imagen:
- gpt-4o-mini con detail=high (768x768 tiles): ~1100 tokens imagen + ~300 prompt
  = ~1400 input tokens × $0.15/1M = ~$0.0002 input + ~500 output × $0.60/1M = $0.0003
  Total mini: ~$0.0005/imagen
- gpt-4o con detail=high: ~$0.005/imagen (10x más)
- Caso promedio (90% mini + 10% escalado): ~$0.001/imagen
"""

import base64
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

PROMPT_OCR_COMPROBANTE = """
Eres un experto en comprobantes electrónicos peruanos (SUNAT).
Analiza esta imagen y extrae los datos del comprobante.
Responde ÚNICAMENTE con JSON válido, sin explicaciones, sin markdown.

Estructura requerida:
{
  "tipo_comprobante": "factura|boleta|nota_credito|nota_debito|guia_remision|otro",
  "serie": "string o null",
  "correlativo": "string o null",
  "fecha_emision": "YYYY-MM-DD o null",
  "ruc_emisor": "11 dígitos o null",
  "nombre_emisor": "string o null",
  "ruc_receptor": "11 dígitos o null",
  "nombre_receptor": "string o null",
  "moneda": "PEN|USD|null",
  "subtotal": número o null,
  "igv": número o null,
  "isc": número o null,
  "icbper": número o null,
  "total": número o null,
  "lineas": [
    {
      "descripcion": "string",
      "cantidad": número o null,
      "precio_unitario": número o null,
      "total_linea": número o null
    }
  ],
  "confianza": {
    "global": "alta|media|baja",
    "campos_dudosos": ["lista de campos con baja confianza"]
  },
  "notas": "observaciones del OCR si las hay"
}
"""


def _get_openai_client():
    """Obtiene cliente OpenAI. Lazy init para no fallar al importar sin API key."""
    from openai import OpenAI
    from app.core.config import settings
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _llamar_modelo_vision(client, modelo: str, imagen_b64: str, mime_type: str) -> dict:
    """Llama al modelo de visión y retorna el JSON parseado."""
    data_url = f"data:{mime_type};base64,{imagen_b64}"

    response = client.chat.completions.create(
        model=modelo,
        max_tokens=1500,
        temperature=0.1,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT_OCR_COMPROBANTE},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            }
        ],
    )
    texto = response.choices[0].message.content.strip()
    # Limpiar posibles markdown fences si el modelo los incluyó
    texto = texto.replace("```json", "").replace("```", "").strip()
    return json.loads(texto)


def ocr_comprobante_vision(imagen_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Extrae datos estructurados de un comprobante usando GPT-4o Vision.

    Estrategia de modelo:
    1. Intentar con gpt-4o-mini (más barato)
    2. Si confianza global == 'baja' → reintentar con gpt-4o completo
    3. Retornar el mejor resultado

    Returns: dict con datos del comprobante + metadatos de confianza
    """
    client = _get_openai_client()
    imagen_b64 = base64.b64encode(imagen_bytes).decode("utf-8")

    # Redimensionar si la imagen es muy grande (ahorrar tokens)
    # OpenAI cobra por tiles de 512x512 en modo high-detail
    imagen_b64_resized = _redimensionar_si_necesario(imagen_bytes, imagen_b64)

    modelo_usado = "gpt-4o-mini"
    try:
        resultado = _llamar_modelo_vision(client, "gpt-4o-mini", imagen_b64_resized, mime_type)
    except json.JSONDecodeError:
        logger.warning("gpt-4o-mini retornó JSON inválido, escalando a gpt-4o")
        resultado = _llamar_modelo_vision(client, "gpt-4o", imagen_b64_resized, mime_type)
        modelo_usado = "gpt-4o"
    except Exception as e:
        logger.error(f"Error en OCR Vision mini: {e}")
        raise

    # Si la confianza es baja, escalar a gpt-4o completo
    confianza = resultado.get("confianza", {}).get("global", "media")
    if confianza == "baja" and modelo_usado == "gpt-4o-mini":
        try:
            resultado_hd = _llamar_modelo_vision(client, "gpt-4o", imagen_b64_resized, mime_type)
            resultado = resultado_hd
            modelo_usado = "gpt-4o"
            logger.info("OCR escalado a gpt-4o por baja confianza de mini")
        except Exception:
            pass  # Mantener resultado de mini si falla gpt-4o

    resultado["_modelo_usado"] = modelo_usado
    resultado["_origen"] = "openai_vision"

    logger.info(
        f"OCR Vision completado: modelo={modelo_usado}, "
        f"confianza={resultado.get('confianza', {}).get('global', '?')}, "
        f"tipo={resultado.get('tipo_comprobante', '?')}"
    )

    return resultado


def _redimensionar_si_necesario(imagen_bytes: bytes, imagen_b64: str) -> str:
    """
    Redimensiona la imagen si excede 2048px en cualquier dimensión.
    OpenAI cobra por tiles de 512x512, así que imágenes enormes son costosas.
    Mantener Pillow para esta función (validación + resize, no OCR).
    """
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(imagen_bytes))
        max_dim = max(img.size)

        if max_dim > 2048:
            # Redimensionar manteniendo aspect ratio
            ratio = 2048 / max_dim
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)

            buffer = io.BytesIO()
            fmt = "JPEG" if img.mode == "RGB" else "PNG"
            if img.mode == "RGBA":
                img = img.convert("RGB")
                fmt = "JPEG"
            img.save(buffer, format=fmt, quality=85)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except ImportError:
        pass  # Sin Pillow, enviar imagen original
    except Exception as e:
        logger.debug(f"No se pudo redimensionar imagen: {e}")

    return imagen_b64


# --- Compatibilidad con código existente ---
# Funciones legacy que redirigen al nuevo OCR Vision

def ocr_imagen(imagen_bytes: bytes) -> str:
    """
    DEPRECATED: usar ocr_comprobante_vision() en su lugar.
    Mantiene compatibilidad con código de sesión 3.
    Retorna texto resumido para logging.
    """
    try:
        resultado = ocr_comprobante_vision(imagen_bytes)
        return json.dumps(resultado, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error en OCR: {e}")
        return ""


def extraer_datos_comprobante_ocr(texto_ocr: str) -> dict:
    """
    DEPRECATED: ocr_comprobante_vision() ya retorna datos estructurados.
    Mantiene compatibilidad. Si el texto es JSON de Vision, lo parsea directamente.
    """
    if not texto_ocr:
        return {}
    try:
        datos = json.loads(texto_ocr)
        # Convertir formato Vision a formato legacy con confianza
        result = {}
        for campo in ["ruc_emisor", "serie", "correlativo", "fecha_emision", "total", "igv", "tipo_comprobante"]:
            valor = datos.get(campo)
            if valor is not None:
                campos_dudosos = datos.get("confianza", {}).get("campos_dudosos", [])
                confianza = "baja" if campo in campos_dudosos else "alta"
                result[campo] = {"valor": str(valor), "confianza": confianza}
        return result
    except (json.JSONDecodeError, TypeError):
        return {}
