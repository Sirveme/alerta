"""
services/portal_service.py — Core logic for processing reenviame.pe portal submissions.

Flow: extract data from XML/PDF/Vision -> validate with SUNAT rules ->
      check duplicates -> if receptor is empresa_cliente -> create Comprobante
      in main system -> generate acuse PDF -> notify receptor.

Decisiones tecnicas:
- Procesamiento sincrono: los envios del portal ya llegan via Celery task o
  endpoint async, asi que este servicio es sync puro para simplicidad.
- Fallback strategy: XML > PDF embedded XML > Vision OCR.
  Si XML disponible, se usa siempre. PDF intenta extraer XML embebido primero.
- El Comprobante solo se crea si el receptor es una empresa_cliente registrada.
  Si no lo es, el envio queda como registro del portal (acuse igual se genera).
- Errores no fatales: se registran en envio.errores_validacion (JSONB) y se
  continua el pipeline. Solo ParseError detiene el flujo.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portal import EnvioPortal, EstadoValidacionPortal
from app.models.empresas import EmpresaCliente
from app.models.comprobantes import (
    Comprobante,
    TipoComprobante,
    EstadoComprobante,
    DetalleComprobante,
)
from app.parsers.xml_sunat import parsear_xml_sunat, ComprobanteParseado, ParseError
from app.services.duplicados import verificar_duplicado
from app.services.validacion_comprobante import validar_comprobante, EstadoValidacion
from app.services.alertas_service import crear_alerta_por_tipo
from app.services.gcs_service import subir_documento_sync

logger = logging.getLogger(__name__)


def procesar_envio_portal(envio_id: uuid.UUID, db: Session) -> dict:
    """
    Pipeline completo para un envio del portal reenviame.pe.

    Pasos:
      1. Cargar envio de BD
      2. Extraer datos del comprobante (XML o PDF)
      3. Validar campos con reglas SUNAT
      4. Verificar duplicados
      5. Si receptor es empresa_cliente, crear Comprobante en sistema principal
      6. Generar acuse de recepcion
      7. Retornar resultado

    Args:
        envio_id: UUID del EnvioPortal a procesar.
        db: Sesion de SQLAlchemy.

    Returns:
        dict con claves: estado, envio_id, comprobante_id (si aplica),
        empresa_encontrada, errores, acuse_url.
    """
    resultado = {
        "envio_id": str(envio_id),
        "estado": "error",
        "comprobante_id": None,
        "empresa_encontrada": False,
        "errores": [],
        "acuse_url": None,
    }

    # 1. Cargar envio
    envio = db.execute(
        select(EnvioPortal).where(EnvioPortal.id == envio_id)
    ).scalar_one_or_none()

    if not envio:
        logger.error(f"EnvioPortal no encontrado: {envio_id}")
        resultado["errores"].append("Envio no encontrado")
        return resultado

    try:
        # 2. Extraer datos segun tipo de archivo
        datos_extraidos = _extraer_datos_comprobante(envio, db)

        if not datos_extraidos:
            envio.estado_validacion = EstadoValidacionPortal.ERROR_SUNAT
            envio.errores_validacion = {"error": "No se pudo extraer datos del archivo"}
            db.commit()
            resultado["errores"].append("No se pudo extraer datos del archivo")
            return resultado

        # Actualizar envio con datos extraidos
        _actualizar_envio_con_datos(envio, datos_extraidos)

        # 3. Validar campos con reglas SUNAT
        errores_validacion = _validar_datos_extraidos(datos_extraidos)

        if errores_validacion:
            envio.estado_validacion = EstadoValidacionPortal.OBSERVADO
            envio.errores_validacion = {"errores": errores_validacion}
            resultado["errores"] = errores_validacion
        else:
            envio.estado_validacion = EstadoValidacionPortal.VALIDO

        envio.validado_en = datetime.now(timezone.utc)

        # 4. Identificar empresa receptora
        empresa = identificar_empresa_receptora(envio.ruc_receptor, db)
        resultado["empresa_encontrada"] = empresa is not None

        if empresa:
            envio.empresa_cliente_id = empresa.id

            # 5. Verificar duplicados y crear Comprobante
            comprobante_id = _crear_comprobante_si_no_duplicado(
                envio, datos_extraidos, empresa, db
            )
            if comprobante_id:
                envio.comprobante_id = comprobante_id
                resultado["comprobante_id"] = comprobante_id

        # 6. Generar acuse de recepcion
        try:
            from app.services.acuse_service import generar_y_subir_acuse
            acuse_url = generar_y_subir_acuse(envio.id, db)
            resultado["acuse_url"] = acuse_url
        except Exception as e:
            logger.warning(f"Error generando acuse para envio {envio_id}: {e}")
            resultado["errores"].append(f"Error generando acuse: {str(e)}")

        # Determinar estado final
        if envio.estado_validacion == EstadoValidacionPortal.VALIDO:
            resultado["estado"] = "valido"
        elif envio.estado_validacion == EstadoValidacionPortal.OBSERVADO:
            resultado["estado"] = "observado"
        else:
            resultado["estado"] = "error"

        db.commit()
        logger.info(
            f"Envio portal {envio_id} procesado: estado={resultado['estado']}, "
            f"empresa={'si' if empresa else 'no'}, comprobante={resultado['comprobante_id']}"
        )
        return resultado

    except Exception as e:
        logger.exception(f"Error procesando envio portal {envio_id}: {e}")
        db.rollback()
        envio.estado_validacion = EstadoValidacionPortal.ERROR_SUNAT
        envio.errores_validacion = {"error_fatal": str(e)}
        db.commit()
        resultado["errores"].append(f"Error fatal: {str(e)}")
        return resultado


def identificar_empresa_receptora(
    ruc_receptor: str, db: Session
) -> Optional[EmpresaCliente]:
    """
    Busca si el RUC receptor corresponde a una empresa_cliente registrada.

    Solo busca empresas activas (sin soft delete). Si un RUC aparece en
    multiples tenants, retorna la primera encontrada (el envio se asocia
    luego al tenant correcto via empresa_cliente_id).

    Args:
        ruc_receptor: RUC de 11 digitos del receptor.
        db: Sesion de SQLAlchemy.

    Returns:
        EmpresaCliente si existe, None si el RUC no esta registrado.
    """
    if not ruc_receptor or len(ruc_receptor) != 11:
        logger.warning(f"RUC receptor invalido para busqueda: {ruc_receptor}")
        return None

    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.ruc == ruc_receptor,
            EmpresaCliente.deleted_at == None,  # noqa: E711 — SQLAlchemy requiere == None
        )
    ).scalar_one_or_none()

    if empresa:
        logger.info(f"Empresa receptora encontrada: {empresa.razon_social} (RUC {ruc_receptor})")
    else:
        logger.debug(f"RUC receptor {ruc_receptor} no es empresa_cliente registrada")

    return empresa


def procesar_xml_portal(
    xml_bytes: bytes, envio: EnvioPortal, db: Session
) -> dict:
    """
    Parsea XML SUNAT y ejecuta validacion.

    Usa el parser xml_sunat para extraer todos los campos del comprobante.
    Si el parseo falla fatalmente (ParseError), marca el envio como error.

    Args:
        xml_bytes: Contenido XML en bytes.
        envio: EnvioPortal asociado.
        db: Sesion de SQLAlchemy.

    Returns:
        dict con datos extraidos del comprobante, o dict vacio si falla.
    """
    try:
        parseado: ComprobanteParseado = parsear_xml_sunat(xml_bytes)

        datos = {
            "tipo_comprobante": parseado.tipo_comprobante,
            "serie": parseado.serie,
            "correlativo": parseado.correlativo,
            "fecha_emision": parseado.fecha_emision,
            "moneda": parseado.moneda,
            "ruc_emisor": parseado.ruc_emisor,
            "nombre_emisor": parseado.nombre_emisor,
            "ruc_receptor": parseado.ruc_receptor,
            "nombre_receptor": parseado.nombre_receptor,
            "subtotal": parseado.subtotal,
            "igv": parseado.total_igv,
            "total": parseado.total_comprobante,
            "hash_cpe": parseado.hash_cpe,
            "lineas": parseado.lineas,
            "warnings": parseado.warnings,
            "origen": "xml",
        }

        # Guardar XML original en el envio
        try:
            envio.xml_original = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            envio.xml_original = xml_bytes.decode("latin-1")

        # Subir XML a GCS
        if parseado.ruc_emisor and parseado.serie and parseado.correlativo:
            gcs_path = subir_documento_sync(
                ruc_empresa=parseado.ruc_receptor or parseado.ruc_emisor,
                tipo="portal",
                serie=parseado.serie,
                correlativo=parseado.correlativo,
                contenido=xml_bytes,
                extension="xml",
            )
            if gcs_path:
                envio.archivo_gcs = gcs_path

        logger.info(
            f"XML parseado OK: {parseado.tipo_comprobante} "
            f"{parseado.serie}-{parseado.correlativo} "
            f"de {parseado.ruc_emisor}, warnings={len(parseado.warnings)}"
        )
        return datos

    except ParseError as e:
        logger.error(f"Error fatal parseando XML del envio {envio.id}: {e}")
        envio.estado_validacion = EstadoValidacionPortal.ERROR_SUNAT
        envio.errores_validacion = {"parse_error": str(e)}
        return {}

    except Exception as e:
        logger.exception(f"Error inesperado parseando XML del envio {envio.id}: {e}")
        return {}


def procesar_pdf_portal(
    pdf_bytes: bytes, envio: EnvioPortal, db: Session
) -> dict:
    """
    Procesa un PDF de comprobante.

    Estrategia de fallback:
      1. Intentar extraer XML embebido en el PDF (muchos emisores lo incluyen).
      2. Si no hay XML embebido, usar Vision OCR para extraer datos.

    Args:
        pdf_bytes: Contenido del PDF en bytes.
        envio: EnvioPortal asociado.
        db: Sesion de SQLAlchemy.

    Returns:
        dict con datos extraidos, o dict vacio si falla.
    """
    # Paso 1: Intentar extraer XML embebido del PDF
    xml_embebido = _extraer_xml_de_pdf(pdf_bytes)
    if xml_embebido:
        logger.info(f"XML embebido encontrado en PDF del envio {envio.id}")
        datos = procesar_xml_portal(xml_embebido, envio, db)
        if datos:
            datos["origen"] = "pdf_xml_embebido"
            return datos

    # Paso 2: Fallback a Vision OCR
    logger.info(f"Sin XML embebido en PDF, usando OCR para envio {envio.id}")
    try:
        from app.parsers.ocr_parser import extraer_datos_ocr

        datos_ocr = extraer_datos_ocr(pdf_bytes, tipo="pdf")
        if datos_ocr:
            datos = {
                "tipo_comprobante": datos_ocr.get("tipo_comprobante", ""),
                "serie": datos_ocr.get("serie", ""),
                "correlativo": datos_ocr.get("correlativo", ""),
                "fecha_emision": datos_ocr.get("fecha_emision"),
                "moneda": datos_ocr.get("moneda", "PEN"),
                "ruc_emisor": datos_ocr.get("ruc_emisor", ""),
                "nombre_emisor": datos_ocr.get("nombre_emisor", ""),
                "ruc_receptor": datos_ocr.get("ruc_receptor", ""),
                "nombre_receptor": datos_ocr.get("nombre_receptor", ""),
                "subtotal": datos_ocr.get("subtotal"),
                "igv": datos_ocr.get("igv"),
                "total": datos_ocr.get("total"),
                "hash_cpe": None,
                "lineas": [],
                "warnings": ["Datos extraidos via OCR, verificar manualmente"],
                "origen": "pdf_ocr",
            }

            # Subir PDF a GCS
            ruc_ref = datos.get("ruc_receptor") or datos.get("ruc_emisor") or "sin_ruc"
            serie = datos.get("serie") or "XXXX"
            correlativo = datos.get("correlativo") or str(envio.id)[:8]
            gcs_path = subir_documento_sync(
                ruc_empresa=ruc_ref,
                tipo="portal",
                serie=serie,
                correlativo=correlativo,
                contenido=pdf_bytes,
                extension="pdf",
            )
            if gcs_path:
                envio.archivo_gcs = gcs_path

            return datos

    except ImportError:
        logger.warning("OCR parser no disponible, no se puede procesar PDF sin XML embebido")
    except Exception as e:
        logger.exception(f"Error en OCR para envio {envio.id}: {e}")

    return {}


# -- Funciones internas -------------------------------------------------------


def _extraer_datos_comprobante(envio: EnvioPortal, db: Session) -> dict:
    """
    Extrae datos del comprobante segun el tipo de archivo del envio.
    Delega a procesar_xml_portal o procesar_pdf_portal.
    """
    from app.models.portal import TipoArchivo

    # Obtener bytes del archivo (de xml_original o de GCS)
    archivo_bytes = _obtener_archivo_bytes(envio)
    if not archivo_bytes:
        logger.error(f"No se pudo obtener archivo para envio {envio.id}")
        return {}

    if envio.tipo_archivo == TipoArchivo.XML:
        return procesar_xml_portal(archivo_bytes, envio, db)
    elif envio.tipo_archivo == TipoArchivo.PDF:
        return procesar_pdf_portal(archivo_bytes, envio, db)
    elif envio.tipo_archivo == TipoArchivo.IMAGEN:
        # Imagen: solo OCR
        try:
            from app.parsers.ocr_parser import extraer_datos_ocr
            datos_ocr = extraer_datos_ocr(archivo_bytes, tipo="imagen")
            if datos_ocr:
                return {
                    "tipo_comprobante": datos_ocr.get("tipo_comprobante", ""),
                    "serie": datos_ocr.get("serie", ""),
                    "correlativo": datos_ocr.get("correlativo", ""),
                    "fecha_emision": datos_ocr.get("fecha_emision"),
                    "moneda": datos_ocr.get("moneda", "PEN"),
                    "ruc_emisor": datos_ocr.get("ruc_emisor", ""),
                    "nombre_emisor": datos_ocr.get("nombre_emisor", ""),
                    "ruc_receptor": datos_ocr.get("ruc_receptor", ""),
                    "nombre_receptor": datos_ocr.get("nombre_receptor", ""),
                    "subtotal": datos_ocr.get("subtotal"),
                    "igv": datos_ocr.get("igv"),
                    "total": datos_ocr.get("total"),
                    "hash_cpe": None,
                    "lineas": [],
                    "warnings": ["Datos extraidos via OCR de imagen, verificar manualmente"],
                    "origen": "imagen_ocr",
                }
        except Exception as e:
            logger.exception(f"Error en OCR de imagen para envio {envio.id}: {e}")
        return {}

    logger.error(f"Tipo de archivo no soportado: {envio.tipo_archivo}")
    return {}


def _obtener_archivo_bytes(envio: EnvioPortal) -> Optional[bytes]:
    """
    Obtiene los bytes del archivo del envio.
    Prioridad: xml_original (ya almacenado) > descarga de GCS.
    """
    # Si hay XML original almacenado, usarlo
    if envio.xml_original:
        try:
            return envio.xml_original.encode("utf-8")
        except Exception:
            return envio.xml_original.encode("latin-1")

    # Descargar de GCS si hay ruta
    if envio.archivo_gcs:
        try:
            from app.services.gcs_service import _get_bucket, GCS_BUCKET_NAME
            bucket = _get_bucket()
            if bucket:
                path = envio.archivo_gcs.replace(f"gs://{GCS_BUCKET_NAME}/", "")
                blob = bucket.blob(path)
                return blob.download_as_bytes()
        except Exception as e:
            logger.error(f"Error descargando archivo de GCS para envio {envio.id}: {e}")

    return None


def _actualizar_envio_con_datos(envio: EnvioPortal, datos: dict) -> None:
    """Actualiza los campos del EnvioPortal con los datos extraidos."""
    envio.tipo_comprobante = datos.get("tipo_comprobante") or envio.tipo_comprobante
    envio.serie = datos.get("serie") or envio.serie
    envio.correlativo = datos.get("correlativo") or envio.correlativo
    envio.moneda = datos.get("moneda") or envio.moneda
    envio.ruc_emisor = datos.get("ruc_emisor") or envio.ruc_emisor
    envio.nombre_emisor = datos.get("nombre_emisor") or envio.nombre_emisor
    envio.ruc_receptor = datos.get("ruc_receptor") or envio.ruc_receptor
    envio.nombre_receptor = datos.get("nombre_receptor") or envio.nombre_receptor

    if datos.get("total") is not None:
        envio.total = datos["total"]

    if datos.get("fecha_emision"):
        fecha_str = datos["fecha_emision"]
        if isinstance(fecha_str, str):
            try:
                from datetime import date
                envio.fecha_emision = date.fromisoformat(fecha_str)
            except (ValueError, TypeError):
                logger.warning(f"Fecha emision no parseable: {fecha_str}")
        else:
            envio.fecha_emision = fecha_str


def _validar_datos_extraidos(datos: dict) -> list[str]:
    """
    Validacion basica de los datos extraidos antes de crear Comprobante.
    Retorna lista de errores encontrados (vacia si todo OK).
    """
    errores = []

    if not datos.get("ruc_emisor"):
        errores.append("RUC emisor no encontrado")
    elif len(datos["ruc_emisor"]) != 11:
        errores.append(f"RUC emisor invalido: {datos['ruc_emisor']}")

    if not datos.get("serie"):
        errores.append("Serie no encontrada")

    if not datos.get("correlativo"):
        errores.append("Correlativo no encontrado")

    if not datos.get("tipo_comprobante"):
        errores.append("Tipo de comprobante no identificado")

    if datos.get("total") is None:
        errores.append("Monto total no encontrado")

    # Agregar warnings del parseo
    warnings = datos.get("warnings", [])
    if warnings:
        for w in warnings:
            errores.append(f"Advertencia: {w}")

    return errores


def _crear_comprobante_si_no_duplicado(
    envio: EnvioPortal,
    datos: dict,
    empresa: EmpresaCliente,
    db: Session,
) -> Optional[int]:
    """
    Verifica duplicados y crea un Comprobante en el sistema principal.

    Si es duplicado exacto (Nivel 1), NO crea nuevo comprobante.
    Si es fuzzy (Nivel 3), crea pero genera alerta.

    Returns:
        ID del comprobante creado, o None si no se creo.
    """
    from decimal import Decimal

    ruc_emisor = datos.get("ruc_emisor", "")
    serie = datos.get("serie", "")
    correlativo = datos.get("correlativo", "")
    total = datos.get("total") or Decimal("0")

    # Verificar duplicados
    resultado_dup = verificar_duplicado(
        db=db,
        empresa_id=empresa.id,
        ruc_emisor=ruc_emisor,
        serie=serie,
        correlativo=correlativo,
        ruc_receptor=empresa.ruc,
        monto_total=total,
        fecha_emision=envio.fecha_emision,
    )

    if resultado_dup.es_duplicado and resultado_dup.nivel <= 2:
        # Duplicado exacto o multi-origen: no crear nuevo comprobante
        logger.info(
            f"Comprobante duplicado (nivel {resultado_dup.nivel}): "
            f"{serie}-{correlativo}, original #{resultado_dup.original_id}"
        )
        crear_alerta_por_tipo(
            db, empresa.id, "duplicado_exacto",
            mensaje=(
                f"Comprobante {serie}-{correlativo} del portal ya existe "
                f"como #{resultado_dup.original_id}"
            ),
            referencia_id=resultado_dup.original_id,
            referencia_tabla="comprobantes",
        )
        return resultado_dup.original_id

    # Mapear tipo_comprobante string a enum
    tipo_map = {
        "factura": TipoComprobante.FACTURA,
        "boleta": TipoComprobante.BOLETA,
        "nota_credito": TipoComprobante.NOTA_CREDITO,
        "nota_debito": TipoComprobante.NOTA_DEBITO,
        "guia_remision": TipoComprobante.GUIA_REMISION,
        "liquidacion": TipoComprobante.LIQUIDACION,
    }
    tipo = tipo_map.get(datos.get("tipo_comprobante", ""), TipoComprobante.FACTURA)

    # Determinar estado inicial
    if resultado_dup.es_duplicado and resultado_dup.nivel == 3:
        estado = EstadoComprobante.PENDIENTE  # Fuzzy: guardar pero revisar
    else:
        estado = EstadoComprobante.PENDIENTE

    # Crear comprobante
    fecha_emision = envio.fecha_emision
    if not fecha_emision:
        from datetime import date
        fecha_emision = date.today()

    comprobante = Comprobante(
        empresa_id=empresa.id,
        tipo=tipo,
        serie=serie,
        correlativo=correlativo,
        ruc_emisor=ruc_emisor,
        razon_social_emisor=datos.get("nombre_emisor"),
        ruc_receptor=empresa.ruc,
        razon_social_receptor=empresa.razon_social,
        moneda=datos.get("moneda", "PEN"),
        subtotal=datos.get("subtotal") or Decimal("0"),
        igv=datos.get("igv") or Decimal("0"),
        total=total,
        fecha_emision=fecha_emision,
        estado=estado,
        hash_cpe=datos.get("hash_cpe"),
        notas=f"Ingresado via portal reenviame.pe (envio {envio.id})",
    )

    try:
        db.add(comprobante)
        db.flush()  # Obtener ID sin commit final

        # Crear lineas de detalle si hay
        lineas = datos.get("lineas", [])
        for linea in lineas:
            detalle = DetalleComprobante(
                comprobante_id=comprobante.id,
                numero_linea=getattr(linea, "numero_linea", 0),
                codigo_producto=getattr(linea, "codigo_producto", None),
                codigo_sunat=getattr(linea, "codigo_sunat", None),
                descripcion=getattr(linea, "descripcion", ""),
                unidad_medida=getattr(linea, "unidad_medida", None),
                cantidad=getattr(linea, "cantidad", Decimal("1")),
                precio_unitario=getattr(linea, "precio_unitario", Decimal("0")),
                precio_unitario_inc=getattr(linea, "precio_unitario_inc", None),
                valor_venta=getattr(linea, "valor_venta", Decimal("0")),
                igv_base=getattr(linea, "igv_base", Decimal("0")),
                igv_monto=getattr(linea, "igv_monto", Decimal("0")),
                igv_tipo=getattr(linea, "igv_tipo", None),
                igv_afectacion=getattr(linea, "igv_afectacion", None),
                isc_base=getattr(linea, "isc_base", Decimal("0")),
                isc_monto=getattr(linea, "isc_monto", Decimal("0")),
                isc_tipo=getattr(linea, "isc_tipo", None),
                icbper_cantidad=getattr(linea, "icbper_cantidad", 0),
                icbper_monto=getattr(linea, "icbper_monto", Decimal("0")),
                ivap_base=getattr(linea, "ivap_base", Decimal("0")),
                ivap_monto=getattr(linea, "ivap_monto", Decimal("0")),
                total_linea=getattr(linea, "total_linea", Decimal("0")),
            )
            db.add(detalle)

        # Alerta de nuevo comprobante
        crear_alerta_por_tipo(
            db, empresa.id, "comprobante_nuevo",
            mensaje=(
                f"Comprobante {serie}-{correlativo} de {ruc_emisor} "
                f"recibido via portal reenviame.pe"
            ),
            referencia_id=comprobante.id,
            referencia_tabla="comprobantes",
        )

        # Alerta si es fuzzy duplicate
        if resultado_dup.es_duplicado and resultado_dup.nivel == 3:
            crear_alerta_por_tipo(
                db, empresa.id, "posible_duplicado",
                mensaje=(
                    f"Comprobante {serie}-{correlativo} similar a "
                    f"#{resultado_dup.original_id}. Revisar manualmente."
                ),
                referencia_id=comprobante.id,
                referencia_tabla="comprobantes",
            )

        logger.info(f"Comprobante #{comprobante.id} creado desde portal para empresa {empresa.id}")
        return comprobante.id

    except Exception as e:
        logger.exception(f"Error creando comprobante desde portal: {e}")
        db.rollback()
        return None


def _extraer_xml_de_pdf(pdf_bytes: bytes) -> Optional[bytes]:
    """
    Intenta extraer un XML embebido dentro de un PDF.
    Muchos emisores peruanos incluyen el XML UBL dentro del PDF como adjunto.
    """
    try:
        from app.parsers.pdf_parser import extraer_xml_embebido
        return extraer_xml_embebido(pdf_bytes)
    except ImportError:
        logger.debug("pdf_parser.extraer_xml_embebido no disponible")
    except Exception as e:
        logger.debug(f"No se encontro XML embebido en PDF: {e}")
    return None
