"""
routers/ingesta.py — Endpoints de ingesta manual: foto, XML, formulario.

Decisiones técnicas:
- Foto: se valida con content-type (no solo extensión). Max 10MB.
  Se sube a GCS temp/, se despacha tarea Celery para OCR.
  Respuesta inmediata con tarea_id para polling.
- XML: parseo sincrónico (es rápido, <100ms para XMLs típicos).
  Motor de duplicados, guardado y subida a GCS.
- Formulario: ingreso manual sin archivo. Motor de duplicados igual.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.comprobantes import Comprobante, EstadoComprobante, TipoComprobante, DetalleComprobante
from app.parsers.xml_sunat import parsear_xml_sunat, ParseError
from app.services.duplicados import verificar_duplicado
from app.services.alertas_service import crear_alerta_por_tipo
from app.schemas.comprobante import ComprobanteIn
from app.schemas.ingesta import IngestaFotoResponse, IngestaXMLResponse, TareaEstadoResponse

router = APIRouter(prefix="/ingesta", tags=["ingesta"])

MAX_FOTO_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.post("/foto", response_model=IngestaFotoResponse)
async def subir_foto(
    request: Request,
    foto: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Sube foto de comprobante. Valida formato y tamaño.
    Despacha tarea Celery para OCR. Retorna tarea_id para polling.
    """
    # Validar content type
    if foto.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usar JPG, PNG o WebP.")

    contenido = await foto.read()

    # Validar tamaño
    if len(contenido) > MAX_FOTO_SIZE:
        raise HTTPException(status_code=400, detail="La imagen no debe superar 10 MB")

    # Obtener empresa activa
    payload = request.state.token_payload
    empresa_id = payload.get("empresa_activa_id")
    if not empresa_id:
        raise HTTPException(status_code=400, detail="Selecciona una empresa antes de subir")

    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == empresa_id)
    ).scalar_one_or_none()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    # Subir a GCS temp/
    from app.services.gcs_service import subir_foto_sync
    ext = (foto.filename or "photo.jpg").rsplit(".", 1)[-1].lower()
    gcs_path = subir_foto_sync(empresa.ruc, contenido, ext)

    # OCR con OpenAI Vision (sincrónico — ~1-3s por imagen)
    # Decisión: no usar Celery para OCR Vision porque es suficientemente rápido
    # y el usuario necesita ver el resultado inmediatamente para editar.
    from app.parsers.ocr_parser import ocr_comprobante_vision

    mime_type = foto.content_type or "image/jpeg"
    try:
        datos_ocr = ocr_comprobante_vision(contenido, mime_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en OCR: {str(e)}")

    return {
        "tarea_id": "vision-sync",
        "mensaje": "OCR completado con OpenAI Vision",
        "datos": datos_ocr,
        "confianza": datos_ocr.get("confianza", {}),
        "gcs_path": gcs_path,
    }


@router.post("/xml", response_model=IngestaXMLResponse)
async def subir_xml(
    request: Request,
    archivo: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Sube XML SUNAT. Parseo sincrónico + duplicados + guardado.
    Retorna comprobante completo parseado.
    """
    if not archivo.filename or not archivo.filename.lower().endswith(".xml"):
        raise HTTPException(status_code=400, detail="El archivo debe ser XML")

    contenido = await archivo.read()
    if len(contenido) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="XML no debe superar 5 MB")

    payload = request.state.token_payload
    empresa_id = payload.get("empresa_activa_id")
    if not empresa_id:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    # Parsear XML
    try:
        datos = parsear_xml_sunat(contenido)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=f"Error de parseo: {e}")

    # Motor de duplicados
    dup = verificar_duplicado(
        db, empresa_id,
        datos.ruc_emisor, datos.serie, datos.correlativo, datos.ruc_receptor,
        datos.total_comprobante,
        datetime.strptime(datos.fecha_emision, "%Y-%m-%d").date() if datos.fecha_emision else None,
    )

    # Determinar estado
    estado = EstadoComprobante.PENDIENTE
    if dup.es_duplicado and dup.nivel in (1, 2):
        estado = EstadoComprobante.DUPLICADO

    # Mapear tipo
    tipo_map = {
        "factura": TipoComprobante.FACTURA,
        "boleta": TipoComprobante.BOLETA,
        "nota_credito": TipoComprobante.NOTA_CREDITO,
        "nota_debito": TipoComprobante.NOTA_DEBITO,
        "guia_remision": TipoComprobante.GUIA_REMISION,
        "liquidacion": TipoComprobante.LIQUIDACION,
    }

    # Guardar comprobante
    comprobante = Comprobante(
        empresa_id=empresa_id,
        tipo=tipo_map.get(datos.tipo_comprobante, TipoComprobante.FACTURA),
        serie=datos.serie,
        correlativo=datos.correlativo,
        ruc_emisor=datos.ruc_emisor,
        razon_social_emisor=datos.nombre_emisor,
        ruc_receptor=datos.ruc_receptor,
        razon_social_receptor=datos.nombre_receptor,
        moneda=datos.moneda,
        subtotal=datos.subtotal,
        igv=datos.total_igv,
        total=datos.total_comprobante,
        fecha_emision=datetime.strptime(datos.fecha_emision, "%Y-%m-%d").date() if datos.fecha_emision else date.today(),
        estado=estado,
        hash_cpe=datos.hash_cpe,
    )
    db.add(comprobante)
    db.flush()

    # Guardar líneas de detalle
    for linea in datos.lineas:
        detalle = DetalleComprobante(
            comprobante_id=comprobante.id,
            numero_linea=linea.numero_linea,
            codigo_producto=linea.codigo_producto,
            codigo_sunat=linea.codigo_sunat,
            descripcion=linea.descripcion,
            unidad_medida=linea.unidad_medida,
            cantidad=linea.cantidad,
            precio_unitario=linea.precio_unitario,
            precio_unitario_inc=linea.precio_unitario_inc,
            valor_venta=linea.valor_venta,
            igv_base=linea.igv_base,
            igv_monto=linea.igv_monto,
            igv_tipo=linea.igv_tipo,
            igv_afectacion=linea.igv_afectacion,
            isc_base=linea.isc_base,
            isc_monto=linea.isc_monto,
            isc_tipo=linea.isc_tipo,
            icbper_cantidad=linea.icbper_cantidad,
            icbper_monto=linea.icbper_monto,
            ivap_base=linea.ivap_base,
            ivap_monto=linea.ivap_monto,
            otros_tributos=linea.otros_tributos,
            total_linea=linea.total_linea,
        )
        db.add(detalle)

    db.commit()
    db.refresh(comprobante)

    # Subir a GCS
    from app.services.gcs_service import subir_documento_sync
    subir_documento_sync(
        ruc_empresa=datos.ruc_receptor or datos.ruc_emisor,
        tipo=datos.tipo_comprobante,
        serie=datos.serie,
        correlativo=datos.correlativo,
        contenido=contenido,
        extension="xml",
    )

    # Alerta si duplicado fuzzy
    if dup.es_duplicado and dup.nivel == 3:
        crear_alerta_por_tipo(
            db, empresa_id, "posible_duplicado",
            mensaje=f"Comprobante {datos.serie}-{datos.correlativo} similar a #{dup.original_id}",
            referencia_id=comprobante.id,
            referencia_tabla="comprobantes",
        )

    return IngestaXMLResponse(
        comprobante_id=comprobante.id,
        tipo=datos.tipo_comprobante,
        serie=datos.serie,
        correlativo=datos.correlativo,
        total=float(datos.total_comprobante),
        es_duplicado=dup.es_duplicado,
        duplicado_nivel=dup.nivel,
    )


@router.post("/formulario")
def ingesta_formulario(
    body: ComprobanteIn,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ingreso manual de comprobante por formulario. Origen = formulario_manual."""
    payload = request.state.token_payload
    empresa_id = payload.get("empresa_activa_id")
    if not empresa_id:
        raise HTTPException(status_code=400, detail="Selecciona una empresa")

    # Motor de duplicados
    dup = verificar_duplicado(
        db, empresa_id,
        body.ruc_emisor, body.serie, body.correlativo, body.ruc_receptor or "",
        body.total, body.fecha_emision,
    )

    tipo_map = {
        "factura": TipoComprobante.FACTURA,
        "boleta": TipoComprobante.BOLETA,
        "nota_credito": TipoComprobante.NOTA_CREDITO,
        "nota_debito": TipoComprobante.NOTA_DEBITO,
        "guia_remision": TipoComprobante.GUIA_REMISION,
        "liquidacion": TipoComprobante.LIQUIDACION,
    }

    estado = EstadoComprobante.DUPLICADO if dup.es_duplicado and dup.nivel in (1, 2) else EstadoComprobante.PENDIENTE

    comprobante = Comprobante(
        empresa_id=empresa_id,
        tipo=tipo_map.get(body.tipo, TipoComprobante.FACTURA),
        serie=body.serie,
        correlativo=body.correlativo,
        ruc_emisor=body.ruc_emisor,
        razon_social_emisor=body.razon_social_emisor,
        ruc_receptor=body.ruc_receptor,
        razon_social_receptor=body.razon_social_receptor,
        moneda=body.moneda,
        subtotal=body.subtotal,
        igv=body.igv,
        total=body.total,
        fecha_emision=body.fecha_emision,
        fecha_vencimiento=body.fecha_vencimiento,
        estado=estado,
    )
    db.add(comprobante)
    db.flush()

    # Líneas de detalle
    for linea in body.detalle_lineas:
        detalle = DetalleComprobante(
            comprobante_id=comprobante.id,
            numero_linea=linea.numero_linea,
            descripcion=linea.descripcion,
            cantidad=linea.cantidad,
            precio_unitario=linea.precio_unitario,
            igv_monto=linea.igv_monto,
            total_linea=linea.total_linea,
            codigo_producto=linea.codigo_producto,
            unidad_medida=linea.unidad_medida,
            categoria_ia=linea.categoria_ia,
            es_deducible=linea.es_deducible,
            clasificado_por="usuario" if linea.es_deducible is not None else None,
        )
        db.add(detalle)

    db.commit()
    db.refresh(comprobante)

    return {
        "comprobante_id": comprobante.id,
        "serie": comprobante.serie,
        "correlativo": comprobante.correlativo,
        "es_duplicado": dup.es_duplicado,
    }


@router.get("/tarea/{tarea_id}", response_model=TareaEstadoResponse)
def estado_tarea(tarea_id: str):
    """Estado de tarea Celery (polling desde frontend tras subir foto)."""
    if tarea_id.startswith("sync-"):
        return TareaEstadoResponse(estado="success", resultado={"modo": "sincrono"})

    try:
        from app.workers.celery_app import celery_app
        result = celery_app.AsyncResult(tarea_id)
        estado_map = {"PENDING": "pending", "STARTED": "started", "SUCCESS": "success", "FAILURE": "failure"}
        return TareaEstadoResponse(
            estado=estado_map.get(result.status, result.status),
            resultado=result.result if result.successful() else None,
            error=str(result.result) if result.failed() else None,
        )
    except Exception:
        return TareaEstadoResponse(estado="unknown", error="Celery no disponible")
