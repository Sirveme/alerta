"""
gcs.py — alerta.pe   (C:\\alertape\\gcs.py)
═══════════════════════════════════════════════════════════════════════
Google Cloud Storage para los PDFs de deuda (zAlerta-34).

Replica el patrón ya probado de CCPL (app/utils/gcs.py), parametrizado:
  - Credencial: env GCS_CREDENTIALS_JSON (JSON completo de la service account).
  - Bucket:     env GCS_BUCKET_NAME (default 'alertape-pdfs', southamerica-west1,
                privado). NO se hardcodea el bucket de CCPL.
  - PDFs de deuda = PRIVADOS → se guardan por blob_path y se sirven con
    signed URL temporal (nunca público).

Si GCS no está configurado (sin la env), las funciones devuelven None y NO
rompen el flujo — el worker registra el documento igual (con gcs_key NULL) y
se puede re-subir luego. La credencial NUNCA va al repo (vive en Railway).
"""

from __future__ import annotations

import datetime
import json
import os

_client = None
_credentials = None

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "alertape-pdfs")


def _get_credentials():
    """Parsea la credencial de la service account una sola vez (singleton)."""
    global _credentials
    if _credentials is not None:
        return _credentials
    creds_json = os.getenv("GCS_CREDENTIALS_JSON")
    if not creds_json:
        return None
    try:
        from google.oauth2 import service_account
        _credentials = service_account.Credentials.from_service_account_info(
            json.loads(creds_json))
        return _credentials
    except Exception as e:
        print(f"[GCS] error parseando credenciales: {e}", flush=True)
        return None


def _get_client():
    """Cliente GCS (lazy singleton)."""
    global _client
    if _client is not None:
        return _client
    creds = _get_credentials()
    if not creds:
        return None
    try:
        from google.cloud import storage
        _client = storage.Client(credentials=creds)
        return _client
    except Exception as e:
        print(f"[GCS] error inicializando cliente: {e}", flush=True)
        return None


def gcs_disponible() -> bool:
    """True si hay credencial+cliente (para que el worker decida si subir)."""
    return _get_client() is not None


def subir_pdf(file_bytes: bytes, blob_path: str,
              content_type: str = "application/pdf") -> str | None:
    """Sube un PDF privado a GCS. Devuelve el blob_path (la gcs_key) o None.

    blob_path (zAlerta-34): '{contribuyente_id}/valorados/{num_doc}_{cod_msg}.pdf'.
    No se hace público: se sirve con signed URL bajo demanda.
    """
    client = _get_client()
    if not client:
        print("[GCS] no configurado — PDF no subido (gcs_key=None)", flush=True)
        return None
    try:
        blob = client.bucket(BUCKET_NAME).blob(blob_path)
        blob.upload_from_string(file_bytes, content_type=content_type)
        return blob_path
    except Exception as e:
        print(f"[GCS] error subiendo {blob_path}: {e}", flush=True)
        return None


def signed_url(blob_path: str, minutos: int = 5) -> str | None:
    """URL temporal de descarga para un PDF privado (default 5 min)."""
    creds = _get_credentials()
    client = _get_client()
    if not client or not creds or not blob_path:
        return None
    try:
        blob = client.bucket(BUCKET_NAME).blob(blob_path)
        return blob.generate_signed_url(
            expiration=datetime.timedelta(minutes=minutos),
            method="GET", credentials=creds)
    except Exception as e:
        print(f"[GCS] error firmando URL {blob_path}: {e}", flush=True)
        return None
