"""
services/tipo_cambio_service.py — Servicio de tipo de cambio BCRP Peru.

Obtiene y cachea el tipo de cambio diario del Banco Central de Reserva del Peru
(BCRP) para conversion de moneda extranjera en comprobantes.

Fuente de datos:
- API publica BCRP: estadisticas.bcrp.gob.pe
- Serie PD04638PD: tipo de cambio interbancario (compra/venta)
- Formato de respuesta: JSON con periodos y valores

Decisiones tecnicas:
- Cache en tabla TipoCambioHistorico (PK = fecha) para evitar consultas repetidas.
- Si la fecha cae en fin de semana o feriado (BCRP no publica), se retrocede
  al ultimo dia habil anterior. Maximo 7 dias de retroceso.
- Se usa httpx para llamadas HTTP (async-compatible pero usado sync aqui).
- Retry con tenacity: 3 intentos con backoff exponencial para manejar
  caidas temporales de la API del BCRP.
- Validacion de TC en comprobantes: compara el TC declarado por el emisor
  contra el TC oficial con tolerancia configurable (default 2%).
"""

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.contabilidad import TipoCambioHistorico

logger = logging.getLogger(__name__)

# URL base de la API del BCRP
BCRP_API_BASE = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"

# Serie del tipo de cambio interbancario compra/venta
BCRP_SERIE_TC = "PD04638PD"

# Maximo de dias a retroceder para encontrar un dia habil
MAX_DIAS_RETROCESO = 7

# Tolerancia por defecto para validacion de TC (2%)
TOLERANCIA_TC_DEFAULT = Decimal("0.02")

# Timeout para la API del BCRP (segundos)
BCRP_TIMEOUT = 15.0


def _formato_fecha_bcrp(fecha: date) -> str:
    """Formatea una fecha como yyyy-mm-dd para la API del BCRP."""
    return fecha.strftime("%Y-%m-%d")


def _buscar_dia_habil_anterior(fecha: date) -> date:
    """
    Retrocede hasta encontrar un dia de lunes a viernes.
    No contempla feriados nacionales; para eso se depende de que
    la API del BCRP no tenga datos y se retroceda un dia mas.
    """
    actual = fecha
    for _ in range(MAX_DIAS_RETROCESO):
        if actual.weekday() < 5:  # 0=lunes ... 4=viernes
            return actual
        actual -= timedelta(days=1)
    # Si todos los dias son fin de semana (imposible en 7 dias),
    # retornar la fecha original
    return fecha


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _consultar_bcrp(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """
    Consulta la API del BCRP para obtener tipos de cambio en un rango.

    La API responde con JSON:
    {
        "config": {...},
        "periods": [
            {"name": "01.Ene.25", "values": ["3.7530", "3.7590"]},
            ...
        ]
    }

    values[0] = tipo de cambio compra
    values[1] = tipo de cambio venta

    Args:
        fecha_inicio: Fecha de inicio del rango.
        fecha_fin: Fecha de fin del rango.

    Returns:
        Lista de diccionarios con fecha, compra y venta.

    Raises:
        httpx.HTTPError: Si la API falla despues de los reintentos.
        ValueError: Si la respuesta no tiene el formato esperado.
    """
    url = (
        f"{BCRP_API_BASE}/{BCRP_SERIE_TC}/json/"
        f"{_formato_fecha_bcrp(fecha_inicio)}/{_formato_fecha_bcrp(fecha_fin)}/ing"
    )

    logger.debug("Consultando BCRP: %s", url)

    with httpx.Client(timeout=BCRP_TIMEOUT) as client:
        response = client.get(url)
        response.raise_for_status()

    data = response.json()
    periods = data.get("periods", [])

    resultados: list[dict] = []

    # Mapeo de meses BCRP (ingles abreviado) a numero
    meses_bcrp = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    for period in periods:
        name = period.get("name", "")
        values = period.get("values", [])

        if len(values) < 2:
            continue

        # Parsear nombre del periodo: "01.Jan.25" o "01.Ene.25"
        # El formato puede variar segun idioma de la API (ing = ingles)
        try:
            partes = name.split(".")
            dia = int(partes[0])
            mes_str = partes[1]
            anio_corto = int(partes[2])
            anio = 2000 + anio_corto if anio_corto < 100 else anio_corto
            mes = meses_bcrp.get(mes_str, 0)
            if mes == 0:
                logger.warning("Mes no reconocido en respuesta BCRP: %s", mes_str)
                continue
            fecha_periodo = date(anio, mes, dia)
        except (IndexError, ValueError) as e:
            logger.warning("Error parseando periodo BCRP '%s': %s", name, e)
            continue

        # Valores pueden ser "n.d." (no disponible) en feriados
        try:
            compra = Decimal(values[0])
            venta = Decimal(values[1])
        except (InvalidOperation, ValueError):
            logger.debug("TC no disponible para %s: %s", name, values)
            continue

        resultados.append({
            "fecha": fecha_periodo,
            "compra": compra,
            "venta": venta,
        })

    return resultados


def obtener_tc_fecha(db: Session, fecha: date) -> tuple[Decimal, Decimal]:
    """
    Obtiene el tipo de cambio (compra, venta) para una fecha dada.

    Orden de busqueda:
    1. Verificar si existe en la tabla TipoCambioHistorico (cache).
    2. Si no existe, consultar la API del BCRP.
    3. Si la fecha es fin de semana o feriado, retroceder al ultimo dia habil.
    4. Guardar en la tabla y retornar.

    Args:
        db: Sesion SQLAlchemy.
        fecha: Fecha para la cual se necesita el tipo de cambio.

    Returns:
        Tupla (compra, venta) como Decimal.

    Raises:
        ValueError: Si no se puede obtener el tipo de cambio.
    """
    # 1. Buscar en cache
    tc = db.get(TipoCambioHistorico, fecha)
    if tc is not None:
        return tc.compra, tc.venta

    # 2. Ajustar a dia habil si es fin de semana
    fecha_habil = _buscar_dia_habil_anterior(fecha)

    # Si la fecha habil es diferente, verificar cache tambien
    if fecha_habil != fecha:
        tc = db.get(TipoCambioHistorico, fecha_habil)
        if tc is not None:
            return tc.compra, tc.venta

    # 3. Consultar API del BCRP con un rango de dias para cubrir feriados
    fecha_inicio = fecha_habil - timedelta(days=MAX_DIAS_RETROCESO)
    resultados = _consultar_bcrp(fecha_inicio, fecha_habil)

    if not resultados:
        raise ValueError(
            f"No se pudo obtener tipo de cambio para {fecha} "
            f"(ni dias anteriores hasta {fecha_inicio})"
        )

    # Guardar todos los resultados en la tabla
    for r in resultados:
        existente = db.get(TipoCambioHistorico, r["fecha"])
        if existente is None:
            nuevo_tc = TipoCambioHistorico(
                fecha=r["fecha"],
                compra=r["compra"],
                venta=r["venta"],
                fuente="bcrp",
            )
            db.add(nuevo_tc)

    db.flush()

    # Retornar el mas reciente (mas cercano a la fecha solicitada)
    resultados.sort(key=lambda r: r["fecha"], reverse=True)
    mejor = resultados[0]

    logger.info(
        "TC obtenido para %s (habil: %s): compra=%s, venta=%s",
        fecha,
        mejor["fecha"],
        mejor["compra"],
        mejor["venta"],
    )

    return mejor["compra"], mejor["venta"]


def sincronizar_tc_periodo(
    db: Session,
    fecha_inicio: date,
    fecha_fin: date,
) -> int:
    """
    Sincroniza tipos de cambio para un rango de fechas completo.

    Util para carga masiva al configurar una empresa nueva o para
    asegurar que todos los TC del mes esten disponibles antes de
    generar el PLE.

    Args:
        db: Sesion SQLAlchemy.
        fecha_inicio: Fecha de inicio del rango.
        fecha_fin: Fecha de fin del rango (inclusive).

    Returns:
        Cantidad de registros nuevos guardados.
    """
    if fecha_inicio > fecha_fin:
        raise ValueError(
            f"fecha_inicio ({fecha_inicio}) debe ser <= fecha_fin ({fecha_fin})"
        )

    # Consultar el rango completo a la API
    resultados = _consultar_bcrp(fecha_inicio, fecha_fin)

    guardados = 0
    for r in resultados:
        existente = db.get(TipoCambioHistorico, r["fecha"])
        if existente is None:
            nuevo_tc = TipoCambioHistorico(
                fecha=r["fecha"],
                compra=r["compra"],
                venta=r["venta"],
                fuente="bcrp",
            )
            db.add(nuevo_tc)
            guardados += 1

    db.flush()

    logger.info(
        "TC sincronizados para %s a %s: %d nuevos de %d obtenidos",
        fecha_inicio,
        fecha_fin,
        guardados,
        len(resultados),
    )

    return guardados


def validar_tc_comprobante(
    db: Session,
    tc_declarado: Decimal,
    fecha_emision: date,
    tolerancia: Decimal = TOLERANCIA_TC_DEFAULT,
) -> tuple[bool, Decimal]:
    """
    Valida el tipo de cambio declarado en un comprobante contra el TC oficial.

    Compara el TC venta declarado por el emisor con el TC venta del BCRP
    para la fecha de emision. Si la diferencia porcentual excede la tolerancia,
    se marca como invalido.

    Esto permite detectar comprobantes con TC manipulado para inflar o
    deflactar montos en soles.

    Args:
        db: Sesion SQLAlchemy.
        tc_declarado: Tipo de cambio declarado en el comprobante.
        fecha_emision: Fecha de emision del comprobante.
        tolerancia: Tolerancia porcentual (default 0.02 = 2%).

    Returns:
        Tupla (es_valido, tc_oficial_venta).
        es_valido es True si la diferencia esta dentro de la tolerancia.
    """
    try:
        _, tc_venta_oficial = obtener_tc_fecha(db, fecha_emision)
    except ValueError:
        # Si no se puede obtener el TC oficial, no se puede validar.
        # Retornar como valido para no bloquear el flujo.
        logger.warning(
            "No se pudo obtener TC oficial para %s; validacion omitida",
            fecha_emision,
        )
        return True, Decimal("0")

    if tc_venta_oficial == 0:
        return True, tc_venta_oficial

    # Diferencia porcentual: |declarado - oficial| / oficial
    diferencia = abs(tc_declarado - tc_venta_oficial) / tc_venta_oficial

    es_valido = diferencia <= tolerancia

    if not es_valido:
        logger.warning(
            "TC declarado %s difiere del oficial %s en %.2f%% (tolerancia %.2f%%) "
            "para fecha %s",
            tc_declarado,
            tc_venta_oficial,
            float(diferencia * 100),
            float(tolerancia * 100),
            fecha_emision,
        )

    return es_valido, tc_venta_oficial
