"""
services/acuse_service.py — Generates professional PDF receipt (acuse de recepcion).

White background (printable), professional layout.
Includes QR code pointing to reenviame.pe/acuse/{uuid}/verificar.

Decisiones tecnicas:
- reportlab para PDF: libreria madura, sin dependencias de sistema (no wkhtmltopdf).
- QR via qrcode: ligero, genera PNG en memoria que se incrusta en el PDF.
- Ambas librerias son opcionales: si no estan instaladas, se loguea warning
  y se retorna None en vez de crashear. Esto permite que el sistema funcione
  sin generacion de acuses en entornos de desarrollo.
- Timezone Lima (UTC-5) para timestamp en el acuse.
- Numero de acuse: ARE-{primeros 8 chars del UUID en mayusculas}.
  Formato corto, unico, legible por telefono.
"""

import io
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portal import EnvioPortal, EstadoValidacionPortal

logger = logging.getLogger(__name__)

# Timezone Lima (UTC-5)
TZ_LIMA = timezone(timedelta(hours=-5))

# Datos de la empresa emisora del acuse
EMPRESA_NOMBRE = "Peru Sistemas Pro EIRL"
EMPRESA_RUC = "20615446565"
ACUSE_BASE_URL = "https://reenviame.pe/acuse"


def generar_numero_acuse(acuse_uuid: uuid.UUID) -> str:
    """
    Genera numero legible para el acuse de recepcion.

    Formato: ARE-{primeros 8 caracteres del UUID en mayusculas}
    Ejemplo: ARE-3F7A1B2C

    Args:
        acuse_uuid: UUID del acuse.

    Returns:
        String con formato ARE-XXXXXXXX.
    """
    return f"ARE-{str(acuse_uuid)[:8].upper()}"


def generar_acuse_pdf(envio: EnvioPortal) -> bytes:
    """
    Genera PDF profesional del acuse de recepcion.

    Contenido:
    - Header: "Acuse de Recepcion Validada"
    - Numero de acuse: ARE-{8 chars}
    - Timestamp en zona horaria Lima
    - Datos del comprobante (emisor, receptor, serie, monto)
    - Resultado de validacion con icono visual
    - Codigo QR para verificacion
    - Footer con datos de Peru Sistemas Pro EIRL

    Args:
        envio: EnvioPortal con datos del envio procesado.

    Returns:
        bytes del PDF generado.

    Raises:
        ImportError: Si reportlab no esta instalado.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm, cm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Table, TableStyle,
            Spacer, Image,
        )
    except ImportError:
        logger.error(
            "reportlab no instalado. Ejecutar: pip install reportlab. "
            "El acuse no se puede generar sin esta dependencia."
        )
        raise

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    # Estilos
    styles = getSampleStyleSheet()

    style_titulo = ParagraphStyle(
        "AcuseTitulo",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=HexColor("#1a237e"),
        alignment=TA_CENTER,
        spaceAfter=6 * mm,
    )

    style_subtitulo = ParagraphStyle(
        "AcuseSubtitulo",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=HexColor("#424242"),
        alignment=TA_CENTER,
        spaceAfter=4 * mm,
    )

    style_normal = ParagraphStyle(
        "AcuseNormal",
        parent=styles["Normal"],
        fontSize=10,
        textColor=HexColor("#333333"),
        spaceAfter=2 * mm,
    )

    style_footer = ParagraphStyle(
        "AcuseFooter",
        parent=styles["Normal"],
        fontSize=8,
        textColor=HexColor("#757575"),
        alignment=TA_CENTER,
    )

    style_estado = ParagraphStyle(
        "AcuseEstado",
        parent=styles["Normal"],
        fontSize=14,
        alignment=TA_CENTER,
        spaceAfter=4 * mm,
    )

    # Construir contenido
    elements = []

    # Header
    elements.append(Paragraph("Acuse de Recepcion Validada", style_titulo))
    elements.append(Paragraph("Portal de Comprobantes Electronicos", style_subtitulo))
    elements.append(Spacer(1, 4 * mm))

    # Numero de acuse y timestamp
    numero_acuse = generar_numero_acuse(envio.acuse_uuid)
    ahora_lima = datetime.now(TZ_LIMA)
    timestamp_str = ahora_lima.strftime("%d/%m/%Y %H:%M:%S")

    info_data = [
        ["N. de Acuse:", numero_acuse],
        ["Fecha y Hora:", f"{timestamp_str} (Lima, Peru)"],
    ]
    info_table = Table(info_data, colWidths=[4 * cm, 12 * cm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), HexColor("#333333")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 6 * mm))

    # Linea separadora
    sep_data = [["" * 80]]
    sep_table = Table(sep_data, colWidths=[16 * cm])
    sep_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1, HexColor("#1a237e")),
    ]))
    elements.append(sep_table)
    elements.append(Spacer(1, 6 * mm))

    # Datos del comprobante
    elements.append(Paragraph("Datos del Comprobante", style_subtitulo))

    comprobante_data = [
        ["Tipo:", envio.tipo_comprobante or "No identificado"],
        ["Serie - Correlativo:", f"{envio.serie or '---'} - {envio.correlativo or '---'}"],
        ["RUC Emisor:", envio.ruc_emisor or "---"],
        ["Razon Social Emisor:", envio.nombre_emisor or "---"],
        ["RUC Receptor:", envio.ruc_receptor or "---"],
        ["Razon Social Receptor:", envio.nombre_receptor or "---"],
        ["Moneda:", envio.moneda or "PEN"],
        ["Monto Total:", f"S/ {envio.total:,.2f}" if envio.total else "---"],
        [
            "Fecha Emision:",
            envio.fecha_emision.strftime("%d/%m/%Y") if envio.fecha_emision else "---",
        ],
    ]

    comp_table = Table(comprobante_data, colWidths=[5 * cm, 11 * cm])
    comp_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), HexColor("#333333")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#fafafa")),
        ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#e0e0e0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, HexColor("#eeeeee")),
    ]))
    elements.append(comp_table)
    elements.append(Spacer(1, 8 * mm))

    # Resultado de validacion
    elements.append(Paragraph("Resultado de Validacion", style_subtitulo))

    estado = envio.estado_validacion
    if estado == EstadoValidacionPortal.VALIDO:
        estado_texto = "VALIDO - Comprobante verificado correctamente"
        estado_color = HexColor("#2e7d32")
        icono = "[OK]"
    elif estado == EstadoValidacionPortal.OBSERVADO:
        estado_texto = "OBSERVADO - Verificar campos marcados"
        estado_color = HexColor("#f57f17")
        icono = "[!]"
    elif estado == EstadoValidacionPortal.BLOQUEADO:
        estado_texto = "BLOQUEADO - Error grave detectado"
        estado_color = HexColor("#c62828")
        icono = "[X]"
    elif estado == EstadoValidacionPortal.ERROR_SUNAT:
        estado_texto = "ERROR SUNAT - No se pudo validar con SUNAT"
        estado_color = HexColor("#c62828")
        icono = "[X]"
    else:
        estado_texto = "PENDIENTE - En proceso de validacion"
        estado_color = HexColor("#757575")
        icono = "[?]"

    style_estado_colored = ParagraphStyle(
        "EstadoColored",
        parent=style_estado,
        textColor=estado_color,
        fontName="Helvetica-Bold",
    )
    elements.append(Paragraph(f"{icono} {estado_texto}", style_estado_colored))

    # Errores de validacion si los hay
    if envio.errores_validacion:
        errores = envio.errores_validacion
        if isinstance(errores, dict):
            errores_lista = errores.get("errores", [])
            if isinstance(errores_lista, list) and errores_lista:
                elements.append(Spacer(1, 2 * mm))
                elements.append(Paragraph("Observaciones:", style_normal))
                for error in errores_lista[:5]:  # Maximo 5 errores en el PDF
                    elements.append(
                        Paragraph(f"  - {error}", style_normal)
                    )

    elements.append(Spacer(1, 8 * mm))

    # QR Code
    qr_url = f"{ACUSE_BASE_URL}/{envio.acuse_uuid}/verificar"
    qr_image = _generar_qr_image(qr_url)
    if qr_image:
        elements.append(Paragraph("Verificacion Digital", style_subtitulo))
        elements.append(qr_image)
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(
            f"Escanee el codigo QR o visite:",
            style_footer,
        ))
        elements.append(Paragraph(qr_url, style_footer))

    elements.append(Spacer(1, 10 * mm))

    # Footer
    sep_table2 = Table([["" * 80]], colWidths=[16 * cm])
    sep_table2.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, HexColor("#bdbdbd")),
    ]))
    elements.append(sep_table2)
    elements.append(Spacer(1, 3 * mm))
    elements.append(Paragraph(
        f"{EMPRESA_NOMBRE} | RUC {EMPRESA_RUC}",
        style_footer,
    ))
    elements.append(Paragraph(
        "Este documento es un acuse de recepcion automatico. "
        "No constituye aceptacion ni conformidad del comprobante.",
        style_footer,
    ))
    elements.append(Paragraph(
        f"Generado el {ahora_lima.strftime('%d/%m/%Y a las %H:%M:%S')} (hora Lima)",
        style_footer,
    ))

    # Generar PDF
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info(f"Acuse PDF generado: {numero_acuse}, {len(pdf_bytes)} bytes")
    return pdf_bytes


def generar_y_subir_acuse(envio_id: uuid.UUID, db: Session) -> Optional[str]:
    """
    Genera el acuse PDF y lo sube a GCS. Retorna URL firmada.

    Actualiza el EnvioPortal con la ruta GCS y marca acuse_generado = True.

    Args:
        envio_id: UUID del EnvioPortal.
        db: Sesion de SQLAlchemy.

    Returns:
        URL firmada (signed URL) del acuse en GCS, o None si falla.
    """
    envio = db.execute(
        select(EnvioPortal).where(EnvioPortal.id == envio_id)
    ).scalar_one_or_none()

    if not envio:
        logger.error(f"EnvioPortal no encontrado para generar acuse: {envio_id}")
        return None

    try:
        # Generar PDF
        pdf_bytes = generar_acuse_pdf(envio)

        # Subir a GCS
        from app.services.gcs_service import subir_documento_sync, obtener_url_firmada

        numero_acuse = generar_numero_acuse(envio.acuse_uuid)
        ruc_ref = envio.ruc_receptor or envio.ruc_emisor or "portal"
        serie = envio.serie or "ACUSE"
        correlativo = numero_acuse

        gcs_path = subir_documento_sync(
            ruc_empresa=ruc_ref,
            tipo="acuses",
            serie=serie,
            correlativo=correlativo,
            contenido=pdf_bytes,
            extension="pdf",
        )

        if gcs_path:
            envio.acuse_gcs = gcs_path
            envio.acuse_generado = True
            db.commit()

            # Generar URL firmada (24 horas)
            url_firmada = obtener_url_firmada(gcs_path, expira_segundos=86400)
            logger.info(f"Acuse {numero_acuse} subido a GCS: {gcs_path}")
            return url_firmada
        else:
            # GCS no disponible — marcar acuse generado pero sin GCS
            envio.acuse_generado = True
            db.commit()
            logger.warning(f"Acuse {numero_acuse} generado pero GCS no disponible")
            return None

    except ImportError:
        logger.error("No se puede generar acuse: reportlab no instalado")
        return None
    except Exception as e:
        logger.exception(f"Error generando/subiendo acuse para envio {envio_id}: {e}")
        return None


def _generar_qr_image(url: str):
    """
    Genera un QR code como Image de reportlab.
    Retorna None si la libreria qrcode no esta disponible.
    """
    try:
        import qrcode
        from reportlab.lib.units import cm
        from reportlab.platypus import Image

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        # Crear Image de reportlab desde buffer
        qr_reportlab = Image(img_buffer, width=4 * cm, height=4 * cm)
        qr_reportlab.hAlign = "CENTER"
        return qr_reportlab

    except ImportError:
        logger.warning(
            "qrcode no instalado. Ejecutar: pip install qrcode[pil]. "
            "El acuse se generara sin codigo QR."
        )
        return None
    except Exception as e:
        logger.warning(f"Error generando QR code: {e}")
        return None
