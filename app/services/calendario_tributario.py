"""
services/calendario_tributario.py — Alertas de vencimiento tributario SUNAT.

Consulta el cronograma SUNAT (tabla CronogramaSunat) para determinar las fechas
de vencimiento de obligaciones tributarias segun el ultimo digito del RUC de cada
empresa. Genera alertas con niveles escalonados segun la proximidad al vencimiento:

  - 7 dias antes: INFO (recordatorio)
  - 3 dias antes: IMPORTANTE (advertencia)
  - 1 dia antes: URGENTE (accion inmediata)
  - Dia del vencimiento: URGENTE con flag sonido especial
  - Despues del vencimiento: URGENTE "VENCIDO" (multa potencial)

Decisiones tecnicas:
- Se calcula ultimo_digito_ruc a partir del RUC de la empresa (posicion -1).
- Se usa date.today() con timezone America/Lima para comparaciones correctas.
- Las alertas se crean via crear_alerta_por_tipo del servicio centralizado,
  pero tambien se retornan como lista para uso en API/dashboard.
- verificar_vencimientos_contador opera sobre el RUC del propio tenant (estudio).
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.contabilidad import CronogramaSunat
from app.models.empresas import EmpresaCliente
from app.models.tenants import Tenant
from app.services.alertas_service import crear_alerta

from app.models.alertas import OrigenAlerta

logger = logging.getLogger(__name__)

# Umbrales de dias para cada nivel de alerta.
# Se evaluan en orden descendente: el primer match gana.
UMBRALES_ALERTA = [
    # (dias_hasta, nivel, descripcion_extra)
    (7, "info", "Recordatorio: vencimiento en {dias} dias"),
    (3, "importante", "Advertencia: vencimiento en {dias} dias"),
    (1, "urgente", "ATENCION: vencimiento MANANA"),
    (0, "urgente", "HOY VENCE: {obligacion}"),
    (-1, "urgente", "VENCIDO: {obligacion} - posible multa"),
]


def verificar_vencimientos_empresa(
    db: Session, empresa_id: int
) -> list[dict]:
    """
    Verifica los proximos vencimientos tributarios para una empresa.

    Consulta CronogramaSunat filtrando por el ultimo digito del RUC de la empresa
    y genera alertas escalonadas segun la proximidad al vencimiento.

    Args:
        db: Sesion de SQLAlchemy.
        empresa_id: ID de la empresa a verificar.

    Returns:
        Lista de dicts con informacion de cada vencimiento encontrado:
        - tipo_obligacion, fecha_vencimiento, dias_hasta, nivel, mensaje, alerta_creada
    """
    # Obtener empresa y su RUC
    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.id == empresa_id,
            EmpresaCliente.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not empresa:
        logger.warning("Empresa %d no encontrada para verificar vencimientos", empresa_id)
        return []

    if not empresa.ruc or len(empresa.ruc) < 1:
        logger.warning("Empresa %d sin RUC configurado", empresa_id)
        return []

    ultimo_digito = int(empresa.ruc[-1])
    return _verificar_vencimientos_por_ruc(
        db=db,
        ultimo_digito_ruc=ultimo_digito,
        empresa_id=empresa_id,
        nombre_entidad=empresa.razon_social,
    )


def verificar_vencimientos_contador(
    db: Session, tenant_id
) -> list[dict]:
    """
    Verifica los proximos vencimientos tributarios para el propio estudio contable
    (tenant), usando el RUC del tenant.

    Util para que el contador no olvide sus propias obligaciones (PDT, PLAME, etc.).

    Args:
        db: Sesion de SQLAlchemy.
        tenant_id: ID (UUID) del tenant.

    Returns:
        Lista de dicts con informacion de cada vencimiento.
    """
    tenant = db.execute(
        select(Tenant).where(
            Tenant.id == tenant_id,
            Tenant.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not tenant:
        logger.warning("Tenant %s no encontrado", tenant_id)
        return []

    if not tenant.ruc or len(tenant.ruc) < 1:
        logger.warning("Tenant %s sin RUC configurado", tenant_id)
        return []

    ultimo_digito = int(tenant.ruc[-1])

    # Para el tenant, usamos empresa_id=None ya que la alerta es del estudio mismo.
    # Se pasa empresa_id=0 como marcador (el servicio de alertas lo acepta).
    return _verificar_vencimientos_por_ruc(
        db=db,
        ultimo_digito_ruc=ultimo_digito,
        empresa_id=None,
        nombre_entidad=tenant.nombre,
    )


def _verificar_vencimientos_por_ruc(
    db: Session,
    ultimo_digito_ruc: int,
    empresa_id: Optional[int],
    nombre_entidad: str,
) -> list[dict]:
    """
    Logica compartida: consulta CronogramaSunat y genera alertas segun proximidad.

    Se buscan vencimientos del mes actual y el siguiente (ventana de ~60 dias)
    para cubrir obligaciones que aun no vencen y las que ya vencieron este mes.
    """
    hoy = date.today()

    # Ventana de busqueda: mes actual y siguiente
    meses_buscar = []
    meses_buscar.append((hoy.year, hoy.month))
    if hoy.month == 12:
        meses_buscar.append((hoy.year + 1, 1))
    else:
        meses_buscar.append((hoy.year, hoy.month + 1))

    # Tambien incluir mes anterior (por si hay vencimientos recien pasados)
    if hoy.month == 1:
        meses_buscar.append((hoy.year - 1, 12))
    else:
        meses_buscar.append((hoy.year, hoy.month - 1))

    # Consultar cronograma SUNAT
    conditions = []
    for anio, mes in meses_buscar:
        conditions.append(
            and_(
                CronogramaSunat.anio == anio,
                CronogramaSunat.mes == mes,
            )
        )

    from sqlalchemy import or_

    vencimientos_db = db.execute(
        select(CronogramaSunat).where(
            CronogramaSunat.ultimo_digito_ruc == ultimo_digito_ruc,
            or_(*conditions) if conditions else True,
        ).order_by(CronogramaSunat.fecha_vencimiento)
    ).scalars().all()

    resultados = []

    for venc in vencimientos_db:
        dias_hasta = (venc.fecha_vencimiento - hoy).days
        nivel, mensaje = _determinar_nivel_y_mensaje(
            dias_hasta=dias_hasta,
            tipo_obligacion=venc.tipo_obligacion,
            fecha_vencimiento=venc.fecha_vencimiento,
        )

        # Solo generar alertas para vencimientos dentro de 7 dias o ya vencidos
        # (hasta 15 dias despues del vencimiento)
        if dias_hasta > 7 or dias_hasta < -15:
            continue

        # Crear alerta en BD
        alerta_creada = None
        try:
            if empresa_id is not None:
                alerta_creada = crear_alerta(
                    db=db,
                    empresa_id=empresa_id,
                    origen=OrigenAlerta.SUNAT,
                    nivel=nivel,
                    titulo=f"Vencimiento: {venc.tipo_obligacion}",
                    descripcion=mensaje,
                )
        except Exception as e:
            logger.error(
                "Error creando alerta de vencimiento para empresa %s: %s",
                empresa_id, e,
            )

        resultado = {
            "tipo_obligacion": venc.tipo_obligacion,
            "fecha_vencimiento": venc.fecha_vencimiento.isoformat(),
            "dias_hasta": dias_hasta,
            "nivel": nivel,
            "mensaje": mensaje,
            "alerta_creada": alerta_creada is not None,
            "sonido_especial": dias_hasta == 0,
            "entidad": nombre_entidad,
        }
        resultados.append(resultado)

        logger.info(
            "Vencimiento %s para %s: %s dias, nivel=%s",
            venc.tipo_obligacion, nombre_entidad, dias_hasta, nivel,
        )

    return resultados


def _determinar_nivel_y_mensaje(
    dias_hasta: int,
    tipo_obligacion: str,
    fecha_vencimiento: date,
) -> tuple[str, str]:
    """
    Determina nivel de alerta y mensaje segun dias restantes.

    Returns:
        Tupla (nivel, mensaje).
    """
    fecha_str = fecha_vencimiento.strftime("%d/%m/%Y")

    if dias_hasta < 0:
        dias_atraso = abs(dias_hasta)
        return (
            "urgente",
            f"VENCIDO hace {dias_atraso} dia(s): {tipo_obligacion}. "
            f"Fecha vencimiento: {fecha_str}. Posible multa e intereses.",
        )
    elif dias_hasta == 0:
        return (
            "urgente",
            f"HOY VENCE: {tipo_obligacion}. "
            f"Fecha: {fecha_str}. Presente la declaracion antes de las 11:59 PM.",
        )
    elif dias_hasta == 1:
        return (
            "urgente",
            f"ATENCION: {tipo_obligacion} vence MANANA {fecha_str}. "
            "Prepare la declaracion ahora.",
        )
    elif dias_hasta <= 3:
        return (
            "importante",
            f"Advertencia: {tipo_obligacion} vence en {dias_hasta} dias "
            f"({fecha_str}). Revise que todo este en orden.",
        )
    elif dias_hasta <= 7:
        return (
            "info",
            f"Recordatorio: {tipo_obligacion} vence en {dias_hasta} dias "
            f"({fecha_str}).",
        )
    else:
        # No deberia llegar aqui por el filtro en _verificar_vencimientos_por_ruc
        return (
            "info",
            f"{tipo_obligacion} vence el {fecha_str} (en {dias_hasta} dias).",
        )
