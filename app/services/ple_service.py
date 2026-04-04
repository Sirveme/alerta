"""
services/ple_service.py — Generador de Libros Electronicos PLE para SUNAT.

Genera archivos TXT en formato PLE (Programa de Libros Electronicos) de SUNAT,
usados para la declaracion mensual de compras y ventas.

Formato PLE:
- Campos separados por "|" (pipe).
- Codificacion Latin-1 (ISO-8859-1), requerida por SUNAT.
- Cada registro termina con "|" (pipe final).
- Sin encabezado; SUNAT valida por posicion de campo.

Nomenclatura de archivos SUNAT:
- Ventas (Registro de Ventas 14.1): LE{RUC}{PERIODO}00140100001{indicador}{moneda}1.txt
  Simplificado: {RUC}{PERIODO}140100001.txt
- Compras (Registro de Compras 8.1): LE{RUC}{PERIODO}00080100001{indicador}{moneda}1.txt
  Simplificado: {RUC}{PERIODO}080100001.txt

Mapeo de tipos de comprobante a codigos SUNAT:
- 01 = Factura
- 03 = Boleta de venta
- 07 = Nota de credito
- 08 = Nota de debito
- 09 = Guia de remision
- 04 = Liquidacion de compra

Mapeo de tipos de documento de identidad:
- 1 = DNI
- 6 = RUC
- 4 = Carnet de extranjeria
- 7 = Pasaporte

Decisiones tecnicas:
- Se lee directamente de la tabla comprobantes filtrada por empresa_id y periodo.
- El CUO (Codigo Unico de Operacion) se genera como {empresa_id}-{periodo}-{correlativo}.
- Campos vacios se representan como cadena vacia entre pipes: "||".
- Los montos se formatean con 2 decimales sin separador de miles.
- La fecha se formatea como dd/mm/yyyy segun especificacion SUNAT.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.comprobantes import Comprobante, EstadoComprobante, TipoComprobante
from app.models.empresas import EmpresaCliente

logger = logging.getLogger(__name__)

# Mapeo de TipoComprobante a codigo SUNAT tabla 10
TIPO_COMPROBANTE_SUNAT: dict[TipoComprobante, str] = {
    TipoComprobante.FACTURA: "01",
    TipoComprobante.BOLETA: "03",
    TipoComprobante.NOTA_CREDITO: "07",
    TipoComprobante.NOTA_DEBITO: "08",
    TipoComprobante.GUIA_REMISION: "09",
    TipoComprobante.LIQUIDACION: "04",
}

# Mapeo de longitud de documento a tipo de documento SUNAT tabla 2
# 8 digitos = DNI (1), 11 digitos = RUC (6), otro = sin documento (0)
TIPO_DOC_POR_LONGITUD: dict[int, str] = {
    8: "1",   # DNI
    11: "6",  # RUC
    12: "4",  # Carnet de extranjeria
}


def _tipo_documento_identidad(numero_doc: Optional[str]) -> str:
    """Determina el tipo de documento de identidad segun la longitud."""
    if not numero_doc:
        return "0"
    return TIPO_DOC_POR_LONGITUD.get(len(numero_doc.strip()), "0")


def _formato_fecha(fecha: Optional[date]) -> str:
    """Formatea fecha como dd/mm/yyyy para PLE SUNAT."""
    if fecha is None:
        return ""
    return fecha.strftime("%d/%m/%Y")


def _formato_monto(monto: Optional[Decimal]) -> str:
    """Formatea monto con 2 decimales, sin separador de miles."""
    if monto is None:
        return "0.00"
    return f"{monto:.2f}"


def generar_cuo(empresa_id: int, periodo: str, correlativo: int) -> str:
    """
    Genera el Codigo Unico de Operacion (CUO) para un registro PLE.

    El CUO identifica de forma unica cada operacion dentro de los libros
    electronicos. Formato: {empresa_id}-{periodo sin guion}-{correlativo:08d}

    Args:
        empresa_id: ID de la empresa.
        periodo: Periodo en formato YYYY-MM.
        correlativo: Numero correlativo dentro del periodo.

    Returns:
        CUO como cadena.
    """
    periodo_limpio = periodo.replace("-", "")
    return f"{empresa_id}-{periodo_limpio}-{correlativo:08d}"


def _obtener_comprobantes_periodo(
    db: Session,
    empresa_id: int,
    periodo: str,
    es_venta: bool,
) -> list[Comprobante]:
    """
    Obtiene comprobantes de una empresa para un periodo, filtrados por tipo.

    Para ventas: el ruc_emisor coincide con el RUC de la empresa (la empresa emitio).
    Para compras: el ruc_receptor coincide con el RUC de la empresa (la empresa recibio).

    Se excluyen comprobantes con estado DUPLICADO y ANULADO del PLE.
    """
    empresa = db.get(EmpresaCliente, empresa_id)
    if empresa is None:
        raise ValueError(f"Empresa {empresa_id} no encontrada")

    ruc_empresa = empresa.ruc

    # Filtrar por periodo (YYYY-MM)
    # fecha_emision debe estar en el mes del periodo
    anio, mes = periodo.split("-")
    fecha_inicio = date(int(anio), int(mes), 1)
    if int(mes) == 12:
        fecha_fin = date(int(anio) + 1, 1, 1)
    else:
        fecha_fin = date(int(anio), int(mes) + 1, 1)

    # Estados excluidos del PLE
    estados_excluidos = [EstadoComprobante.DUPLICADO]

    if es_venta:
        filtro_ruc = Comprobante.ruc_emisor == ruc_empresa
    else:
        filtro_ruc = Comprobante.ruc_receptor == ruc_empresa

    stmt = (
        select(Comprobante)
        .where(
            and_(
                Comprobante.empresa_id == empresa_id,
                filtro_ruc,
                Comprobante.fecha_emision >= fecha_inicio,
                Comprobante.fecha_emision < fecha_fin,
                Comprobante.estado.not_in(estados_excluidos),
            )
        )
        .order_by(Comprobante.fecha_emision, Comprobante.serie, Comprobante.correlativo)
    )

    return list(db.execute(stmt).scalars().all())


def generar_ple_ventas(
    db: Session,
    empresa_id: int,
    periodo: str,
) -> tuple[str, str]:
    """
    Genera el Registro de Ventas PLE 14.1 para SUNAT.

    Campos del registro de ventas (35 columnas principales):
    1. Periodo
    2. CUO (Codigo Unico de Operacion)
    3. Correlativo del asiento (M1, M2...)
    4. Fecha de emision
    5. Fecha de vencimiento
    6. Tipo de comprobante (tabla 10)
    7. Serie del comprobante
    8. Numero del comprobante
    9. Numero final (consolidados, vacio si no aplica)
    10. Tipo de documento del cliente (tabla 2)
    11. Numero de documento del cliente
    12. Razon social del cliente
    13. Valor facturado de la exportacion (0.00 si no aplica)
    14. Base imponible gravada
    15. Descuento de la base imponible
    16. IGV y/o IPM
    17. Descuento del IGV
    18. Monto de exportacion exonerada
    19. Monto de operacion inafecta
    20. ISC
    21. Base imponible arroz pilado
    22. Impuesto arroz pilado
    23. ICBPER
    24. Otros tributos
    25. Total del comprobante
    26. Tipo de cambio
    27. Fecha de emision del comprobante modificado (NC/ND)
    28. Tipo del comprobante modificado
    29. Serie del comprobante modificado
    30. Numero/CUO del comprobante modificado
    31. Identificador del contrato (vacio)
    32. Error tipo 1 (vacio)
    33. Indicador de pago (1=credito, vacio=contado)
    34. Estado del comprobante (1=vigente, 2=anulado, 9=baja)
    35. Campos libres (vacio)

    Args:
        db: Sesion SQLAlchemy.
        empresa_id: ID de la empresa.
        periodo: Periodo en formato YYYY-MM.

    Returns:
        Tupla (contenido_txt, nombre_archivo).
    """
    empresa = db.get(EmpresaCliente, empresa_id)
    if empresa is None:
        raise ValueError(f"Empresa {empresa_id} no encontrada")

    comprobantes = _obtener_comprobantes_periodo(db, empresa_id, periodo, es_venta=True)

    lineas: list[str] = []
    periodo_ple = periodo.replace("-", "")  # YYYYMM -> YYYYMM00 para PLE
    periodo_campo = periodo_ple + "00"

    for idx, comp in enumerate(comprobantes, start=1):
        cuo = generar_cuo(empresa_id, periodo, idx)
        tipo_doc_cliente = _tipo_documento_identidad(comp.ruc_receptor)

        # Estado SUNAT: 1=vigente, 2=anulado
        if comp.estado == EstadoComprobante.ANULADO:
            estado_sunat = "2"
        else:
            estado_sunat = "1"

        # Datos de comprobante modificado (para NC/ND)
        fecha_mod = ""
        tipo_mod = ""
        serie_mod = ""
        numero_mod = ""
        if comp.tipo in (TipoComprobante.NOTA_CREDITO, TipoComprobante.NOTA_DEBITO):
            if comp.comprobante_referencia_id and comp.comprobante_referencia:
                ref = comp.comprobante_referencia
                fecha_mod = _formato_fecha(ref.fecha_emision)
                tipo_mod = TIPO_COMPROBANTE_SUNAT.get(ref.tipo, "")
                serie_mod = ref.serie
                numero_mod = ref.correlativo

        # Tipo de cambio: 1.000 si PEN, valor real si USD
        tipo_cambio = "1.000" if comp.moneda == "PEN" else ""

        campos = [
            periodo_campo,                                  # 1
            cuo,                                            # 2
            f"M{idx}",                                      # 3
            _formato_fecha(comp.fecha_emision),             # 4
            _formato_fecha(comp.fecha_vencimiento),         # 5
            TIPO_COMPROBANTE_SUNAT.get(comp.tipo, "00"),    # 6
            comp.serie,                                     # 7
            comp.correlativo,                               # 8
            "",                                             # 9  numero final
            tipo_doc_cliente,                               # 10
            comp.ruc_receptor or "",                        # 11
            comp.razon_social_receptor or "",               # 12
            "0.00",                                         # 13 exportacion
            _formato_monto(comp.subtotal),                  # 14 base imponible
            "0.00",                                         # 15 descuento BI
            _formato_monto(comp.igv),                       # 16 IGV
            "0.00",                                         # 17 descuento IGV
            "0.00",                                         # 18 exonerada
            "0.00",                                         # 19 inafecta
            "0.00",                                         # 20 ISC
            "0.00",                                         # 21 BI arroz
            "0.00",                                         # 22 imp arroz
            "0.00",                                         # 23 ICBPER
            "0.00",                                         # 24 otros
            _formato_monto(comp.total),                     # 25 total
            tipo_cambio,                                    # 26
            fecha_mod,                                      # 27
            tipo_mod,                                       # 28
            serie_mod,                                      # 29
            numero_mod,                                     # 30
            "",                                             # 31 contrato
            "",                                             # 32 error
            "",                                             # 33 pago
            estado_sunat,                                   # 34
            "",                                             # 35 libre
        ]

        lineas.append("|".join(campos) + "|")

    contenido = "\r\n".join(lineas)
    nombre_archivo = f"LE{empresa.ruc}{periodo_ple}00140100001111.txt"

    logger.info(
        "PLE Ventas generado: %s (%d registros)",
        nombre_archivo,
        len(comprobantes),
    )

    return contenido, nombre_archivo


def generar_ple_compras(
    db: Session,
    empresa_id: int,
    periodo: str,
) -> tuple[str, str]:
    """
    Genera el Registro de Compras PLE 8.1 para SUNAT.

    Estructura similar a ventas pero con campos adicionales:
    - Aduana/DUA para importaciones
    - Detalle de retencion/percepcion
    - Constancia de deposito de detraccion

    Los campos no aplicables se dejan vacios.

    Args:
        db: Sesion SQLAlchemy.
        empresa_id: ID de la empresa.
        periodo: Periodo en formato YYYY-MM.

    Returns:
        Tupla (contenido_txt, nombre_archivo).
    """
    empresa = db.get(EmpresaCliente, empresa_id)
    if empresa is None:
        raise ValueError(f"Empresa {empresa_id} no encontrada")

    comprobantes = _obtener_comprobantes_periodo(db, empresa_id, periodo, es_venta=False)

    lineas: list[str] = []
    periodo_ple = periodo.replace("-", "")
    periodo_campo = periodo_ple + "00"

    for idx, comp in enumerate(comprobantes, start=1):
        cuo = generar_cuo(empresa_id, periodo, idx)
        tipo_doc_proveedor = _tipo_documento_identidad(comp.ruc_emisor)

        # Estado SUNAT
        if comp.estado == EstadoComprobante.ANULADO:
            estado_sunat = "2"
        else:
            estado_sunat = "1"

        # Datos de comprobante modificado (para NC/ND)
        fecha_mod = ""
        tipo_mod = ""
        serie_mod = ""
        numero_mod = ""
        if comp.tipo in (TipoComprobante.NOTA_CREDITO, TipoComprobante.NOTA_DEBITO):
            if comp.comprobante_referencia_id and comp.comprobante_referencia:
                ref = comp.comprobante_referencia
                fecha_mod = _formato_fecha(ref.fecha_emision)
                tipo_mod = TIPO_COMPROBANTE_SUNAT.get(ref.tipo, "")
                serie_mod = ref.serie
                numero_mod = ref.correlativo

        tipo_cambio = "1.000" if comp.moneda == "PEN" else ""

        # Registro de compras 8.1 - campos principales
        campos = [
            periodo_campo,                                  # 1  Periodo
            cuo,                                            # 2  CUO
            f"M{idx}",                                      # 3  Correlativo
            _formato_fecha(comp.fecha_emision),             # 4  Fecha emision
            _formato_fecha(comp.fecha_vencimiento),         # 5  Fecha vencimiento
            TIPO_COMPROBANTE_SUNAT.get(comp.tipo, "00"),    # 6  Tipo comprobante
            comp.serie,                                     # 7  Serie
            "",                                             # 8  Anio de emision DUA/DSI
            comp.correlativo,                               # 9  Numero comprobante
            "",                                             # 10 Numero final (consolidado)
            tipo_doc_proveedor,                             # 11 Tipo doc proveedor
            comp.ruc_emisor or "",                          # 12 Numero doc proveedor
            comp.razon_social_emisor or "",                 # 13 Razon social proveedor
            _formato_monto(comp.subtotal),                  # 14 Base imponible gravada
            _formato_monto(comp.igv),                       # 15 IGV
            "0.00",                                         # 16 Base imponible gravada (no domiciliado)
            "0.00",                                         # 17 IGV no domiciliado
            "0.00",                                         # 18 Base imponible no gravada
            "0.00",                                         # 19 ISC
            "0.00",                                         # 20 ICBPER
            "0.00",                                         # 21 Otros tributos
            _formato_monto(comp.total),                     # 22 Total
            tipo_cambio,                                    # 23 Tipo de cambio
            fecha_mod,                                      # 24 Fecha comp. modificado
            tipo_mod,                                       # 25 Tipo comp. modificado
            serie_mod,                                      # 26 Serie comp. modificado
            "",                                             # 27 Codigo DUA/DSI
            numero_mod,                                     # 28 Numero comp. modificado
            "",                                             # 29 Fecha deposito detraccion
            "",                                             # 30 Numero constancia detraccion
            "",                                             # 31 Marca retencion
            "",                                             # 32 Clasificacion bienes
            "",                                             # 33 Contrato
            "",                                             # 34 Error tipo 1
            "",                                             # 35 Error tipo 2
            "",                                             # 36 Error tipo 3
            "",                                             # 37 Error tipo 4
            "",                                             # 38 Pago
            estado_sunat,                                   # 39 Estado
        ]

        lineas.append("|".join(campos) + "|")

    contenido = "\r\n".join(lineas)
    nombre_archivo = f"LE{empresa.ruc}{periodo_ple}00080100001111.txt"

    logger.info(
        "PLE Compras generado: %s (%d registros)",
        nombre_archivo,
        len(comprobantes),
    )

    return contenido, nombre_archivo
