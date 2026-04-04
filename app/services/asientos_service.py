"""
services/asientos_service.py — Generacion automatica de asientos contables PCGE Peru.

Genera asientos de partida doble para compras, ventas y pagos recibidos,
siguiendo el Plan Contable General Empresarial (PCGE 2020) peruano.

Cuentas PCGE utilizadas:
- Compras:  60x (Compras/DEBE), 40111 (IGV/DEBE), 421 (Facturas por pagar/HABER)
- Ventas:   121 (Facturas por cobrar/DEBE), 70x (Ventas/HABER), 40111 (IGV/HABER)
- Cobros:   104 (Cuentas corrientes/DEBE), 121 (Facturas por cobrar/HABER)

Decisiones tecnicas:
- numero_asiento se calcula como MAX(numero_asiento) + 1 filtrado por empresa+periodo.
  Esto es seguro en el contexto de transacciones serializables por empresa.
- La tolerancia de validacion (DEBE == HABER) es ±0.01 por redondeos de centimos.
- Todos los montos se trabajan como Decimal para evitar errores de punto flotante.
- La glosa se genera automaticamente a partir de los datos del comprobante.
- Los asientos se crean en estado BORRADOR para revision antes de exportar.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.comprobantes import Comprobante, TipoComprobante
from app.models.contabilidad import (
    AsientoContable,
    EstadoAsiento,
    GeneradoPor,
    LineaAsiento,
)
from app.models.pagos import Pago

logger = logging.getLogger(__name__)

# Tolerancia para validacion de partida doble (redondeo de centimos)
TOLERANCIA_PARTIDA_DOBLE = Decimal("0.01")


def _obtener_siguiente_numero(db: Session, empresa_id: int, periodo: str) -> int:
    """
    Obtiene el siguiente numero_asiento para una empresa en un periodo.
    Retorna 1 si no hay asientos previos en el periodo.
    """
    resultado = db.execute(
        select(func.coalesce(func.max(AsientoContable.numero_asiento), 0))
        .where(
            AsientoContable.empresa_id == empresa_id,
            AsientoContable.periodo == periodo,
        )
    ).scalar()
    return resultado + 1


def _periodo_desde_fecha(fecha: date) -> str:
    """Convierte una fecha a formato periodo YYYY-MM."""
    return fecha.strftime("%Y-%m")


def generar_asiento_compra(db: Session, comprobante_id: int) -> AsientoContable:
    """
    Genera un asiento contable de compra a partir de un comprobante.

    Esquema PCGE:
      DEBE  60x  Compras           = valor_venta (subtotal sin IGV)
      DEBE  40111 IGV Credito Fiscal = igv
      HABER 421   Facturas por pagar = total

    Args:
        db: Sesion SQLAlchemy.
        comprobante_id: ID del comprobante de compra.

    Returns:
        AsientoContable creado con sus lineas.

    Raises:
        ValueError: Si el comprobante no existe o no es una compra valida.
    """
    comprobante = db.get(Comprobante, comprobante_id)
    if comprobante is None:
        raise ValueError(f"Comprobante {comprobante_id} no encontrado")

    # Solo facturas y liquidaciones generan asiento de compra con credito fiscal
    if comprobante.tipo not in (TipoComprobante.FACTURA, TipoComprobante.LIQUIDACION):
        raise ValueError(
            f"Tipo de comprobante '{comprobante.tipo.value}' no genera asiento de compra "
            f"con credito fiscal. Solo facturas y liquidaciones."
        )

    periodo = _periodo_desde_fecha(comprobante.fecha_emision)
    numero = _obtener_siguiente_numero(db, comprobante.empresa_id, periodo)

    valor_venta = comprobante.subtotal
    igv = comprobante.igv
    total = comprobante.total

    # Construir glosa descriptiva
    glosa = (
        f"Compra {comprobante.serie}-{comprobante.correlativo} "
        f"/ {comprobante.razon_social_emisor or comprobante.ruc_emisor}"
    )

    asiento = AsientoContable(
        empresa_id=comprobante.empresa_id,
        comprobante_id=comprobante.id,
        periodo=periodo,
        numero_asiento=numero,
        fecha=comprobante.fecha_emision,
        glosa=glosa,
        moneda=comprobante.moneda,
        generado_por=GeneradoPor.AUTOMATICO,
        estado=EstadoAsiento.BORRADOR,
    )

    # Linea 1: DEBE 601 Compras (mercaderias por defecto; subcuenta exacta
    # dependeria del tipo de bien, pero usamos 601 como cuenta generica)
    linea_compras = LineaAsiento(
        orden=1,
        cuenta_codigo="601",
        denominacion="Mercaderias",
        debe=valor_venta,
        haber=Decimal("0"),
        glosa_linea="Compra de mercaderias/servicios",
    )

    # Linea 2: DEBE 40111 IGV - Cuenta propia (credito fiscal)
    linea_igv = LineaAsiento(
        orden=2,
        cuenta_codigo="40111",
        denominacion="IGV - Cuenta propia",
        debe=igv,
        haber=Decimal("0"),
        glosa_linea="IGV credito fiscal",
    )

    # Linea 3: HABER 4212 Emitidas (facturas por pagar)
    linea_por_pagar = LineaAsiento(
        orden=3,
        cuenta_codigo="4212",
        denominacion="Emitidas",
        debe=Decimal("0"),
        haber=total,
        glosa_linea="Factura por pagar al proveedor",
    )

    asiento.lineas = [linea_compras, linea_igv, linea_por_pagar]

    if not validar_asiento(asiento):
        logger.error(
            "Asiento de compra descuadrado para comprobante %d: "
            "debe=%s, haber=%s",
            comprobante_id,
            valor_venta + igv,
            total,
        )
        raise ValueError(
            f"Asiento descuadrado: DEBE={valor_venta + igv}, HABER={total}"
        )

    db.add(asiento)
    db.flush()

    logger.info(
        "Asiento de compra #%d generado para comprobante %d (empresa %d, periodo %s)",
        asiento.numero_asiento,
        comprobante_id,
        comprobante.empresa_id,
        periodo,
    )

    return asiento


def generar_asiento_venta(db: Session, comprobante_id: int) -> AsientoContable:
    """
    Genera un asiento contable de venta a partir de un comprobante.

    Esquema PCGE:
      DEBE  1212  Emitidas en cartera       = total
      HABER 70x   Ventas                    = valor_venta (subtotal sin IGV)
      HABER 40111 IGV - Cuenta propia       = igv

    Args:
        db: Sesion SQLAlchemy.
        comprobante_id: ID del comprobante de venta.

    Returns:
        AsientoContable creado con sus lineas.

    Raises:
        ValueError: Si el comprobante no existe.
    """
    comprobante = db.get(Comprobante, comprobante_id)
    if comprobante is None:
        raise ValueError(f"Comprobante {comprobante_id} no encontrado")

    periodo = _periodo_desde_fecha(comprobante.fecha_emision)
    numero = _obtener_siguiente_numero(db, comprobante.empresa_id, periodo)

    valor_venta = comprobante.subtotal
    igv = comprobante.igv
    total = comprobante.total

    glosa = (
        f"Venta {comprobante.serie}-{comprobante.correlativo} "
        f"/ {comprobante.razon_social_receptor or comprobante.ruc_receptor}"
    )

    asiento = AsientoContable(
        empresa_id=comprobante.empresa_id,
        comprobante_id=comprobante.id,
        periodo=periodo,
        numero_asiento=numero,
        fecha=comprobante.fecha_emision,
        glosa=glosa,
        moneda=comprobante.moneda,
        generado_por=GeneradoPor.AUTOMATICO,
        estado=EstadoAsiento.BORRADOR,
    )

    # Linea 1: DEBE 1212 Emitidas en cartera (facturas por cobrar)
    linea_por_cobrar = LineaAsiento(
        orden=1,
        cuenta_codigo="1212",
        denominacion="Emitidas en cartera",
        debe=total,
        haber=Decimal("0"),
        glosa_linea="Factura por cobrar al cliente",
    )

    # Linea 2: HABER 7011 Mercaderias manufacturadas (ventas, cuenta generica)
    linea_ventas = LineaAsiento(
        orden=2,
        cuenta_codigo="7011",
        denominacion="Mercaderias manufacturadas",
        debe=Decimal("0"),
        haber=valor_venta,
        glosa_linea="Venta de mercaderias/servicios",
    )

    # Linea 3: HABER 40111 IGV - Cuenta propia (debito fiscal)
    linea_igv = LineaAsiento(
        orden=3,
        cuenta_codigo="40111",
        denominacion="IGV - Cuenta propia",
        debe=Decimal("0"),
        haber=igv,
        glosa_linea="IGV debito fiscal",
    )

    asiento.lineas = [linea_por_cobrar, linea_ventas, linea_igv]

    if not validar_asiento(asiento):
        logger.error(
            "Asiento de venta descuadrado para comprobante %d: "
            "debe=%s, haber=%s",
            comprobante_id,
            total,
            valor_venta + igv,
        )
        raise ValueError(
            f"Asiento descuadrado: DEBE={total}, HABER={valor_venta + igv}"
        )

    db.add(asiento)
    db.flush()

    logger.info(
        "Asiento de venta #%d generado para comprobante %d (empresa %d, periodo %s)",
        asiento.numero_asiento,
        comprobante_id,
        comprobante.empresa_id,
        periodo,
    )

    return asiento


def generar_asiento_pago(
    db: Session,
    pago_id: int,
    comprobante_id: int,
) -> AsientoContable:
    """
    Genera un asiento contable por cobro/pago recibido.

    Esquema PCGE:
      DEBE  1041  Cuentas corrientes operativas = monto del pago
      HABER 1212  Emitidas en cartera            = monto del pago

    Nota: La cuenta 104 (Cuentas corrientes) se usa como cuenta generica.
    En una implementacion mas detallada se podria mapear el canal de pago
    a subcuentas especificas (104101 BCP, 104102 BBVA, etc.).

    Args:
        db: Sesion SQLAlchemy.
        pago_id: ID del pago recibido.
        comprobante_id: ID del comprobante que se esta cobrando.

    Returns:
        AsientoContable creado con sus lineas.

    Raises:
        ValueError: Si el pago o comprobante no existen.
    """
    pago = db.get(Pago, pago_id)
    if pago is None:
        raise ValueError(f"Pago {pago_id} no encontrado")

    comprobante = db.get(Comprobante, comprobante_id)
    if comprobante is None:
        raise ValueError(f"Comprobante {comprobante_id} no encontrado")

    periodo = _periodo_desde_fecha(pago.fecha_pago.date() if hasattr(pago.fecha_pago, 'date') else pago.fecha_pago)
    numero = _obtener_siguiente_numero(db, pago.empresa_id, periodo)

    monto = pago.monto

    glosa = (
        f"Cobro {comprobante.serie}-{comprobante.correlativo} "
        f"/ {pago.pagador_nombre or pago.pagador_documento or 'S/N'} "
        f"via {pago.canal.value}"
    )

    asiento = AsientoContable(
        empresa_id=pago.empresa_id,
        comprobante_id=comprobante.id,
        periodo=periodo,
        numero_asiento=numero,
        fecha=pago.fecha_pago.date() if hasattr(pago.fecha_pago, 'date') else pago.fecha_pago,
        glosa=glosa,
        moneda=pago.moneda,
        generado_por=GeneradoPor.AUTOMATICO,
        estado=EstadoAsiento.BORRADOR,
    )

    # Linea 1: DEBE 1041 Cuentas corrientes operativas
    linea_banco = LineaAsiento(
        orden=1,
        cuenta_codigo="1041",
        denominacion="Cuentas corrientes operativas",
        debe=monto,
        haber=Decimal("0"),
        glosa_linea=f"Deposito {pago.canal.value} op:{pago.numero_operacion or 'S/N'}",
    )

    # Linea 2: HABER 1212 Emitidas en cartera (cancela la cuenta por cobrar)
    linea_por_cobrar = LineaAsiento(
        orden=2,
        cuenta_codigo="1212",
        denominacion="Emitidas en cartera",
        debe=Decimal("0"),
        haber=monto,
        glosa_linea="Cancelacion de cuenta por cobrar",
    )

    asiento.lineas = [linea_banco, linea_por_cobrar]

    if not validar_asiento(asiento):
        logger.error(
            "Asiento de pago descuadrado para pago %d: monto=%s",
            pago_id,
            monto,
        )
        raise ValueError(f"Asiento descuadrado para pago {pago_id}")

    db.add(asiento)
    db.flush()

    logger.info(
        "Asiento de pago #%d generado para pago %d / comprobante %d "
        "(empresa %d, periodo %s)",
        asiento.numero_asiento,
        pago_id,
        comprobante_id,
        pago.empresa_id,
        periodo,
    )

    return asiento


def validar_asiento(asiento: AsientoContable) -> bool:
    """
    Valida que un asiento contable cumpla la partida doble:
    suma(DEBE) == suma(HABER) con tolerancia de ±0.01.

    La tolerancia existe porque los montos en soles pueden tener
    redondeos de centimo entre el subtotal, IGV y total del comprobante.

    Args:
        asiento: AsientoContable con sus lineas cargadas.

    Returns:
        True si el asiento esta cuadrado, False si no.
    """
    total_debe = sum(
        (linea.debe or Decimal("0")) for linea in asiento.lineas
    )
    total_haber = sum(
        (linea.haber or Decimal("0")) for linea in asiento.lineas
    )

    diferencia = abs(total_debe - total_haber)
    cuadrado = diferencia <= TOLERANCIA_PARTIDA_DOBLE

    if not cuadrado:
        logger.warning(
            "Asiento descuadrado: DEBE=%s, HABER=%s, diferencia=%s",
            total_debe,
            total_haber,
            diferencia,
        )

    return cuadrado
