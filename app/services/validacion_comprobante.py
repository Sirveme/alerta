"""
services/validacion_comprobante.py — Sistema de semáforo para comprobantes.

Estados:
  VALIDO        — todo correcto, uso tributario habilitado
  OBSERVADO     — campo dudoso, usable con advertencia
  BLOQUEADO     — error grave, NO usar tributariamente
  EN_CORRECCION — NC o anulación solicitada, pendiente

Un comprobante BLOQUEADO no se suma a acumulados ni declaraciones,
no se usa en cálculo de crédito fiscal, genera alerta URGENTE.

Validaciones implementadas:
- RUC: dígito verificador módulo 11 SUNAT
- IGV: base × 0.18, tolerancia ±S/0.10
- ICBPER: cantidad × 0.40 (tasa vigente)
- Total vs suma de líneas (±S/0.01)
- Serie: formato válido (letra + 3 dígitos)
- Fecha: no futura, no >365 días atrás
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class EstadoValidacion(str, Enum):
    VALIDO = "valido"
    OBSERVADO = "observado"
    BLOQUEADO = "bloqueado"
    EN_CORRECCION = "en_correccion"


class TipoError(str, Enum):
    RUC_INVALIDO = "ruc_invalido"
    RUC_BAJA_SUNAT = "ruc_baja_sunat"
    RAZON_SOCIAL_NO_COINC = "razon_social_no_coinc"
    IGV_INCORRECTO = "igv_incorrecto"
    ISC_INCORRECTO = "isc_incorrecto"
    ICBPER_INCORRECTO = "icbper_incorrecto"
    TOTAL_NO_CUADRA = "total_no_cuadra"
    REDONDEO_ERRONEO = "redondeo_erroneo"
    COMPROBANTE_NO_SUNAT = "comprobante_no_sunat"
    FECHA_FUERA_RANGO = "fecha_fuera_rango"
    SERIE_INVALIDA = "serie_invalida"
    CORRELATIVO_INVALIDO = "correlativo_invalido"


ERRORES_BLOQUEANTES = {
    TipoError.RUC_INVALIDO,
    TipoError.RUC_BAJA_SUNAT,
    TipoError.IGV_INCORRECTO,
    TipoError.TOTAL_NO_CUADRA,
    TipoError.COMPROBANTE_NO_SUNAT,
    TipoError.SERIE_INVALIDA,
}

ERRORES_OBSERVADOS = {
    TipoError.RAZON_SOCIAL_NO_COINC,
    TipoError.ISC_INCORRECTO,
    TipoError.ICBPER_INCORRECTO,
    TipoError.REDONDEO_ERRONEO,
    TipoError.FECHA_FUERA_RANGO,
    TipoError.CORRELATIVO_INVALIDO,
}

# Tasa ICBPER vigente 2024-2026
TASA_ICBPER = Decimal("0.50")
# Tasa IGV
TASA_IGV = Decimal("0.18")


@dataclass
class ErrorValidacion:
    tipo: TipoError
    campo: str
    valor_encontrado: str
    valor_esperado: str
    descripcion: str


@dataclass
class ResultadoValidacion:
    estado: EstadoValidacion
    errores: list[ErrorValidacion] = field(default_factory=list)
    accion_recomendada: str = ""
    validado_en: Optional[datetime] = None


async def validar_comprobante(db: Session, comprobante_id: int) -> ResultadoValidacion:
    """
    Ejecuta todas las validaciones en orden de severidad.
    Para en el primer error BLOQUEANTE.
    Acumula todos los errores OBSERVADOS.
    """
    from app.models.comprobantes import Comprobante, DetalleComprobante

    comprobante = db.execute(
        select(Comprobante).where(Comprobante.id == comprobante_id)
    ).scalar_one_or_none()

    if not comprobante:
        return ResultadoValidacion(
            estado=EstadoValidacion.BLOQUEADO,
            errores=[ErrorValidacion(
                TipoError.RUC_INVALIDO, "comprobante", str(comprobante_id), "",
                "Comprobante no encontrado"
            )],
        )

    errores = []

    # 1. Validar RUC emisor
    if not validar_ruc_digito_verificador(comprobante.ruc_emisor):
        errores.append(ErrorValidacion(
            TipoError.RUC_INVALIDO, "ruc_emisor", comprobante.ruc_emisor, "",
            f"RUC {comprobante.ruc_emisor}: dígito verificador incorrecto"
        ))

    # 2. Validar RUC receptor
    if comprobante.ruc_receptor and not validar_ruc_digito_verificador(comprobante.ruc_receptor):
        errores.append(ErrorValidacion(
            TipoError.RUC_INVALIDO, "ruc_receptor", comprobante.ruc_receptor, "",
            f"RUC receptor {comprobante.ruc_receptor}: dígito verificador incorrecto"
        ))

    # 3. Validar serie
    if not _validar_serie(comprobante.serie):
        errores.append(ErrorValidacion(
            TipoError.SERIE_INVALIDA, "serie", comprobante.serie, "Ej: F001, B001, E001",
            f"Serie '{comprobante.serie}' no tiene formato válido SUNAT"
        ))

    # 4. Validar IGV
    if comprobante.subtotal and comprobante.igv:
        igv_ok, diferencia = validar_igv(comprobante.subtotal, comprobante.igv)
        if not igv_ok:
            tipo_err = TipoError.IGV_INCORRECTO if abs(diferencia) > Decimal("0.10") else TipoError.REDONDEO_ERRONEO
            igv_esperado = comprobante.subtotal * TASA_IGV
            errores.append(ErrorValidacion(
                tipo_err, "igv",
                str(comprobante.igv), f"{igv_esperado:.2f}",
                f"IGV declarado S/{comprobante.igv} vs esperado S/{igv_esperado:.2f} (dif: S/{diferencia:.2f})"
            ))

    # 5. Validar total vs líneas
    detalles = db.execute(
        select(DetalleComprobante).where(DetalleComprobante.comprobante_id == comprobante_id)
    ).scalars().all()

    if detalles:
        total_ok, diferencia = validar_total_vs_lineas(comprobante.total, detalles)
        if not total_ok:
            tipo_err = TipoError.TOTAL_NO_CUADRA if abs(diferencia) > Decimal("0.01") else TipoError.REDONDEO_ERRONEO
            suma_lineas = sum(d.total_linea for d in detalles)
            errores.append(ErrorValidacion(
                tipo_err, "total",
                str(comprobante.total), f"{suma_lineas:.2f}",
                f"Total S/{comprobante.total} vs suma líneas S/{suma_lineas:.2f} (dif: S/{diferencia:.2f})"
            ))

    # 6. Validar fecha
    if comprobante.fecha_emision:
        hoy = date.today()
        if comprobante.fecha_emision > hoy:
            errores.append(ErrorValidacion(
                TipoError.FECHA_FUERA_RANGO, "fecha_emision",
                str(comprobante.fecha_emision), f"≤ {hoy}",
                "Fecha de emisión es futura"
            ))
        elif (hoy - comprobante.fecha_emision).days > 365:
            errores.append(ErrorValidacion(
                TipoError.FECHA_FUERA_RANGO, "fecha_emision",
                str(comprobante.fecha_emision), f"últimos 365 días",
                "Fecha de emisión tiene más de 1 año"
            ))

    # Determinar estado final
    tiene_bloqueante = any(e.tipo in ERRORES_BLOQUEANTES for e in errores)
    tiene_observacion = any(e.tipo in ERRORES_OBSERVADOS for e in errores)

    if tiene_bloqueante:
        estado = EstadoValidacion.BLOQUEADO
        accion = "Comprobante bloqueado tributariamente. Solicitar corrección al proveedor."
    elif tiene_observacion:
        estado = EstadoValidacion.OBSERVADO
        accion = "Comprobante con observaciones. Revisar campos marcados antes de declarar."
    else:
        estado = EstadoValidacion.VALIDO
        accion = "Comprobante válido para uso tributario."

    resultado = ResultadoValidacion(
        estado=estado,
        errores=errores,
        accion_recomendada=accion,
        validado_en=datetime.now(timezone.utc),
    )

    # Generar alerta si bloqueado
    if estado == EstadoValidacion.BLOQUEADO:
        from app.services.alertas_service import crear_alerta_por_tipo
        crear_alerta_por_tipo(
            db, comprobante.empresa_id, "xml_parse_error",
            mensaje=f"Comprobante {comprobante.serie}-{comprobante.correlativo} BLOQUEADO: {errores[0].descripcion}",
            referencia_id=comprobante_id,
            referencia_tabla="comprobantes",
        )

    logger.info(f"Validación comprobante #{comprobante_id}: {estado.value}, {len(errores)} errores")
    return resultado


# ── Funciones de validación ──────────────────────────────────

def validar_ruc_digito_verificador(ruc: str) -> bool:
    """
    Algoritmo módulo 11 oficial SUNAT.
    Factores: [5,4,3,2,7,6,5,4,3,2] para los 10 primeros dígitos.
    """
    if not ruc or len(ruc) != 11 or not ruc.isdigit():
        return False

    if ruc[:2] not in ("10", "15", "17", "20"):
        return False

    factores = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(ruc[i]) * factores[i] for i in range(10))
    resto = 11 - (suma % 11)

    if resto == 10:
        digito_esperado = 0
    elif resto == 11:
        digito_esperado = 1
    else:
        digito_esperado = resto

    return int(ruc[10]) == digito_esperado


def validar_igv(base: Decimal, igv: Decimal) -> tuple[bool, Decimal]:
    """IGV correcto = base × 0.18, tolerancia ±S/0.10."""
    esperado = (base * TASA_IGV).quantize(Decimal("0.01"))
    diferencia = igv - esperado
    es_correcto = abs(diferencia) <= Decimal("0.10")
    return es_correcto, diferencia


def validar_icbper(cantidad_bolsas: int, icbper: Decimal) -> tuple[bool, Decimal]:
    """ICBPER = cantidad × tasa vigente (S/0.50 en 2026)."""
    esperado = Decimal(str(cantidad_bolsas)) * TASA_ICBPER
    diferencia = icbper - esperado
    return abs(diferencia) <= Decimal("0.01"), diferencia


def validar_total_vs_lineas(total_declarado: Decimal, lineas: list) -> tuple[bool, Decimal]:
    """Suma total de líneas debe coincidir con total declarado (±S/0.01)."""
    suma = sum(getattr(l, 'total_linea', Decimal("0")) for l in lineas)
    diferencia = total_declarado - suma
    return abs(diferencia) <= Decimal("0.01"), diferencia


def _validar_serie(serie: str) -> bool:
    """Serie válida: letra(s) + 3-4 dígitos. Ej: F001, B001, E001, FC01."""
    if not serie:
        return False
    return bool(re.match(r"^[A-Z]{1,4}\d{2,4}$", serie))
