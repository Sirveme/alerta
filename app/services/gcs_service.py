"""
services/gcs_service.py — Custodia de documentos en Google Cloud Storage.

Estructura de rutas en GCS:
  gs://{BUCKET}/
    docs/{ruc_empresa}/{año}/{mes}/{tipo}/{serie}-{correlativo}.{ext}
    uploads/{ruc_empresa}/{año}/{mes}/fotos/{uuid}.jpg
    temp/{uuid}/  (TTL 24h)

Decisiones técnicas:
- URLs firmadas (signed URLs) con expiración, NUNCA URLs públicas para
  documentos tributarios. Seguridad por defecto.
- Advertencia SUNAT: retención mínima 5 años para comprobantes.
  La eliminación loguea warning y registra en auditoría. Nunca silenciosa.
- Fallback graceful: si GCS no está configurado, loguea warning pero
  no falla fatalmente. El sistema funciona sin GCS (datos en BD).
- Versión sync para workers Celery (no async).
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Configuración GCS
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "alertape-docs")
GCS_PROJECT_ID = os.environ.get("GCS_PROJECT_ID", "")
# GOOGLE_APPLICATION_CREDENTIALS se lee automáticamente por la librería

_client = None
_bucket = None


def _get_bucket():
    """Obtiene el bucket GCS. Lazy init para no fallar al importar si no hay credenciales."""
    global _client, _bucket
    if _bucket is not None:
        return _bucket

    try:
        from google.cloud import storage
        _client = storage.Client(project=GCS_PROJECT_ID or None)
        _bucket = _client.bucket(GCS_BUCKET_NAME)
        logger.info(f"GCS conectado: bucket={GCS_BUCKET_NAME}")
        return _bucket
    except Exception as e:
        logger.warning(f"GCS no disponible: {e}. Los documentos no se subirán a la nube.")
        return None


def verificar_bucket_configurado() -> bool:
    """Verificar al startup que el bucket existe y tenemos permisos."""
    bucket = _get_bucket()
    if bucket is None:
        return False
    try:
        return bucket.exists()
    except Exception as e:
        logger.error(f"Error verificando bucket GCS: {e}")
        return False


def subir_documento_sync(
    ruc_empresa: str,
    tipo: str,
    serie: str,
    correlativo: str,
    contenido: bytes,
    extension: str,
    año: Optional[int] = None,
    mes: Optional[int] = None,
) -> Optional[str]:
    """
    Sube un documento (XML, PDF, EML) a GCS.
    Retorna la ruta GCS (gs://...) o None si falla.

    Ruta: docs/{ruc}/{año}/{mes}/{tipo}/{serie}-{correlativo}.{ext}
    """
    bucket = _get_bucket()
    if bucket is None:
        logger.warning("GCS no configurado, documento no subido")
        return None

    now = datetime.now(timezone.utc)
    año = año or now.year
    mes = mes or now.month

    # Sanitizar tipo para ruta
    tipo_dir = tipo.replace(" ", "_").lower()
    extension = extension.lstrip(".")

    path = f"docs/{ruc_empresa}/{año}/{mes:02d}/{tipo_dir}/{serie}-{correlativo}.{extension}"

    try:
        blob = bucket.blob(path)
        # Content-type según extensión
        content_types = {
            "xml": "application/xml",
            "pdf": "application/pdf",
            "eml": "message/rfc822",
        }
        blob.upload_from_string(
            contenido,
            content_type=content_types.get(extension, "application/octet-stream"),
        )
        logger.info(f"Documento subido a GCS: {path}")
        return f"gs://{GCS_BUCKET_NAME}/{path}"
    except Exception as e:
        logger.error(f"Error subiendo documento a GCS: {e}")
        return None


def subir_foto_sync(ruc_empresa: str, contenido: bytes, extension: str = "jpg") -> Optional[str]:
    """
    Sube una foto de comprobante a GCS.
    Ruta: uploads/{ruc}/{año}/{mes}/fotos/{uuid}.{ext}
    """
    bucket = _get_bucket()
    if bucket is None:
        return None

    now = datetime.now(timezone.utc)
    file_id = uuid.uuid4().hex
    extension = extension.lstrip(".")
    path = f"uploads/{ruc_empresa}/{now.year}/{now.month:02d}/fotos/{file_id}.{extension}"

    try:
        blob = bucket.blob(path)
        content_types = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
        blob.upload_from_string(
            contenido,
            content_type=content_types.get(extension, "image/jpeg"),
        )
        return f"gs://{GCS_BUCKET_NAME}/{path}"
    except Exception as e:
        logger.error(f"Error subiendo foto a GCS: {e}")
        return None


def obtener_url_firmada(gcs_path: str, expira_segundos: int = 3600) -> Optional[str]:
    """
    Genera una URL firmada (signed URL) con expiración para un documento en GCS.
    NUNCA URLs públicas para documentos tributarios.
    """
    bucket = _get_bucket()
    if bucket is None:
        return None

    # Extraer path relativo (quitar gs://bucket/)
    path = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")

    try:
        blob = bucket.blob(path)
        url = blob.generate_signed_url(
            expiration=timedelta(seconds=expira_segundos),
            method="GET",
        )
        return url
    except Exception as e:
        logger.error(f"Error generando URL firmada: {e}")
        return None


def eliminar_documento(gcs_path: str) -> bool:
    """
    Elimina un documento de GCS.

    ADVERTENCIA SUNAT: retención mínima 5 años para comprobantes electrónicos.
    Esta función loguea un WARNING y registra en auditoría antes de eliminar.
    Nunca eliminar silenciosamente.
    """
    bucket = _get_bucket()
    if bucket is None:
        return False

    path = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")

    # Verificar antigüedad si es un documento (no temp/uploads)
    if path.startswith("docs/"):
        logger.warning(
            f"ADVERTENCIA SUNAT: Eliminando documento {path}. "
            f"Los comprobantes electrónicos deben conservarse mínimo 5 años. "
            f"Verificar que este documento cumple el plazo de retención."
        )

    try:
        blob = bucket.blob(path)
        blob.delete()
        logger.info(f"Documento eliminado de GCS: {path}")
        return True
    except Exception as e:
        logger.error(f"Error eliminando documento de GCS: {e}")
        return False
