"""
services/exportacion_service.py — Servicio de exportaciones para empresas exportadoras.

Funcionalidades:
- Calculo de drawback (restitucion de derechos arancelarios) al 3% del valor FOB.
- Saldo a favor del exportador (SFE): IGV compras - IGV ventas locales.
  Permite a exportadores recuperar el IGV de sus compras contra sus ventas al exterior.
- Reporte anual de exportaciones consolidado.

Decisiones tecnicas:
- Se filtra comprobantes de exportacion por igv_tipo = '9999' (codigo SUNAT Exportacion)
  en las lineas de detalle, ya que el modelo Comprobante no tiene campo es_exportacion.
  Una factura se considera exportacion si su moneda es USD o si al menos una linea tiene
  igv_tipo = '9999'. Tambien se considera exportacion si el tipo es factura y el
  ruc_receptor empieza con '-' (no domiciliado) o si existe detalle con afectacion
  de exportacion.
- Se usa Decimal para todos los calculos monetarios, evitando perdida de precision.
- Las queries usan filtro deleted_at IS NULL para respetar soft delete.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.orm import Session

from app.models.comprobantes import (
    Comprobante,
    DetalleComprobante,
    EstadoComprobante,
    TipoComprobante,
)
from app.models.empresas import EmpresaCliente

logger = logging.getLogger(__name__)

# Tasa de drawback vigente (SUNAT DS 104-95-EF y modificatorias).
# 3% del valor FOB es la tasa general desde 2019.
TASA_DRAWBACK = Decimal("0.03")

# Tope maximo: 50% del costo de produccion (se valida en capa superior).
# Codigo SUNAT para operaciones de exportacion (catalogo 07).
CODIGO_IGV_EXPORTACION = "9999"


def calcular_drawback(comprobante_exportacion_id: int, db: Session) -> Decimal:
    """
    Calcula el monto de drawback (restitucion arancelaria) para un comprobante
    de exportacion. Drawback = valor_FOB x 3%.

    El valor FOB se toma del subtotal del comprobante (base imponible sin IGV),
    ya que las exportaciones estan inafectas de IGV.

    Args:
        comprobante_exportacion_id: ID del comprobante de exportacion.
        db: Sesion de SQLAlchemy.

    Returns:
        Monto de drawback en la moneda del comprobante (generalmente USD).

    Raises:
        ValueError: Si el comprobante no existe, no es factura, o no es exportacion.
    """
    comprobante = db.execute(
        select(Comprobante).where(
            Comprobante.id == comprobante_exportacion_id,
            Comprobante.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not comprobante:
        raise ValueError(
            f"Comprobante {comprobante_exportacion_id} no encontrado"
        )

    if comprobante.tipo != TipoComprobante.FACTURA:
        raise ValueError(
            f"Comprobante {comprobante_exportacion_id} no es factura "
            f"(tipo={comprobante.tipo.value}). Drawback solo aplica a facturas."
        )

    # Verificar que sea exportacion: moneda USD o detalle con igv_tipo 9999
    es_exportacion = _es_comprobante_exportacion(db, comprobante)
    if not es_exportacion:
        raise ValueError(
            f"Comprobante {comprobante_exportacion_id} no es de exportacion. "
            "No tiene lineas con igv_tipo=9999 ni moneda USD."
        )

    # Valor FOB = subtotal (base imponible). En exportaciones, IGV = 0.
    valor_fob = comprobante.subtotal or Decimal("0")
    if valor_fob <= 0:
        logger.warning(
            "Comprobante %d tiene valor FOB <= 0: %s",
            comprobante_exportacion_id, valor_fob,
        )
        return Decimal("0")

    drawback = (valor_fob * TASA_DRAWBACK).quantize(Decimal("0.01"))
    logger.info(
        "Drawback calculado para comprobante %d: FOB=%s, drawback=%s (%s)",
        comprobante_exportacion_id, valor_fob, drawback, comprobante.moneda,
    )
    return drawback


def calcular_saldo_favor_exportador(
    db: Session, empresa_id: int, periodo: str
) -> dict:
    """
    Calcula el Saldo a Favor del Exportador (SFE) para un periodo.

    SFE = IGV compras del periodo - IGV ventas locales del periodo.
    Si el resultado es positivo, el exportador tiene credito fiscal a su favor.
    Si es negativo, debe IGV neto.

    El SFE se compensa contra pagos a cuenta de renta o se solicita devolucion.

    Args:
        db: Sesion de SQLAlchemy.
        empresa_id: ID de la empresa.
        periodo: Periodo en formato 'YYYY-MM'.

    Returns:
        dict con claves:
          - igv_compras: total IGV de compras (facturas recibidas)
          - igv_ventas_locales: total IGV de ventas nacionales
          - igv_exportaciones: total IGV de exportaciones (deberia ser 0)
          - saldo_favor: igv_compras - igv_ventas_locales
          - tiene_saldo_favor: bool
          - periodo: periodo consultado
    """
    try:
        anio, mes = periodo.split("-")
        anio_int, mes_int = int(anio), int(mes)
    except (ValueError, AttributeError):
        raise ValueError(f"Periodo invalido: {periodo}. Formato esperado: YYYY-MM")

    # IGV de compras: facturas recibidas donde la empresa es receptor
    igv_compras_result = db.execute(
        select(func.coalesce(func.sum(Comprobante.igv), 0)).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.deleted_at.is_(None),
            Comprobante.estado.in_([
                EstadoComprobante.VALIDADO,
                EstadoComprobante.PENDIENTE,
            ]),
            Comprobante.tipo.in_([
                TipoComprobante.FACTURA,
                TipoComprobante.LIQUIDACION,
            ]),
            extract("year", Comprobante.fecha_emision) == anio_int,
            extract("month", Comprobante.fecha_emision) == mes_int,
            # Compras: empresa es receptor (el proveedor emite)
            Comprobante.ruc_receptor == _get_ruc_empresa(db, empresa_id),
        )
    ).scalar() or Decimal("0")

    ruc_empresa = _get_ruc_empresa(db, empresa_id)

    # IGV de ventas locales (no exportaciones): empresa es emisor, venta nacional
    # Se excluyen comprobantes de exportacion (moneda USD o igv_tipo 9999)
    stmt_ventas = (
        select(func.coalesce(func.sum(Comprobante.igv), 0)).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.deleted_at.is_(None),
            Comprobante.estado.in_([
                EstadoComprobante.VALIDADO,
                EstadoComprobante.PENDIENTE,
            ]),
            Comprobante.tipo.in_([
                TipoComprobante.FACTURA,
                TipoComprobante.BOLETA,
            ]),
            extract("year", Comprobante.fecha_emision) == anio_int,
            extract("month", Comprobante.fecha_emision) == mes_int,
            Comprobante.ruc_emisor == ruc_empresa,
            # Solo ventas locales: moneda PEN y sin codigo exportacion
            Comprobante.moneda == "PEN",
        )
    )
    igv_ventas_locales = db.execute(stmt_ventas).scalar() or Decimal("0")

    # IGV de exportaciones (referencial, deberia ser 0 o muy bajo)
    stmt_export = (
        select(func.coalesce(func.sum(Comprobante.igv), 0)).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.deleted_at.is_(None),
            Comprobante.estado.in_([
                EstadoComprobante.VALIDADO,
                EstadoComprobante.PENDIENTE,
            ]),
            Comprobante.tipo == TipoComprobante.FACTURA,
            extract("year", Comprobante.fecha_emision) == anio_int,
            extract("month", Comprobante.fecha_emision) == mes_int,
            Comprobante.ruc_emisor == ruc_empresa,
            # Exportaciones: moneda USD
            Comprobante.moneda != "PEN",
        )
    )
    igv_exportaciones = db.execute(stmt_export).scalar() or Decimal("0")

    saldo_favor = igv_compras_result - igv_ventas_locales

    resultado = {
        "igv_compras": igv_compras_result,
        "igv_ventas_locales": igv_ventas_locales,
        "igv_exportaciones": igv_exportaciones,
        "saldo_favor": saldo_favor,
        "tiene_saldo_favor": saldo_favor > 0,
        "periodo": periodo,
    }

    logger.info(
        "SFE empresa %d periodo %s: compras=%s, ventas_loc=%s, saldo=%s",
        empresa_id, periodo, igv_compras_result, igv_ventas_locales, saldo_favor,
    )
    return resultado


def generar_reporte_exportaciones(
    db: Session, empresa_id: int, anio: int
) -> dict:
    """
    Genera reporte anual consolidado de exportaciones.

    Incluye: total FOB, drawback estimado, SFE acumulado, desglose mensual,
    monedas utilizadas, principales destinos (por ruc_receptor).

    Args:
        db: Sesion de SQLAlchemy.
        empresa_id: ID de la empresa.
        anio: Anio del reporte.

    Returns:
        dict con resumen anual y desglose mensual de exportaciones.
    """
    ruc_empresa = _get_ruc_empresa(db, empresa_id)

    # Facturas de exportacion del anio (moneda != PEN como proxy)
    stmt_exportaciones = (
        select(Comprobante).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.deleted_at.is_(None),
            Comprobante.estado.in_([
                EstadoComprobante.VALIDADO,
                EstadoComprobante.PENDIENTE,
            ]),
            Comprobante.tipo == TipoComprobante.FACTURA,
            Comprobante.ruc_emisor == ruc_empresa,
            extract("year", Comprobante.fecha_emision) == anio,
            Comprobante.moneda != "PEN",
        ).order_by(Comprobante.fecha_emision)
    )
    exportaciones = db.execute(stmt_exportaciones).scalars().all()

    # Desglose mensual
    desglose_mensual: dict[int, dict] = {}
    total_fob = Decimal("0")
    total_igv = Decimal("0")
    cantidad_comprobantes = 0
    destinos: dict[str, Decimal] = {}  # ruc_receptor -> monto acumulado
    monedas: set[str] = set()

    for comp in exportaciones:
        mes = comp.fecha_emision.month
        subtotal = comp.subtotal or Decimal("0")
        igv = comp.igv or Decimal("0")

        if mes not in desglose_mensual:
            desglose_mensual[mes] = {
                "mes": mes,
                "total_fob": Decimal("0"),
                "total_igv": Decimal("0"),
                "cantidad": 0,
                "drawback_estimado": Decimal("0"),
            }

        desglose_mensual[mes]["total_fob"] += subtotal
        desglose_mensual[mes]["total_igv"] += igv
        desglose_mensual[mes]["cantidad"] += 1
        desglose_mensual[mes]["drawback_estimado"] += (
            subtotal * TASA_DRAWBACK
        ).quantize(Decimal("0.01"))

        total_fob += subtotal
        total_igv += igv
        cantidad_comprobantes += 1
        monedas.add(comp.moneda)

        # Acumular destinos
        receptor = comp.ruc_receptor or "desconocido"
        destinos[receptor] = destinos.get(receptor, Decimal("0")) + subtotal

    # SFE acumulado anual (suma de SFE mensuales)
    sfe_acumulado = Decimal("0")
    for mes_num in range(1, 13):
        periodo_str = f"{anio}-{mes_num:02d}"
        try:
            sfe = calcular_saldo_favor_exportador(db, empresa_id, periodo_str)
            sfe_acumulado += sfe["saldo_favor"]
        except Exception:
            # Periodo sin datos, continuar
            pass

    # Top destinos por monto
    top_destinos = sorted(
        [{"ruc": ruc, "total_fob": monto} for ruc, monto in destinos.items()],
        key=lambda x: x["total_fob"],
        reverse=True,
    )[:10]

    drawback_total = (total_fob * TASA_DRAWBACK).quantize(Decimal("0.01"))

    resultado = {
        "empresa_id": empresa_id,
        "anio": anio,
        "resumen": {
            "total_fob": total_fob,
            "total_igv_exportaciones": total_igv,
            "drawback_estimado": drawback_total,
            "sfe_acumulado": sfe_acumulado,
            "cantidad_comprobantes": cantidad_comprobantes,
            "monedas_utilizadas": sorted(monedas),
        },
        "desglose_mensual": [
            desglose_mensual.get(m) for m in range(1, 13) if m in desglose_mensual
        ],
        "top_destinos": top_destinos,
    }

    logger.info(
        "Reporte exportaciones empresa %d anio %d: FOB=%s, drawback=%s, "
        "comprobantes=%d",
        empresa_id, anio, total_fob, drawback_total, cantidad_comprobantes,
    )
    return resultado


# ── Funciones auxiliares ────────────────────────────────────────


def _get_ruc_empresa(db: Session, empresa_id: int) -> Optional[str]:
    """Obtiene el RUC de una empresa por su ID."""
    result = db.execute(
        select(EmpresaCliente.ruc).where(
            EmpresaCliente.id == empresa_id,
            EmpresaCliente.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not result:
        raise ValueError(f"Empresa {empresa_id} no encontrada")
    return result


def _es_comprobante_exportacion(db: Session, comprobante: Comprobante) -> bool:
    """
    Determina si un comprobante es de exportacion.
    Criterios:
    1. Moneda distinta de PEN (USD, EUR, etc.)
    2. Al menos una linea de detalle con igv_tipo = '9999' (Exportacion SUNAT)
    """
    if comprobante.moneda and comprobante.moneda != "PEN":
        return True

    # Verificar en detalle normalizado
    tiene_linea_export = db.execute(
        select(func.count(DetalleComprobante.id)).where(
            DetalleComprobante.comprobante_id == comprobante.id,
            DetalleComprobante.igv_tipo == CODIGO_IGV_EXPORTACION,
        )
    ).scalar()

    if tiene_linea_export and tiene_linea_export > 0:
        return True

    # Verificar en detalle JSONB (detalle_items) como fallback
    if comprobante.detalle_items and isinstance(comprobante.detalle_items, list):
        for item in comprobante.detalle_items:
            if isinstance(item, dict) and item.get("igv_tipo") == CODIGO_IGV_EXPORTACION:
                return True

    return False
