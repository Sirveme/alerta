"""
Lógica de negocio del módulo RendiPe — Rendición de Gastos y Viáticos.

Funciones principales:
- Cálculo de días de comisión y fechas límite de rendición (días hábiles).
- Cálculo de saldo de comisión (asignado vs gastado vs observado).
- Generación de informe IA con OpenAI gpt-4o-mini.
- Generación de PDF de rendición y de informe de resultados con reportlab.
- Pipeline de procesamiento de foto de gasto (OCR + validación + GCS + registro).
- Verificación de vencimientos de rendiciones pendientes.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.rendipe import (
    Comision,
    GastoComision,
    InformeComision,
    Servidor,
    InstitucionConfig,
    SaldoComision,
    EstadoComision,
    EstadoValidacionGasto,
    OrigenGasto,
    TipoSaldo,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cálculo de días y fechas
# ---------------------------------------------------------------------------

def calcular_dias_comision(fecha_inicio: date, fecha_fin: date) -> int:
    """
    Calcula el total de días de una comisión de servicio.
    Incluye tanto el día de inicio como el de fin.
    """
    return (fecha_fin - fecha_inicio).days + 1


def calcular_fecha_limite_rendicion(fecha_fin: date, plazo_dias_habiles: int) -> date:
    """
    Calcula la fecha límite de rendición sumando días hábiles a la fecha fin.
    Implementación simple: salta sábados y domingos.
    No considera feriados peruanos — para eso se necesitaría una tabla de feriados.
    """
    dias_agregados = 0
    fecha_actual = fecha_fin
    while dias_agregados < plazo_dias_habiles:
        fecha_actual += timedelta(days=1)
        # weekday(): 0=lunes ... 4=viernes, 5=sábado, 6=domingo
        if fecha_actual.weekday() < 5:
            dias_agregados += 1
    return fecha_actual


# ---------------------------------------------------------------------------
# Saldo de comisión
# ---------------------------------------------------------------------------

def calcular_saldo_comision(comision_id: int, db: Session) -> dict:
    """
    Calcula el saldo financiero de una comisión.

    Retorna:
        {
            total_asignado:  Decimal  – monto total asignado a la comisión,
            total_gastado:   Decimal  – suma de gastos válidos y aprobados,
            total_observado: Decimal  – suma de gastos con estado 'observado',
            saldo:           Decimal  – total_asignado - total_gastado,
            tipo_saldo:      str      – 'a_favor' | 'por_devolver' | 'equilibrado',
        }
    """
    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    total_asignado = comision.monto_asignado or Decimal("0")

    # Gastos válidos (aprobados)
    total_gastado = db.execute(
        select(func.coalesce(func.sum(GastoComision.monto), Decimal("0"))).where(
            GastoComision.comision_id == comision_id,
            GastoComision.aprobado_contador == True,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar() or Decimal("0")

    # Gastos observados
    total_observado = db.execute(
        select(func.coalesce(func.sum(GastoComision.monto), Decimal("0"))).where(
            GastoComision.comision_id == comision_id,
            GastoComision.estado_validacion == EstadoValidacionGasto.OBSERVADO,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar() or Decimal("0")

    saldo = total_asignado - total_gastado

    if saldo > 0:
        tipo_saldo = "por_devolver"
    elif saldo < 0:
        tipo_saldo = "a_favor"
    else:
        tipo_saldo = "equilibrado"

    return {
        "total_asignado": total_asignado,
        "total_gastado": total_gastado,
        "total_observado": total_observado,
        "saldo": saldo,
        "tipo_saldo": tipo_saldo,
    }


# ---------------------------------------------------------------------------
# Generación de informe con IA
# ---------------------------------------------------------------------------

def generar_informe_ia(comision_id: int, db: Session) -> str:
    """
    Genera un borrador de informe de resultados usando OpenAI gpt-4o-mini.
    Lee los datos de la comisión y construye un prompt solicitando las secciones
    estándar de un informe de comisión de servicio peruano.
    """
    from app.core.config import settings
    import openai

    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    # Cargar servidor comisionado
    servidor = db.execute(
        select(Servidor).where(Servidor.id == comision.servidor_id)
    ).scalar_one_or_none()

    # Cargar gastos asociados
    gastos = db.execute(
        select(GastoComision).where(
            GastoComision.comision_id == comision_id,
            GastoComision.deleted_at.is_(None),
        )
    ).scalars().all()

    gastos_texto = "\n".join(
        f"- {g.fecha}: {g.rubro} - S/ {g.monto} ({g.descripcion or 'sin detalle'})"
        for g in gastos
    )

    prompt = f"""Eres un asistente administrativo del sector público peruano.
Genera un borrador de informe de resultados de comisión de servicio con las
siguientes secciones:

1. ANTECEDENTES
2. OBJETIVOS DE LA COMISIÓN
3. ACTIVIDADES REALIZADAS
4. RESULTADOS OBTENIDOS
5. CONCLUSIONES
6. RECOMENDACIONES

Datos de la comisión:
- Servidor: {servidor.nombres} {servidor.apellidos} (DNI: {servidor.dni})
- Cargo: {servidor.cargo or 'No especificado'}
- Destino: {comision.destino}
- Fecha inicio: {comision.fecha_inicio}
- Fecha fin: {comision.fecha_fin}
- Motivo: {comision.motivo or 'No especificado'}
- Objetivo: {comision.objetivo or 'No especificado'}
- Monto asignado: S/ {comision.monto_asignado}

Gastos registrados:
{gastos_texto or 'Sin gastos registrados aún.'}

Genera un informe formal, en español, con lenguaje institucional apropiado
para una entidad pública peruana. Incluye espacios para firma al final.
"""

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=2000,
    )

    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Generación de PDF de rendición
# ---------------------------------------------------------------------------

def generar_pdf_rendicion(comision_id: int, db: Session) -> bytes:
    """
    Genera el PDF de la planilla de rendición de gastos.
    Incluye: encabezado institucional, datos de la comisión, tabla de gastos
    (fecha / RUC / tipo / serie-correlativo / rubro / monto / estado),
    totales y espacios para firmas.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    servidor = db.execute(
        select(Servidor).where(Servidor.id == comision.servidor_id)
    ).scalar_one_or_none()

    gastos = db.execute(
        select(GastoComision).where(
            GastoComision.comision_id == comision_id,
            GastoComision.deleted_at.is_(None),
        ).order_by(GastoComision.fecha)
    ).scalars().all()

    saldo_info = calcular_saldo_comision(comision_id, db)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    elements = []

    # Encabezado
    elements.append(Paragraph("PLANILLA DE RENDICIÓN DE GASTOS", styles["Title"]))
    elements.append(Spacer(1, 0.5 * cm))

    # Datos de la comisión
    info_data = [
        ["Servidor:", f"{servidor.nombres} {servidor.apellidos}" if servidor else "—"],
        ["DNI:", servidor.dni if servidor else "—"],
        ["Cargo:", servidor.cargo or "—" if servidor else "—"],
        ["Destino:", comision.destino or "—"],
        ["Fecha inicio:", str(comision.fecha_inicio)],
        ["Fecha fin:", str(comision.fecha_fin)],
        ["Monto asignado:", f"S/ {comision.monto_asignado}"],
    ]
    info_table = Table(info_data, colWidths=[4 * cm, 12 * cm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.8 * cm))

    # Tabla de gastos
    header = ["N°", "Fecha", "RUC Emisor", "Tipo", "Serie-Corr.", "Rubro", "Monto", "Estado"]
    rows = [header]
    for i, g in enumerate(gastos, 1):
        rows.append([
            str(i),
            str(g.fecha),
            g.ruc_emisor or "—",
            g.tipo_comprobante or "—",
            f"{g.serie or ''}-{g.correlativo or ''}",
            g.rubro or "—",
            f"S/ {g.monto}",
            g.estado or "—",
        ])

    col_widths = [1 * cm, 2 * cm, 2.5 * cm, 1.8 * cm, 2.5 * cm, 2.5 * cm, 2 * cm, 2 * cm]
    gastos_table = Table(rows, colWidths=col_widths)
    gastos_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (6, 0), (6, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    elements.append(gastos_table)
    elements.append(Spacer(1, 0.5 * cm))

    # Totales
    totales_data = [
        ["Total asignado:", f"S/ {saldo_info['total_asignado']}"],
        ["Total gastado:", f"S/ {saldo_info['total_gastado']}"],
        ["Total observado:", f"S/ {saldo_info['total_observado']}"],
        ["Saldo:", f"S/ {saldo_info['saldo']} ({saldo_info['tipo_saldo']})"],
    ]
    totales_table = Table(totales_data, colWidths=[4 * cm, 4 * cm])
    totales_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    elements.append(totales_table)
    elements.append(Spacer(1, 2 * cm))

    # Firmas
    firma_data = [
        ["________________________", "", "________________________"],
        ["Comisionado", "", "Jefe de Área"],
        ["", "", ""],
        ["________________________", "", "________________________"],
        ["Tesorería", "", "Administración"],
    ]
    firma_table = Table(firma_data, colWidths=[5 * cm, 3 * cm, 5 * cm])
    firma_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements.append(firma_table)

    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Generación de PDF de informe de resultados
# ---------------------------------------------------------------------------

def generar_pdf_informe(informe_id: int, db: Session) -> bytes:
    """
    Genera el PDF del informe de resultados de la comisión de servicio.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    informe = db.execute(
        select(InformeComision).where(InformeComision.id == informe_id)
    ).scalar_one_or_none()
    if not informe:
        raise ValueError(f"Informe {informe_id} no encontrado")

    comision = db.execute(
        select(Comision).where(Comision.id == informe.comision_id)
    ).scalar_one_or_none()

    servidor = None
    if comision:
        servidor = db.execute(
            select(Servidor).where(Servidor.id == comision.servidor_id)
        ).scalar_one_or_none()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "BodyJustified",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        alignment=4,  # TA_JUSTIFY
    )

    elements = []

    elements.append(Paragraph("INFORME DE RESULTADOS DE COMISIÓN DE SERVICIO", styles["Title"]))
    elements.append(Spacer(1, 0.5 * cm))

    if comision and servidor:
        elements.append(Paragraph(
            f"<b>Servidor:</b> {servidor.nombres} {servidor.apellidos} | "
            f"<b>DNI:</b> {servidor.dni}",
            styles["Normal"],
        ))
        elements.append(Paragraph(
            f"<b>Destino:</b> {comision.destino} | "
            f"<b>Periodo:</b> {comision.fecha_inicio} al {comision.fecha_fin}",
            styles["Normal"],
        ))
        elements.append(Spacer(1, 0.5 * cm))

    # Contenido del informe (texto generado por IA o editado manualmente)
    contenido = informe.contenido or ""
    for parrafo in contenido.split("\n"):
        parrafo = parrafo.strip()
        if not parrafo:
            elements.append(Spacer(1, 0.3 * cm))
            continue
        # Secciones en mayúsculas como subtítulos
        if parrafo.isupper() or parrafo.startswith("#"):
            elements.append(Paragraph(parrafo.replace("#", "").strip(), styles["Heading2"]))
        else:
            elements.append(Paragraph(parrafo, body_style))

    elements.append(Spacer(1, 2 * cm))

    # Firmas
    elements.append(Paragraph("________________________", styles["Normal"]))
    if servidor:
        elements.append(Paragraph(
            f"{servidor.nombres} {servidor.apellidos}",
            styles["Normal"],
        ))
    elements.append(Paragraph("Comisionado", styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Pipeline de procesamiento de foto de gasto
# ---------------------------------------------------------------------------

def procesar_foto_gasto(
    imagen_bytes: bytes,
    comision_id: int,
    rubro: str,
    db: Session,
) -> GastoComision:
    """
    Pipeline completo para procesar una foto de comprobante de gasto:
    1. OCR con OpenAI Vision para extraer datos del comprobante.
    2. Validación de RUC emisor (formato básico).
    3. Subida de imagen a Google Cloud Storage.
    4. Creación del registro GastoComision en BD.
    """
    from app.parsers.ocr_parser import ocr_comprobante_vision
    from app.services.gcs_service import subir_archivo

    import uuid as uuid_mod

    # 1. OCR del comprobante
    datos_ocr = ocr_comprobante_vision(imagen_bytes)
    logger.info(f"OCR completado para comisión {comision_id}: {datos_ocr}")

    # 2. Validar RUC emisor (formato básico: 11 dígitos empezando con 10 o 20)
    ruc_emisor = datos_ocr.get("ruc_emisor", "")
    ruc_valido = bool(ruc_emisor and len(ruc_emisor) == 11 and ruc_emisor[:2] in ("10", "20"))
    if not ruc_valido:
        logger.warning(f"RUC emisor no válido: {ruc_emisor}")

    # 3. Subir imagen a GCS
    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    archivo_id = str(uuid_mod.uuid4())
    gcs_path = f"rendipe/{comision.tenant_id}/{comision_id}/gastos/{archivo_id}.jpg"
    try:
        url_archivo = subir_archivo(imagen_bytes, gcs_path, content_type="image/jpeg")
    except Exception as e:
        logger.warning(f"Error subiendo a GCS: {e}. Se guardará sin URL de imagen.")
        url_archivo = None

    # 4. Crear registro de gasto
    gasto = GastoComision(
        comision_id=comision_id,
        tenant_id=comision.tenant_id,
        fecha=datos_ocr.get("fecha"),
        ruc_emisor=ruc_emisor if ruc_valido else None,
        razon_social_emisor=datos_ocr.get("razon_social"),
        tipo_comprobante=datos_ocr.get("tipo"),
        serie=datos_ocr.get("serie"),
        correlativo=datos_ocr.get("correlativo"),
        monto=datos_ocr.get("monto", 0),
        rubro=rubro,
        descripcion=datos_ocr.get("descripcion"),
        imagen_url=url_archivo,
        imagen_gcs_path=gcs_path,
        estado_validacion=EstadoValidacionGasto.PENDIENTE,
        datos_ocr_raw=datos_ocr,
        ruc_valido=ruc_valido,
    )
    db.add(gasto)
    db.commit()
    db.refresh(gasto)

    logger.info(f"Gasto #{gasto.id} creado para comisión {comision_id} (rubro: {rubro})")
    return gasto


# ---------------------------------------------------------------------------
# Verificación de vencimientos
# ---------------------------------------------------------------------------

def verificar_vencimiento_rendiciones(db: Session) -> None:
    """
    Revisa comisiones con rendición vencida y genera alertas.
    Una rendición está vencida cuando:
    - El estado es 'en_rendicion' o 'pendiente_rendicion'
    - La fecha límite de rendición ya pasó
    """
    from app.services.rendipe_alertas import crear_alerta_rendipe

    hoy = date.today()

    comisiones_vencidas = db.execute(
        select(Comision).where(
            Comision.estado.in_([
                EstadoComision.EN_RENDICION,
                EstadoComision.PENDIENTE_RENDICION,
            ]),
            Comision.fecha_limite_rendicion < hoy,
            Comision.deleted_at.is_(None),
        )
    ).scalars().all()

    for comision in comisiones_vencidas:
        dias_vencida = (hoy - comision.fecha_limite_rendicion).days

        # Solo alertar si no se ha alertado recientemente (evitar spam)
        if dias_vencida in (1, 3, 7, 15, 30) or dias_vencida % 30 == 0:
            servidor = db.execute(
                select(Servidor).where(Servidor.id == comision.servidor_id)
            ).scalar_one_or_none()

            nombre_servidor = (
                f"{servidor.nombres} {servidor.apellidos}" if servidor else "Servidor desconocido"
            )

            crear_alerta_rendipe(
                db=db,
                tenant_id=comision.tenant_id,
                tipo="rendicion_vencida",
                comision_id=comision.id,
                mensaje=(
                    f"La rendición de {nombre_servidor} para la comisión a "
                    f"{comision.destino} está vencida por {dias_vencida} día(s). "
                    f"Fecha límite: {comision.fecha_limite_rendicion}."
                ),
            )

    logger.info(
        f"Verificación de vencimientos completada: {len(comisiones_vencidas)} comisión(es) vencida(s)."
    )


# ---------------------------------------------------------------------------
# Sesión 8: Geolocalización, DJ, Exterior, Selfie
# ---------------------------------------------------------------------------

from math import radians, sin, cos, sqrt, atan2


def calcular_distancia_metros(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Fórmula Haversine para distancia entre dos coordenadas GPS. Retorna metros."""
    R = 6371000  # Radio de la Tierra en metros
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return int(R * 2 * atan2(sqrt(a), sqrt(1 - a)))


def calcular_rubros_propios(comision: Comision) -> dict:
    """
    Determina qué rubros/días paga la institución (no cubiertos por invitante).

    Casos:
    A) No hay invitación → todos los rubros, todos los días, todos los viáticos.
    B) Invitación cubre rubros específicos → los demás son propios.
    C) Invitación cubre días específicos → los demás son propios.
    D) Mixto → combina B y C.
    """
    todos_los_rubros = [
        "pasajes_aereos", "pasajes_terrestres", "alojamiento",
        "alimentacion", "movilidad_local", "otros",
    ]
    dias_comision = (comision.fecha_fin - comision.fecha_inicio).days + 1
    todos_los_dias = list(range(1, dias_comision + 1))

    # Caso A: sin invitación o sin cobertura definida
    if not comision.por_invitacion or not comision.cobertura_invitacion:
        return {
            "rubros_propios": todos_los_rubros,
            "dias_propios": todos_los_dias,
            "viaticos_propios": float(comision.total_viaticos),
            "caso": "A",
            "descripcion": "Sin invitación — la institución cubre todo.",
        }

    cobertura = comision.cobertura_invitacion
    rubros_cubiertos = cobertura.get("cubre", [])
    dias_cubiertos = cobertura.get("dias_cubiertos", None)

    # Rubros propios = todos menos los cubiertos
    rubros_propios = [r for r in todos_los_rubros if r not in rubros_cubiertos]

    # Días propios = todos menos los cubiertos
    if dias_cubiertos and isinstance(dias_cubiertos, list):
        dias_propios = [d for d in todos_los_dias if d not in dias_cubiertos]
    else:
        dias_propios = todos_los_dias

    # Estimar viáticos propios proporcionalmente
    total = float(comision.total_viaticos)
    if dias_comision > 0 and len(dias_propios) < dias_comision:
        # Proporción por días propios
        viaticos_propios = round(total * len(dias_propios) / dias_comision, 2)
    else:
        viaticos_propios = total

    if rubros_cubiertos and not dias_cubiertos:
        caso = "B"
        descripcion = f"Invitante cubre: {', '.join(rubros_cubiertos)}."
    elif dias_cubiertos and not rubros_cubiertos:
        caso = "C"
        descripcion = f"Invitante cubre días: {dias_cubiertos}."
    else:
        caso = "D"
        descripcion = f"Invitante cubre rubros {rubros_cubiertos} en días {dias_cubiertos}."

    return {
        "rubros_propios": rubros_propios,
        "dias_propios": dias_propios,
        "viaticos_propios": viaticos_propios,
        "caso": caso,
        "descripcion": descripcion,
    }


def validar_gasto_vs_cobertura(gasto: GastoComision, comision: Comision) -> dict:
    """
    Verifica si un gasto cae en un rubro/día cubierto por el invitante.
    Retorna advertencia si es así (el gasto no se bloquea, solo se advierte).
    """
    if not comision.por_invitacion or not comision.cobertura_invitacion:
        return {"cubierto_por_invitante": False, "advertencia": None}

    cobertura = comision.cobertura_invitacion
    rubros_cubiertos = cobertura.get("cubre", [])
    dias_cubiertos = cobertura.get("dias_cubiertos", None)

    rubro_cubierto = gasto.rubro in rubros_cubiertos if rubros_cubiertos else False

    dia_cubierto = False
    if dias_cubiertos and gasto.fecha_emision and comision.fecha_inicio:
        dia_num = (gasto.fecha_emision - comision.fecha_inicio).days + 1
        dia_cubierto = dia_num in dias_cubiertos

    if rubro_cubierto or dia_cubierto:
        partes = []
        if rubro_cubierto:
            partes.append(f"rubro '{gasto.rubro}'")
        if dia_cubierto:
            partes.append(f"día de gasto")
        return {
            "cubierto_por_invitante": True,
            "advertencia": (
                f"Atención: {' y '.join(partes)} estaría cubierto por "
                f"{comision.institucion_invitante or 'el invitante'}. "
                "Verifique que no se esté duplicando el reembolso."
            ),
        }

    return {"cubierto_por_invitante": False, "advertencia": None}


def validar_asistencia(lat_servidor: float, lon_servidor: float, comision: Comision) -> dict:
    """
    Compara la ubicación del servidor con el lugar declarado de la comisión.
    Retorna validación de asistencia con distancia y si requiere justificación.
    """
    # Si la comisión no tiene coordenadas del lugar, solo informamos
    if comision.lugar_latitud is None or comision.lugar_longitud is None:
        return {
            "valida": None,
            "distancia_metros": None,
            "radio_tolerancia": comision.lugar_radio_metros or 300,
            "mensaje": "La comisión no tiene coordenadas del lugar de destino. Validación informativa.",
            "requiere_justificacion": False,
        }

    distancia = calcular_distancia_metros(
        lat_servidor, lon_servidor,
        float(comision.lugar_latitud), float(comision.lugar_longitud),
    )
    radio = comision.lugar_radio_metros or 300

    if distancia <= radio:
        return {
            "valida": True,
            "distancia_metros": distancia,
            "radio_tolerancia": radio,
            "mensaje": f"Ubicación validada. Distancia: {distancia}m (dentro del radio de {radio}m).",
            "requiere_justificacion": False,
        }
    else:
        return {
            "valida": False,
            "distancia_metros": distancia,
            "radio_tolerancia": radio,
            "mensaje": (
                f"Ubicación fuera del radio permitido. "
                f"Distancia: {distancia}m, radio tolerado: {radio}m."
            ),
            "requiere_justificacion": True,
        }


async def registrar_asistencia(
    gasto_id: int,
    lat: float,
    lon: float,
    foto_bytes: bytes,
    db: Session,
) -> dict:
    """
    Registra asistencia/presencia del servidor en campo.
    1. Calcula distancia al lugar declarado.
    2. Sube selfie a GCS.
    3. Actualiza campos de asistencia en GastoComision.
    4. Retorna resultado.
    """
    import uuid as uuid_mod
    from datetime import datetime, timezone
    from app.services.gcs_service import subir_foto_sync

    gasto = db.execute(
        select(GastoComision).where(GastoComision.id == gasto_id)
    ).scalar_one_or_none()
    if not gasto:
        raise ValueError(f"Gasto {gasto_id} no encontrado")

    comision = db.execute(
        select(Comision).where(Comision.id == gasto.comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión del gasto {gasto_id} no encontrada")

    # 1. Validar asistencia (distancia)
    resultado_asistencia = validar_asistencia(lat, lon, comision)

    # 2. Subir selfie a GCS
    foto_url = None
    try:
        gcs_path = subir_foto_sync(
            ruc_empresa=f"rendipe/{comision.tenant_id}/{comision.id}",
            contenido=foto_bytes,
            extension="jpg",
        )
        foto_url = gcs_path
    except Exception as e:
        logger.warning(f"Error subiendo selfie a GCS: {e}")

    # 3. Actualizar gasto
    gasto.latitud = Decimal(str(lat))
    gasto.longitud = Decimal(str(lon))
    gasto.asistencia_validada = resultado_asistencia.get("valida")
    gasto.asistencia_distancia_m = resultado_asistencia.get("distancia_metros")
    gasto.asistencia_foto_gcs = foto_url
    gasto.asistencia_timestamp = datetime.now(timezone.utc)

    db.commit()
    db.refresh(gasto)

    logger.info(
        f"Asistencia registrada para gasto #{gasto_id}: "
        f"distancia={resultado_asistencia.get('distancia_metros')}m, "
        f"valida={resultado_asistencia.get('valida')}"
    )

    return {
        "gasto_id": gasto.id,
        "asistencia_validada": resultado_asistencia.get("valida"),
        "distancia_metros": resultado_asistencia.get("distancia_metros"),
        "radio_tolerancia": resultado_asistencia.get("radio_tolerancia"),
        "mensaje": resultado_asistencia.get("mensaje"),
        "requiere_justificacion": resultado_asistencia.get("requiere_justificacion"),
        "foto_gcs": foto_url,
    }


async def crear_gasto_dj(
    comision_id: int,
    rubro: str,
    monto: float,
    descripcion: str,
    establecimiento: str,
    motivo_sin_ce: str,
    fecha_gasto: date,
    db: Session,
) -> dict:
    """
    Crea un gasto por Declaración Jurada (sin comprobante electrónico).
    Valida límites: porcentaje máximo DJ y monto/día máximo.
    Retorna el gasto creado con advertencias si los límites están cerca o excedidos.
    """
    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    # Verificar límites DJ
    limites = validar_limites_dj(comision_id, db)
    advertencias = []

    # Verificar monto por día
    if comision.dj_monto_dia_max:
        # Sumar gastos DJ del mismo día
        gastos_dia = db.execute(
            select(func.coalesce(func.sum(GastoComision.monto), Decimal("0"))).where(
                GastoComision.comision_id == comision_id,
                GastoComision.origen == OrigenGasto.DECLARACION_JURADA,
                GastoComision.fecha_emision == fecha_gasto,
                GastoComision.estado_validacion != EstadoValidacionGasto.BLOQUEADO,
            )
        ).scalar() or Decimal("0")

        if Decimal(str(monto)) + gastos_dia > comision.dj_monto_dia_max:
            advertencias.append(
                f"Excede límite diario DJ: S/ {gastos_dia + Decimal(str(monto))} "
                f"de S/ {comision.dj_monto_dia_max} permitidos."
            )

    # Verificar porcentaje total
    if comision.dj_porcentaje_max:
        nuevo_acum = limites["monto_dj_acumulado"] + monto
        nuevo_pct = (nuevo_acum / float(comision.total_viaticos) * 100) if comision.total_viaticos else 0
        if nuevo_pct > comision.dj_porcentaje_max:
            advertencias.append(
                f"Excede {comision.dj_porcentaje_max}% máximo DJ: "
                f"acumulado sería S/ {nuevo_acum:.2f} ({nuevo_pct:.1f}%)."
            )

    # Crear el gasto DJ
    gasto = GastoComision(
        comision_id=comision_id,
        servidor_id=comision.servidor_id,
        rubro=rubro,
        monto=Decimal(str(monto)),
        moneda=comision.moneda,
        descripcion=descripcion,
        fecha_emision=fecha_gasto,
        origen=OrigenGasto.DECLARACION_JURADA,
        estado_validacion=EstadoValidacionGasto.SIN_COMPROBANTE_ELECTRONICO,
        dj_motivo=motivo_sin_ce,
        dj_establecimiento=establecimiento,
    )
    db.add(gasto)
    db.commit()
    db.refresh(gasto)

    logger.info(f"Gasto DJ #{gasto.id} creado para comisión {comision_id}: S/ {monto}")

    return {
        "gasto_id": gasto.id,
        "monto": float(gasto.monto),
        "rubro": gasto.rubro,
        "estado_validacion": gasto.estado_validacion.value,
        "dj_motivo": gasto.dj_motivo,
        "advertencias": advertencias,
    }


async def generar_pdf_dj(gasto_id: int, db: Session) -> bytes:
    """
    Genera un PDF de Declaración Jurada para un gasto sin comprobante.
    Incluye: encabezado institucional, datos del servidor, datos de la comisión,
    datos del gasto, texto legal de DJ, y espacio para firma.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    gasto = db.execute(
        select(GastoComision).where(GastoComision.id == gasto_id)
    ).scalar_one_or_none()
    if not gasto:
        raise ValueError(f"Gasto {gasto_id} no encontrado")

    comision = db.execute(
        select(Comision).where(Comision.id == gasto.comision_id)
    ).scalar_one_or_none()

    servidor = db.execute(
        select(Servidor).where(Servidor.id == gasto.servidor_id)
    ).scalar_one_or_none()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "BodyDJ",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        alignment=4,  # TA_JUSTIFY
    )

    elements = []

    # Encabezado
    elements.append(Paragraph("DECLARACION JURADA", styles["Title"]))
    elements.append(Paragraph("(Gasto sin Comprobante de Pago Electronico)", styles["Normal"]))
    elements.append(Spacer(1, 0.8 * cm))

    # Datos del servidor
    if servidor:
        info_data = [
            ["Servidor:", f"{servidor.nombres} {servidor.apellidos}"],
            ["DNI:", servidor.dni],
            ["Cargo:", servidor.cargo or "No especificado"],
        ]
        info_table = Table(info_data, colWidths=[4 * cm, 12 * cm])
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 0.5 * cm))

    # Datos de la comisión
    if comision:
        com_data = [
            ["Comision a:", comision.destino_ciudad],
            ["Periodo:", f"{comision.fecha_inicio} al {comision.fecha_fin}"],
            ["Resolucion:", comision.resolucion_numero or "—"],
        ]
        com_table = Table(com_data, colWidths=[4 * cm, 12 * cm])
        com_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        elements.append(com_table)
        elements.append(Spacer(1, 0.5 * cm))

    # Datos del gasto
    gasto_data = [
        ["Fecha del gasto:", str(gasto.fecha_emision or "—")],
        ["Rubro:", gasto.rubro],
        ["Monto:", f"S/ {gasto.monto}"],
        ["Descripcion:", gasto.descripcion or "—"],
        ["Establecimiento:", gasto.dj_establecimiento or "—"],
        ["Motivo sin C.E.:", gasto.dj_motivo or "—"],
    ]
    gasto_table = Table(gasto_data, colWidths=[4 * cm, 12 * cm])
    gasto_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9f9f9")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(gasto_table)
    elements.append(Spacer(1, 1 * cm))

    # Texto legal
    texto_legal = (
        "Yo, el suscrito, DECLARO BAJO JURAMENTO que el gasto arriba detallado "
        "fue realizado en el marco de la comision de servicio indicada, que no me fue "
        "posible obtener comprobante de pago electronico por las razones senaladas, "
        "y que el monto declarado corresponde fielmente al gasto efectuado. "
        "Asumo la responsabilidad administrativa, civil y/o penal que pudiera "
        "derivarse de la falsedad de la presente declaracion, conforme a lo "
        "establecido en el articulo 42 del TUO de la Ley 27444 — Ley del "
        "Procedimiento Administrativo General."
    )
    elements.append(Paragraph(texto_legal, body_style))
    elements.append(Spacer(1, 2.5 * cm))

    # Firma
    firma_data = [
        ["________________________"],
        [f"{servidor.nombres} {servidor.apellidos}" if servidor else "Servidor"],
        [f"DNI: {servidor.dni}" if servidor else ""],
    ]
    firma_table = Table(firma_data, colWidths=[8 * cm])
    firma_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements.append(firma_table)

    doc.build(elements)

    # Guardar referencia del PDF en el gasto
    pdf_bytes = buffer.getvalue()

    return pdf_bytes


def validar_limites_dj(comision_id: int, db: Session) -> dict:
    """
    Calcula el acumulado de gastos DJ vs límites configurados en la comisión.
    Retorna: monto acumulado, límites, porcentaje usado, si puede agregar más.
    """
    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    # Sumar gastos DJ
    monto_dj = db.execute(
        select(func.coalesce(func.sum(GastoComision.monto), Decimal("0"))).where(
            GastoComision.comision_id == comision_id,
            GastoComision.origen == OrigenGasto.DECLARACION_JURADA,
            GastoComision.estado_validacion != EstadoValidacionGasto.BLOQUEADO,
        )
    ).scalar() or Decimal("0")

    total_viaticos = float(comision.total_viaticos) if comision.total_viaticos else 0
    monto_dj_float = float(monto_dj)

    porcentaje_usado = (monto_dj_float / total_viaticos * 100) if total_viaticos > 0 else 0

    # Límite de porcentaje
    limite_porcentaje = comision.dj_porcentaje_max or 100
    limite_monto = round(total_viaticos * limite_porcentaje / 100, 2) if total_viaticos > 0 else 0

    puede_agregar = monto_dj_float < limite_monto

    return {
        "monto_dj_acumulado": monto_dj_float,
        "limite_porcentaje": limite_porcentaje,
        "limite_monto": limite_monto,
        "porcentaje_usado": round(porcentaje_usado, 1),
        "puede_agregar_mas": puede_agregar,
        "dj_monto_dia_max": float(comision.dj_monto_dia_max) if comision.dj_monto_dia_max else None,
    }


async def crear_gasto_exterior(
    comision_id: int,
    rubro: str,
    monto_ext: float,
    moneda_ext: str,
    descripcion: str,
    establecimiento: str,
    fecha_gasto: date,
    tipo_cambio: Optional[float],
    db: Session,
) -> dict:
    """
    Crea un gasto en moneda extranjera para comisión internacional.
    No se valida contra SUNAT. Se convierte a PEN usando tipo de cambio BCRP.
    estado_validacion = 'sin_comprobante_electronico' (exterior no tiene CE peruano).
    """
    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if not comision:
        raise ValueError(f"Comisión {comision_id} no encontrada")

    if not comision.es_exterior:
        raise ValueError("La comisión no está marcada como internacional/exterior")

    # Si no se proporciona tipo de cambio, intentar obtener del BCRP
    tc = tipo_cambio
    if tc is None:
        # Fallback: usar un TC por defecto razonable o lanzar error
        logger.warning(
            f"Tipo de cambio no proporcionado para gasto exterior en {moneda_ext}. "
            "Se requiere ingreso manual."
        )
        raise ValueError(
            f"Debe proporcionar el tipo de cambio {moneda_ext}/PEN. "
            "Consulte el tipo de cambio BCRP del día del gasto."
        )

    monto_pen = round(monto_ext * tc, 2)

    gasto = GastoComision(
        comision_id=comision_id,
        servidor_id=comision.servidor_id,
        rubro=rubro,
        monto=Decimal(str(monto_pen)),
        moneda="PEN",
        monto_moneda_ext=Decimal(str(monto_ext)),
        tipo_cambio_usado=Decimal(str(tc)),
        descripcion=descripcion,
        dj_establecimiento=establecimiento,
        fecha_emision=fecha_gasto,
        origen=OrigenGasto.MANUAL,
        estado_validacion=EstadoValidacionGasto.SIN_COMPROBANTE_ELECTRONICO,
    )
    db.add(gasto)
    db.commit()
    db.refresh(gasto)

    logger.info(
        f"Gasto exterior #{gasto.id} creado: {moneda_ext} {monto_ext} "
        f"(TC {tc}) = PEN {monto_pen}"
    )

    return {
        "gasto_id": gasto.id,
        "monto_moneda_ext": monto_ext,
        "moneda_ext": moneda_ext,
        "tipo_cambio": tc,
        "monto_pen": monto_pen,
        "rubro": gasto.rubro,
        "estado_validacion": gasto.estado_validacion.value,
    }
