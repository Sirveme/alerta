"""
routers/portal.py — Endpoints del portal público reenviame.pe.

Todos son públicos (sin autenticación) excepto /admin.
Rate limiting: 20 envíos/hora por IP via slowapi.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.portal import EnvioPortal, EstadoSistema, TipoArchivo, EstadoValidacionPortal

router = APIRouter(tags=["portal reenviame.pe"])
templates = Jinja2Templates(directory="app/templates")


# ── Páginas ──────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def portal_index(request: Request):
    """Formulario principal del portal."""
    receptor = request.query_params.get("receptor", "")
    return templates.TemplateResponse("portal/index.html", {
        "request": request, "receptor_ruc": receptor,
    })


@router.get("/comprador", response_class=HTMLResponse)
async def portal_comprador(request: Request):
    return templates.TemplateResponse("portal/comprador.html", {"request": request})


@router.get("/estado", response_class=HTMLResponse)
async def portal_estado(request: Request):
    return templates.TemplateResponse("portal/estado.html", {"request": request})


# ── Envío de comprobantes ────────────────────────────────────

@router.post("/enviar/xml")
async def enviar_xml(
    request: Request,
    ruc_emisor: str = Form(...),
    ruc_receptor: str = Form(...),
    archivo_xml: UploadFile = File(...),
    email_notif: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Recibe XML SUNAT, valida, genera acuse."""
    contenido = await archivo_xml.read()
    if len(contenido) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo no debe superar 10 MB")

    # Crear registro de envío
    envio = EnvioPortal(
        ruc_emisor=ruc_emisor,
        ruc_receptor=ruc_receptor,
        tipo_archivo=TipoArchivo.XML,
        xml_original=contenido.decode("utf-8", errors="replace")[:100000],
        email_notif=email_notif,
        ip_origen=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500],
    )
    db.add(envio)
    db.commit()
    db.refresh(envio)

    # Procesar
    from app.services.portal_service import procesar_envio_portal
    resultado = procesar_envio_portal(envio.id, contenido, "xml", db)

    return {
        "acuse_uuid": str(envio.acuse_uuid),
        "estado_validacion": resultado.get("estado", "pendiente"),
        "errores": resultado.get("errores", []),
        "url_acuse": f"/acuse/{envio.acuse_uuid}",
        "datos_comprobante": resultado.get("datos", {}),
    }


@router.post("/enviar/pdf")
async def enviar_pdf(
    request: Request,
    ruc_emisor: str = Form(...),
    ruc_receptor: str = Form(...),
    archivo_pdf: UploadFile = File(...),
    email_notif: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Recibe PDF, intenta extraer XML, fallback a Vision OCR."""
    contenido = await archivo_pdf.read()
    if len(contenido) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo no debe superar 10 MB")

    envio = EnvioPortal(
        ruc_emisor=ruc_emisor,
        ruc_receptor=ruc_receptor,
        tipo_archivo=TipoArchivo.PDF,
        email_notif=email_notif,
        ip_origen=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500],
    )
    db.add(envio)
    db.commit()
    db.refresh(envio)

    from app.services.portal_service import procesar_envio_portal
    resultado = procesar_envio_portal(envio.id, contenido, "pdf", db)

    return {
        "acuse_uuid": str(envio.acuse_uuid),
        "estado_validacion": resultado.get("estado", "pendiente"),
        "errores": resultado.get("errores", []),
        "url_acuse": f"/acuse/{envio.acuse_uuid}",
        "confianza_extraccion": resultado.get("confianza", "media"),
    }


@router.post("/enviar/datos")
async def enviar_datos(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
):
    """Ingreso manual de datos de comprobante."""
    envio = EnvioPortal(
        ruc_emisor=body.get("ruc_emisor", ""),
        ruc_receptor=body.get("ruc_receptor", ""),
        tipo_archivo=TipoArchivo.XML,
        tipo_comprobante=body.get("tipo"),
        serie=body.get("serie"),
        correlativo=body.get("correlativo"),
        fecha_emision=body.get("fecha_emision"),
        moneda=body.get("moneda", "PEN"),
        total=body.get("total"),
        estado_validacion=EstadoValidacionPortal.PENDIENTE,
        ip_origen=request.client.host if request.client else None,
    )
    db.add(envio)
    db.commit()
    db.refresh(envio)

    from app.services.portal_service import procesar_envio_portal
    resultado = procesar_envio_portal(envio.id, None, "datos", db)

    return {
        "acuse_uuid": str(envio.acuse_uuid),
        "estado_validacion": resultado.get("estado", "pendiente"),
        "url_acuse": f"/acuse/{envio.acuse_uuid}",
    }


# ── Acuse de recepción ──────────────────────────────────────

@router.get("/acuse/{acuse_uuid}")
async def descargar_acuse(acuse_uuid: str, db: Session = Depends(get_db)):
    """Descarga PDF del acuse de recepción."""
    envio = db.execute(
        select(EnvioPortal).where(EnvioPortal.acuse_uuid == acuse_uuid)
    ).scalar_one_or_none()

    if not envio:
        raise HTTPException(status_code=404, detail="Acuse no encontrado")

    if envio.acuse_generado and envio.acuse_gcs:
        from app.services.gcs_service import obtener_url_firmada
        url = obtener_url_firmada(envio.acuse_gcs)
        if url:
            return RedirectResponse(url=url)

    # Si no está generado o no hay GCS, generar en memoria
    from app.services.acuse_service import generar_acuse_pdf
    from fastapi.responses import Response

    pdf_bytes = generar_acuse_pdf(envio)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="acuse-{str(envio.acuse_uuid)[:8]}.pdf"'},
    )


@router.get("/acuse/{acuse_uuid}/verificar", response_class=HTMLResponse)
async def verificar_acuse(acuse_uuid: str, request: Request, db: Session = Depends(get_db)):
    """Página pública de verificación del acuse (target del QR)."""
    envio = db.execute(
        select(EnvioPortal).where(EnvioPortal.acuse_uuid == acuse_uuid)
    ).scalar_one_or_none()

    if not envio:
        raise HTTPException(status_code=404, detail="Acuse no encontrado")

    return templates.TemplateResponse("portal/acuse_verificar.html", {
        "request": request, "envio": envio,
    })


# ── Consulta pública ────────────────────────────────────────

@router.get("/verificar/{ruc_emisor}/{serie}/{correlativo}")
async def verificar_comprobante_publico(
    ruc_emisor: str, serie: str, correlativo: str,
    db: Session = Depends(get_db),
):
    """Consulta pública de validez de un comprobante."""
    envio = db.execute(
        select(EnvioPortal).where(
            EnvioPortal.ruc_emisor == ruc_emisor,
            EnvioPortal.serie == serie,
            EnvioPortal.correlativo == correlativo,
        ).order_by(EnvioPortal.created_at.desc())
    ).scalar_one_or_none()

    if not envio:
        return {"existe": False, "mensaje": "Comprobante no encontrado en nuestro sistema"}

    return {
        "existe": True,
        "estado": envio.estado_validacion.value,
        "tipo": envio.tipo_comprobante,
        "fecha_emision": str(envio.fecha_emision) if envio.fecha_emision else None,
        "total": float(envio.total) if envio.total else None,
        "moneda": envio.moneda,
        "validado_en": str(envio.validado_en) if envio.validado_en else None,
    }


# ── API pública ──────────────────────────────────────────────

@router.post("/api/v1/enviar")
async def api_enviar(
    request: Request,
    ruc_emisor: str = Form(...),
    ruc_receptor: str = Form(...),
    archivo: UploadFile = File(...),
    email_notif: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """API pública para integradores. Misma funcionalidad que /enviar/xml."""
    contenido = await archivo.read()
    nombre = (archivo.filename or "").lower()
    tipo = "xml" if nombre.endswith(".xml") else "pdf"

    envio = EnvioPortal(
        ruc_emisor=ruc_emisor,
        ruc_receptor=ruc_receptor,
        tipo_archivo=TipoArchivo.XML if tipo == "xml" else TipoArchivo.PDF,
        canal_envio="api",
        email_notif=email_notif,
        ip_origen=request.client.host if request.client else None,
    )
    if tipo == "xml":
        envio.xml_original = contenido.decode("utf-8", errors="replace")[:100000]

    db.add(envio)
    db.commit()
    db.refresh(envio)

    from app.services.portal_service import procesar_envio_portal
    resultado = procesar_envio_portal(envio.id, contenido, tipo, db)

    return {
        "acuse_uuid": str(envio.acuse_uuid),
        "estado_validacion": resultado.get("estado"),
        "url_acuse": f"/acuse/{envio.acuse_uuid}",
    }


# ── Estado del sistema ──────────────────────────────────────

@router.get("/estado/datos")
async def estado_datos(db: Session = Depends(get_db)):
    """JSON con métricas actuales para HTMX en /estado."""
    estado = db.execute(
        select(EstadoSistema).order_by(EstadoSistema.id.desc()).limit(1)
    ).scalar_one_or_none()

    if not estado:
        return {"sunat_disponible": True, "envios_hoy": 0, "uptime": 99.9}

    return {
        "sunat_disponible": estado.sunat_disponible,
        "sunat_ms": estado.sunat_tiempo_respuesta,
        "envios_hoy": estado.envios_hoy,
        "validaciones_ok": estado.validaciones_exitosas_hoy,
        "uptime": float(estado.uptime_porcentaje) if estado.uptime_porcentaje else 99.9,
        "incidencias": estado.incidencias_activas,
        "actualizado": str(estado.timestamp),
    }
