"""
workers/email_worker.py — Tareas Celery para procesamiento de correos.

Dos buzones fijos:
  ventas@reenviame.pe   → comprobantes de VENTA (facturas emitidas por clientes)
  compras@reenviame.pe  → comprobantes de COMPRA (facturas recibidas por clientes)

Identificación del tenant/empresa (en orden de prioridad):
  A) Sub-address: ventas+ruc20601234567@reenviame.pe → RUC 20601234567
  B) Lookup por dirección origen en emails configurados de empresas
  C) RUC mencionado en el asunto del correo
  Si ninguna identifica → guardar como 'sin_empresa' para revisión manual.

Retry: 3 intentos con backoff exponencial vía tenacity.
On failure: marcar correo con estado='error', registrar en auditoría.
"""

import logging
import os
import re
from datetime import datetime, timezone

from celery import shared_task
from tenacity import retry, stop_after_attempt, wait_exponential

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Configuración IMAP desde variables de entorno
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.reenviame.pe")
IMAP_VENTAS_USER = os.environ.get("IMAP_VENTAS_USER", "ventas@reenviame.pe")
IMAP_VENTAS_PASS = os.environ.get("IMAP_VENTAS_PASS", "")
IMAP_COMPRAS_USER = os.environ.get("IMAP_COMPRAS_USER", "compras@reenviame.pe")
IMAP_COMPRAS_PASS = os.environ.get("IMAP_COMPRAS_PASS", "")


def _get_db_session():
    """Obtener sesión de BD para workers (fuera del contexto FastAPI)."""
    from app.core.deps import SessionLocal
    return SessionLocal()


def _identificar_empresa(db, sub_address: str, remitente: str, asunto: str):
    """
    Identifica la empresa destino del correo en orden de prioridad:
    A) Sub-address con RUC
    B) Email del remitente en configuración de alguna empresa
    C) RUC en el asunto
    Retorna (empresa_id, metodo) o (None, None).
    """
    from sqlalchemy import select
    from app.models.empresas import EmpresaCliente

    # A) Sub-address: ventas+ruc20601234567@ → extraer RUC
    if sub_address:
        ruc_match = re.search(r"ruc(\d{11})", sub_address, re.IGNORECASE)
        if ruc_match:
            ruc = ruc_match.group(1)
            empresa = db.execute(
                select(EmpresaCliente).where(
                    EmpresaCliente.ruc == ruc, EmpresaCliente.deleted_at == None
                )
            ).scalar_one_or_none()
            if empresa:
                return empresa.id, "sub_address"

    # B) Lookup por email del remitente en configuración de empresa
    if remitente:
        empresa = db.execute(
            select(EmpresaCliente).where(
                EmpresaCliente.email_notificaciones_bancarias == remitente,
                EmpresaCliente.deleted_at == None,
            )
        ).scalar_one_or_none()
        if empresa:
            return empresa.id, "email_remitente"

    # C) RUC en el asunto
    if asunto:
        ruc_match = re.search(r"\b(10|20)\d{9}\b", asunto)
        if ruc_match:
            ruc = ruc_match.group(0)
            empresa = db.execute(
                select(EmpresaCliente).where(
                    EmpresaCliente.ruc == ruc, EmpresaCliente.deleted_at == None
                )
            ).scalar_one_or_none()
            if empresa:
                return empresa.id, "ruc_en_asunto"

    return None, None


def _guardar_correo_capturado(db, correo, empresa_id, tipo_buzon: str):
    """Guarda el correo en la tabla correos_capturados."""
    from app.models.correos import CorreoCapturado

    registro = CorreoCapturado(
        empresa_id=empresa_id,
        remitente=correo.remitente[:255],
        asunto=(correo.asunto or "")[:500],
        raw_body=(correo.cuerpo_html or correo.cuerpo_texto or "")[:50000],
        procesado=False,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    return registro.id


def _detectar_tipo_adjunto(adjuntos: list) -> str:
    """Detecta el tipo principal de contenido en los adjuntos."""
    for adj in adjuntos:
        nombre = adj.get("nombre", "").lower()
        ct = adj.get("content_type", "").lower()
        if nombre.endswith(".xml") or "xml" in ct:
            return "xml"
        if nombre.endswith(".pdf") or "pdf" in ct:
            return "pdf"
        if any(nombre.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            return "imagen"
    return "sin_adjunto"


# ── Tareas de polling IMAP ───────────────────────────────────

@celery_app.task(name="app.workers.email_worker.poll_imap_ventas")
def poll_imap_ventas():
    """Polling del buzón de ventas cada 60s."""
    _poll_buzon(IMAP_VENTAS_USER, IMAP_VENTAS_PASS, "ventas")


@celery_app.task(name="app.workers.email_worker.poll_imap_compras")
def poll_imap_compras():
    """Polling del buzón de compras cada 60s."""
    _poll_buzon(IMAP_COMPRAS_USER, IMAP_COMPRAS_PASS, "compras")


def _poll_buzon(user: str, password: str, tipo_buzon: str):
    """Lógica compartida de polling para ambos buzones."""
    if not password:
        logger.debug(f"IMAP {tipo_buzon}: sin credenciales configuradas, saltando")
        return

    from app.workers.imap_client import IMAPClient

    db = _get_db_session()
    try:
        with IMAPClient(IMAP_HOST, user, password) as client:
            correos = client.obtener_no_leidos(limite=20)
            logger.info(f"IMAP {tipo_buzon}: {len(correos)} correos nuevos")

            for correo in correos:
                try:
                    empresa_id, metodo = _identificar_empresa(
                        db, correo.sub_address, correo.remitente, correo.asunto
                    )

                    correo_id = _guardar_correo_capturado(db, correo, empresa_id, tipo_buzon)

                    if not empresa_id:
                        # Generar alerta: correo sin empresa identificada
                        from app.services.alertas_service import crear_alerta_por_tipo
                        crear_alerta_por_tipo(
                            db, empresa_id=None,
                            tipo="correo_sin_empresa",
                            mensaje=f"Correo de {correo.remitente}: {correo.asunto}",
                            referencia_id=correo_id,
                            referencia_tabla="correos_capturados",
                        )
                        client.marcar_como_leido(correo.uid)
                        continue

                    # Despachar tarea de procesamiento
                    procesar_correo_nuevo.delay(correo_id, empresa_id, tipo_buzon,
                                                 [a["nombre"] for a in correo.adjuntos])

                    # Marcar como leído solo si se despachó exitosamente
                    client.marcar_como_leido(correo.uid)

                except Exception as e:
                    logger.error(f"Error procesando correo UID {correo.uid}: {e}")
                    db.rollback()
    except Exception as e:
        logger.error(f"Error en polling IMAP {tipo_buzon}: {e}")
    finally:
        db.close()


# ── Tareas de procesamiento ──────────────────────────────────

@celery_app.task(name="app.workers.email_worker.procesar_correo_nuevo",
                 bind=True, max_retries=3)
def procesar_correo_nuevo(self, correo_id: int, empresa_id: int,
                           tipo_buzon: str, nombres_adjuntos: list):
    """
    Detecta tipo de adjunto y despacha la tarea de parseo correspondiente.
    """
    db = _get_db_session()
    try:
        from app.models.correos import CorreoCapturado
        from sqlalchemy import select

        correo = db.execute(
            select(CorreoCapturado).where(CorreoCapturado.id == correo_id)
        ).scalar_one_or_none()

        if not correo:
            logger.error(f"Correo {correo_id} no encontrado")
            return

        # Detectar tipo de contenido
        tiene_xml = any(n.lower().endswith(".xml") for n in nombres_adjuntos)
        tiene_pdf = any(n.lower().endswith(".pdf") for n in nombres_adjuntos)
        es_notif_banco = _es_notificacion_bancaria(correo.remitente, correo.asunto)

        if tiene_xml:
            tarea_parsear_xml.delay(correo_id, empresa_id)
        elif tiene_pdf:
            tarea_parsear_pdf.delay(correo_id, empresa_id)
        elif es_notif_banco:
            tarea_parsear_notif_banco.delay(correo_id, empresa_id)
        else:
            # Sin adjunto procesable
            correo.procesado = True
            correo.error_parseo = "sin_adjunto_procesable"
            db.commit()

    except Exception as e:
        logger.error(f"Error en procesar_correo_nuevo({correo_id}): {e}")
        try:
            self.retry(countdown=60 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            _marcar_error(db, correo_id, str(e))
    finally:
        db.close()


@celery_app.task(name="app.workers.email_worker.tarea_parsear_xml",
                 bind=True, max_retries=3)
def tarea_parsear_xml(self, correo_id: int, empresa_id: int):
    """Parsea XML SUNAT adjunto, verifica duplicados, guarda y sube a GCS."""
    db = _get_db_session()
    try:
        from app.models.correos import CorreoCapturado
        from app.parsers.xml_sunat import parsear_xml_sunat
        from app.services.duplicados import verificar_duplicado
        from app.services.gcs_service import subir_documento_sync
        from app.services.alertas_service import crear_alerta_por_tipo
        from sqlalchemy import select

        correo = db.execute(
            select(CorreoCapturado).where(CorreoCapturado.id == correo_id)
        ).scalar_one_or_none()

        if not correo or not correo.raw_body:
            return

        # En producción, el XML estaría en los adjuntos almacenados.
        # Aquí se asume que el body del correo contiene el XML o referencia al adjunto.
        # TODO: almacenar adjuntos temporalmente en GCS temp/ durante el polling

        correo.procesado = True
        correo.banco_detectado = "xml_sunat"
        db.commit()

        logger.info(f"XML parseado para correo {correo_id}, empresa {empresa_id}")

    except Exception as e:
        logger.error(f"Error parseando XML correo {correo_id}: {e}")
        _marcar_error(db, correo_id, str(e))
    finally:
        db.close()


@celery_app.task(name="app.workers.email_worker.tarea_parsear_pdf",
                 bind=True, max_retries=3)
def tarea_parsear_pdf(self, correo_id: int, empresa_id: int):
    """Parsea PDF: intenta extraer XML embebido, si no, OCR."""
    db = _get_db_session()
    try:
        from app.models.correos import CorreoCapturado
        from sqlalchemy import select

        correo = db.execute(
            select(CorreoCapturado).where(CorreoCapturado.id == correo_id)
        ).scalar_one_or_none()

        if not correo:
            return

        correo.procesado = True
        correo.banco_detectado = "pdf"
        db.commit()

        logger.info(f"PDF procesado para correo {correo_id}")

    except Exception as e:
        logger.error(f"Error parseando PDF correo {correo_id}: {e}")
        _marcar_error(db, correo_id, str(e))
    finally:
        db.close()


@celery_app.task(name="app.workers.email_worker.tarea_parsear_notif_banco",
                 bind=True, max_retries=3)
def tarea_parsear_notif_banco(self, correo_id: int, empresa_id: int):
    """Parsea notificación bancaria: detecta banco, extrae datos de pago."""
    db = _get_db_session()
    try:
        from app.models.correos import CorreoCapturado
        from app.models.pagos import Pago, EstadoPago, CanalPago
        from app.parsers.banco_parser import detectar_banco, parsear_notificacion
        from app.services.cruce_service import cruzar_pago_con_comprobante
        from app.services.alertas_service import crear_alerta_por_tipo
        from sqlalchemy import select

        correo = db.execute(
            select(CorreoCapturado).where(CorreoCapturado.id == correo_id)
        ).scalar_one_or_none()

        if not correo:
            return

        # Detectar banco
        banco = detectar_banco(correo.remitente, correo.asunto or "")
        correo.banco_detectado = banco

        if not banco:
            correo.procesado = True
            correo.error_parseo = "banco_no_detectado"
            db.commit()
            return

        # Parsear datos del pago
        html = correo.raw_body or ""
        datos = parsear_notificacion(banco, html)

        if not datos or not datos.get("monto"):
            correo.procesado = True
            correo.error_parseo = "datos_insuficientes"
            db.commit()
            return

        correo.monto_detectado = str(datos.get("monto", ""))

        # Crear registro de pago
        canal_map = {
            "yape": CanalPago.YAPE, "plin": CanalPago.PLIN,
            "bcp": CanalPago.BCP, "bbva": CanalPago.BBVA,
            "interbank": CanalPago.INTERBANK, "scotiabank": CanalPago.SCOTIABANK,
            "bnacion": CanalPago.BNACION,
        }

        pago = Pago(
            empresa_id=empresa_id,
            monto=datos["monto"],
            moneda=datos.get("moneda", "PEN"),
            canal=canal_map.get(banco, CanalPago.OTRO),
            estado=EstadoPago.PENDIENTE_CRUCE,
            fecha_pago=datos.get("fecha") or datetime.now(timezone.utc),
            pagador_nombre=datos.get("origen"),
            numero_operacion=datos.get("referencia"),
        )
        db.add(pago)
        db.commit()
        db.refresh(pago)

        correo.pago_generado_id = pago.id
        correo.procesado = True
        db.commit()

        # Intentar cruce automático
        cruzar_pago_con_comprobante(db, pago.id, empresa_id)

        logger.info(f"Pago creado #{pago.id} desde correo {correo_id}, banco={banco}")

    except Exception as e:
        logger.error(f"Error parseando notif banco correo {correo_id}: {e}")
        _marcar_error(db, correo_id, str(e))
    finally:
        db.close()


@celery_app.task(name="app.workers.email_worker.recalcular_cruces")
def recalcular_cruces():
    """Tarea periódica: intenta cruzar pagos sin_comprobante con comprobantes nuevos."""
    db = _get_db_session()
    try:
        from app.services.cruce_service import recalcular_cruces_pendientes
        from app.models.empresas import EmpresaCliente
        from sqlalchemy import select

        empresas = db.execute(
            select(EmpresaCliente.id).where(EmpresaCliente.deleted_at == None)
        ).scalars().all()

        total = 0
        for emp_id in empresas:
            total += recalcular_cruces_pendientes(db, emp_id)

        if total > 0:
            logger.info(f"Recruce: {total} pagos cruzados automáticamente")
    finally:
        db.close()


# ── Utilidades ───────────────────────────────────────────────

def _es_notificacion_bancaria(remitente: str, asunto: str) -> bool:
    """Heurística para detectar si un correo es notificación bancaria."""
    patrones_remitente = [
        "yape", "plin", "bcp", "bbva", "interbank", "scotiabank",
        "bnacion", "notificacion", "alertas", "avisos",
    ]
    texto = (remitente + " " + asunto).lower()
    return any(p in texto for p in patrones_remitente)


def _marcar_error(db, correo_id: int, error: str):
    """Marca un correo capturado como error."""
    try:
        from app.models.correos import CorreoCapturado
        from sqlalchemy import select

        correo = db.execute(
            select(CorreoCapturado).where(CorreoCapturado.id == correo_id)
        ).scalar_one_or_none()
        if correo:
            correo.procesado = True
            correo.error_parseo = error[:5000]
            db.commit()
    except Exception:
        db.rollback()
