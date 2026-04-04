"""
workers/cierre_worker.py — Tareas Celery para pre-cierre contable mensual.

Ejecuta verificaciones previas al cierre mensual de una empresa:
1. Validaciones pendientes de comprobantes
2. Pagos sin cruzar (pendiente_cruce)
3. Diferencias entre SIRE declarado y registros internos
4. Asientos contables desbalanceados (debe != haber)
5. Comprobantes bloqueados/observados sin resolver
6. Tipo de cambio faltante para operaciones en moneda extranjera
7. Facturas de exportacion sin DAM (Declaracion Aduanera de Mercancias)

Genera una alerta URGENTE con resumen ejecutivo y notificacion push al contador.

Decisiones tecnicas:
- Se usa @celery_app.task para registro en Celery.
- Cada verificacion es independiente y no bloquea las demas (try/except individual).
- La sesion de BD se obtiene con _get_db_session() (fuera del contexto FastAPI).
- pre_cierre_todas_empresas itera empresas activas con deleted_at IS NULL.
"""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, extract, func, select
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_db_session() -> Session:
    """Obtener sesion de BD para workers (fuera del contexto FastAPI)."""
    from app.core.deps import SessionLocal
    return SessionLocal()


@celery_app.task(
    name="app.workers.cierre_worker.pre_cierre_mensual",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def pre_cierre_mensual(self, empresa_id: int, periodo: str) -> dict:
    """
    Ejecuta verificaciones de pre-cierre mensual para una empresa.

    Revisa cada punto de control y genera un resumen con las observaciones
    encontradas. Si hay observaciones criticas, crea una alerta URGENTE.

    Args:
        empresa_id: ID de la empresa.
        periodo: Periodo en formato 'YYYY-MM'.

    Returns:
        dict con resumen de verificaciones y observaciones.
    """
    db = _get_db_session()
    try:
        return _ejecutar_pre_cierre(db, empresa_id, periodo)
    except Exception as exc:
        logger.exception(
            "Error en pre-cierre empresa %d periodo %s", empresa_id, periodo
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                "Pre-cierre fallido tras reintentos: empresa %d periodo %s",
                empresa_id, periodo,
            )
            return {"error": str(exc), "empresa_id": empresa_id, "periodo": periodo}
    finally:
        db.close()


@celery_app.task(
    name="app.workers.cierre_worker.pre_cierre_todas_empresas",
    bind=True,
)
def pre_cierre_todas_empresas(self) -> dict:
    """
    Ejecuta pre-cierre para todas las empresas activas.

    Determina el periodo automaticamente (mes anterior al actual).
    Lanza una tarea pre_cierre_mensual por cada empresa.

    Returns:
        dict con cantidad de empresas procesadas.
    """
    from app.models.empresas import EmpresaCliente

    db = _get_db_session()
    try:
        # Periodo = mes anterior
        hoy = date.today()
        if hoy.month == 1:
            periodo = f"{hoy.year - 1}-12"
        else:
            periodo = f"{hoy.year}-{hoy.month - 1:02d}"

        empresas = db.execute(
            select(EmpresaCliente.id).where(
                EmpresaCliente.deleted_at.is_(None),
            )
        ).scalars().all()

        logger.info(
            "Iniciando pre-cierre masivo periodo %s para %d empresas",
            periodo, len(empresas),
        )

        for emp_id in empresas:
            # Lanzar tarea asincrona por cada empresa
            pre_cierre_mensual.delay(emp_id, periodo)

        return {
            "periodo": periodo,
            "empresas_encoladas": len(empresas),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        logger.exception("Error al lanzar pre-cierre masivo")
        raise
    finally:
        db.close()


def _ejecutar_pre_cierre(db: Session, empresa_id: int, periodo: str) -> dict:
    """Logica principal de verificacion de pre-cierre."""
    from app.models.comprobantes import Comprobante, EstadoComprobante, TipoComprobante
    from app.models.pagos import Pago, EstadoPago
    from app.models.acumulados import AcumSIRE, TipoRegistroSIRE
    from app.models.contabilidad import (
        AsientoContable,
        EstadoAsiento,
        LineaAsiento,
        TipoCambioHistorico,
    )
    from app.models.empresas import EmpresaCliente
    from app.services.alertas_service import crear_alerta
    from app.models.alertas import OrigenAlerta

    try:
        anio, mes = periodo.split("-")
        anio_int, mes_int = int(anio), int(mes)
    except (ValueError, AttributeError):
        raise ValueError(f"Periodo invalido: {periodo}. Formato esperado: YYYY-MM")

    # Verificar que la empresa existe
    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.id == empresa_id,
            EmpresaCliente.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not empresa:
        raise ValueError(f"Empresa {empresa_id} no encontrada")

    observaciones = []
    resumen = {
        "empresa_id": empresa_id,
        "empresa_ruc": empresa.ruc,
        "empresa_nombre": empresa.razon_social,
        "periodo": periodo,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verificaciones": {},
        "total_observaciones": 0,
        "nivel_general": "ok",
    }

    # ── 1. Comprobantes con validacion pendiente ──────────────────

    try:
        pendientes = db.execute(
            select(func.count(Comprobante.id)).where(
                Comprobante.empresa_id == empresa_id,
                Comprobante.deleted_at.is_(None),
                Comprobante.estado == EstadoComprobante.PENDIENTE,
                extract("year", Comprobante.fecha_emision) == anio_int,
                extract("month", Comprobante.fecha_emision) == mes_int,
            )
        ).scalar() or 0

        resumen["verificaciones"]["validaciones_pendientes"] = pendientes
        if pendientes > 0:
            observaciones.append(
                f"{pendientes} comprobante(s) con validacion pendiente"
            )
    except Exception as e:
        logger.error("Error verificando validaciones pendientes: %s", e)
        resumen["verificaciones"]["validaciones_pendientes"] = "error"

    # ── 2. Pagos sin cruzar ───────────────────────────────────────

    try:
        pagos_sin_cruzar = db.execute(
            select(func.count(Pago.id)).where(
                Pago.empresa_id == empresa_id,
                Pago.deleted_at.is_(None),
                Pago.estado == EstadoPago.PENDIENTE_CRUCE,
                extract("year", Pago.fecha_pago) == anio_int,
                extract("month", Pago.fecha_pago) == mes_int,
            )
        ).scalar() or 0

        resumen["verificaciones"]["pagos_sin_cruzar"] = pagos_sin_cruzar
        if pagos_sin_cruzar > 0:
            observaciones.append(
                f"{pagos_sin_cruzar} pago(s) sin cruzar con comprobante"
            )
    except Exception as e:
        logger.error("Error verificando pagos sin cruzar: %s", e)
        resumen["verificaciones"]["pagos_sin_cruzar"] = "error"

    # ── 3. Diferencias con SIRE ───────────────────────────────────

    try:
        diferencias_sire = _verificar_diferencias_sire(
            db, empresa_id, periodo, anio_int, mes_int
        )
        resumen["verificaciones"]["diferencias_sire"] = diferencias_sire
        if diferencias_sire.get("tiene_diferencia"):
            observaciones.append(
                f"Diferencia SIRE detectada: "
                f"ventas={diferencias_sire.get('dif_ventas', 0)}, "
                f"compras={diferencias_sire.get('dif_compras', 0)}"
            )
    except Exception as e:
        logger.error("Error verificando SIRE: %s", e)
        resumen["verificaciones"]["diferencias_sire"] = "error"

    # ── 4. Asientos desbalanceados ────────────────────────────────

    try:
        asientos_desbalanceados = db.execute(
            select(func.count(AsientoContable.id)).where(
                AsientoContable.empresa_id == empresa_id,
                AsientoContable.periodo == periodo,
                AsientoContable.estado == EstadoAsiento.BORRADOR,
            )
        ).scalar() or 0

        # Verificar balance de asientos aprobados
        asientos_periodo = db.execute(
            select(AsientoContable).where(
                AsientoContable.empresa_id == empresa_id,
                AsientoContable.periodo == periodo,
            )
        ).scalars().all()

        desbalanceados = 0
        for asiento in asientos_periodo:
            total_debe = sum(
                (linea.debe or Decimal("0")) for linea in asiento.lineas
            )
            total_haber = sum(
                (linea.haber or Decimal("0")) for linea in asiento.lineas
            )
            if abs(total_debe - total_haber) > Decimal("0.01"):
                desbalanceados += 1

        resumen["verificaciones"]["asientos_borrador"] = asientos_desbalanceados
        resumen["verificaciones"]["asientos_desbalanceados"] = desbalanceados
        if desbalanceados > 0:
            observaciones.append(
                f"{desbalanceados} asiento(s) con debe != haber"
            )
        if asientos_desbalanceados > 0:
            observaciones.append(
                f"{asientos_desbalanceados} asiento(s) en estado borrador"
            )
    except Exception as e:
        logger.error("Error verificando asientos: %s", e)
        resumen["verificaciones"]["asientos_desbalanceados"] = "error"

    # ── 5. Comprobantes bloqueados/observados ─────────────────────

    try:
        bloqueados = db.execute(
            select(func.count(Comprobante.id)).where(
                Comprobante.empresa_id == empresa_id,
                Comprobante.deleted_at.is_(None),
                Comprobante.estado.in_([
                    EstadoComprobante.OBSERVADO,
                    EstadoComprobante.RECHAZADO_SUNAT,
                ]),
                extract("year", Comprobante.fecha_emision) == anio_int,
                extract("month", Comprobante.fecha_emision) == mes_int,
            )
        ).scalar() or 0

        resumen["verificaciones"]["comprobantes_bloqueados"] = bloqueados
        if bloqueados > 0:
            observaciones.append(
                f"{bloqueados} comprobante(s) observados/rechazados sin resolver"
            )
    except Exception as e:
        logger.error("Error verificando comprobantes bloqueados: %s", e)
        resumen["verificaciones"]["comprobantes_bloqueados"] = "error"

    # ── 6. Tipo de cambio faltante ────────────────────────────────

    try:
        # Buscar comprobantes en moneda extranjera del periodo
        comprobantes_me = db.execute(
            select(Comprobante.fecha_emision).where(
                Comprobante.empresa_id == empresa_id,
                Comprobante.deleted_at.is_(None),
                Comprobante.moneda != "PEN",
                extract("year", Comprobante.fecha_emision) == anio_int,
                extract("month", Comprobante.fecha_emision) == mes_int,
            ).distinct()
        ).scalars().all()

        fechas_sin_tc = []
        for fecha_emision in comprobantes_me:
            tc = db.execute(
                select(TipoCambioHistorico).where(
                    TipoCambioHistorico.fecha == fecha_emision,
                )
            ).scalar_one_or_none()
            if not tc:
                fechas_sin_tc.append(fecha_emision.isoformat())

        resumen["verificaciones"]["tc_faltante"] = len(fechas_sin_tc)
        resumen["verificaciones"]["fechas_sin_tc"] = fechas_sin_tc[:10]  # Max 10
        if fechas_sin_tc:
            observaciones.append(
                f"Tipo de cambio faltante para {len(fechas_sin_tc)} fecha(s)"
            )
    except Exception as e:
        logger.error("Error verificando tipo de cambio: %s", e)
        resumen["verificaciones"]["tc_faltante"] = "error"

    # ── 7. Facturas de exportacion sin DAM ────────────────────────

    try:
        # Facturas en moneda extranjera (proxy de exportacion) sin referencia
        # a una guia de remision o DAM. Se revisa si tienen factura_asociada vacía.
        facturas_export = db.execute(
            select(func.count(Comprobante.id)).where(
                Comprobante.empresa_id == empresa_id,
                Comprobante.deleted_at.is_(None),
                Comprobante.tipo == TipoComprobante.FACTURA,
                Comprobante.moneda != "PEN",
                extract("year", Comprobante.fecha_emision) == anio_int,
                extract("month", Comprobante.fecha_emision) == mes_int,
                # Sin guia de remision asociada (proxy de sin DAM)
                Comprobante.factura_asociada_id.is_(None),
            )
        ).scalar() or 0

        resumen["verificaciones"]["exportaciones_sin_dam"] = facturas_export
        if facturas_export > 0:
            observaciones.append(
                f"{facturas_export} factura(s) de exportacion sin DAM asociado"
            )
    except Exception as e:
        logger.error("Error verificando exportaciones sin DAM: %s", e)
        resumen["verificaciones"]["exportaciones_sin_dam"] = "error"

    # ── Generar alerta con resumen ────────────────────────────────

    resumen["total_observaciones"] = len(observaciones)
    resumen["observaciones"] = observaciones

    if len(observaciones) > 0:
        resumen["nivel_general"] = "urgente" if len(observaciones) >= 3 else "importante"

        # Construir mensaje ejecutivo
        titulo = f"Pre-cierre {periodo}: {len(observaciones)} observacion(es)"
        descripcion_partes = [
            f"Empresa: {empresa.razon_social} (RUC {empresa.ruc})",
            f"Periodo: {periodo}",
            f"Observaciones ({len(observaciones)}):",
        ]
        for i, obs in enumerate(observaciones, 1):
            descripcion_partes.append(f"  {i}. {obs}")

        descripcion = "\n".join(descripcion_partes)

        try:
            crear_alerta(
                db=db,
                empresa_id=empresa_id,
                origen=OrigenAlerta.SISTEMA,
                nivel=resumen["nivel_general"],
                titulo=titulo,
                descripcion=descripcion,
            )
            logger.info(
                "Alerta de pre-cierre creada para empresa %d periodo %s: "
                "%d observaciones",
                empresa_id, periodo, len(observaciones),
            )
        except Exception as e:
            logger.error("Error creando alerta de pre-cierre: %s", e)
    else:
        logger.info(
            "Pre-cierre empresa %d periodo %s: sin observaciones",
            empresa_id, periodo,
        )

    return resumen


def _verificar_diferencias_sire(
    db: Session, empresa_id: int, periodo: str, anio: int, mes: int
) -> dict:
    """
    Compara totales del SIRE declarado vs comprobantes registrados internamente.
    """
    from app.models.comprobantes import Comprobante, EstadoComprobante, TipoComprobante
    from app.models.acumulados import AcumSIRE, TipoRegistroSIRE
    from app.models.empresas import EmpresaCliente

    # Obtener RUC de la empresa
    ruc = db.execute(
        select(EmpresaCliente.ruc).where(EmpresaCliente.id == empresa_id)
    ).scalar()

    # Totales SIRE declarados
    sire_ventas = db.execute(
        select(AcumSIRE).where(
            AcumSIRE.empresa_id == empresa_id,
            AcumSIRE.periodo == periodo,
            AcumSIRE.tipo_registro == TipoRegistroSIRE.VENTAS,
        )
    ).scalar_one_or_none()

    sire_compras = db.execute(
        select(AcumSIRE).where(
            AcumSIRE.empresa_id == empresa_id,
            AcumSIRE.periodo == periodo,
            AcumSIRE.tipo_registro == TipoRegistroSIRE.COMPRAS,
        )
    ).scalar_one_or_none()

    # Totales internos de comprobantes
    total_ventas_interno = db.execute(
        select(func.coalesce(func.sum(Comprobante.total), 0)).where(
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
            Comprobante.ruc_emisor == ruc,
            extract("year", Comprobante.fecha_emision) == anio,
            extract("month", Comprobante.fecha_emision) == mes,
        )
    ).scalar() or Decimal("0")

    total_compras_interno = db.execute(
        select(func.coalesce(func.sum(Comprobante.total), 0)).where(
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
            Comprobante.ruc_receptor == ruc,
            extract("year", Comprobante.fecha_emision) == anio,
            extract("month", Comprobante.fecha_emision) == mes,
        )
    ).scalar() or Decimal("0")

    dif_ventas = Decimal("0")
    dif_compras = Decimal("0")
    tiene_sire = False

    if sire_ventas:
        tiene_sire = True
        dif_ventas = abs(sire_ventas.total - total_ventas_interno)

    if sire_compras:
        tiene_sire = True
        dif_compras = abs(sire_compras.total - total_compras_interno)

    # Tolerancia de S/ 1.00 por redondeo
    tolerancia = Decimal("1.00")
    tiene_diferencia = dif_ventas > tolerancia or dif_compras > tolerancia

    return {
        "tiene_sire": tiene_sire,
        "tiene_diferencia": tiene_diferencia,
        "total_ventas_sire": sire_ventas.total if sire_ventas else None,
        "total_ventas_interno": total_ventas_interno,
        "dif_ventas": dif_ventas,
        "total_compras_sire": sire_compras.total if sire_compras else None,
        "total_compras_interno": total_compras_interno,
        "dif_compras": dif_compras,
    }
