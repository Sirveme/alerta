"""
services/correccion_service.py — Servicio de seguimiento de correcciones de comprobantes.

Gestiona el proceso de solicitar a un proveedor que corrija un comprobante errado
(emitir nota de credito + nuevo comprobante correcto). El proceso tiene 4 niveles
de escalamiento con tiempos definidos:

  Nivel 1: Email de solicitud amigable (dia 0)
  Nivel 2: WhatsApp de seguimiento (3 dias sin respuesta)
  Nivel 3: Carta formal via correo certificado (4 dias mas sin respuesta)
  Nivel 4: Bloqueo definitivo del proveedor (8 dias mas sin respuesta)

Decisiones tecnicas:
- El historial de acciones se guarda en JSONB (SeguimientoCorreccion.historial)
  como array de eventos [{fecha, nivel, accion, detalle}].
- La deteccion de proveedor reincidente busca 3+ errores en los ultimos 60 dias
  para el mismo par (ruc_proveedor, empresa_id).
- Los mensajes de escalamiento son templates configurables por nivel.
- No se envian mensajes reales aqui (solo se registra la intencion y se delega
  el envio al sistema de notificaciones).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.comprobantes import Comprobante, EstadoComprobante, TipoComprobante
from app.models.contabilidad import (
    EstadoCorreccion,
    SeguimientoCorreccion,
)
from app.services.alertas_service import crear_alerta_por_tipo

logger = logging.getLogger(__name__)

# Dias de espera antes de escalar a cada nivel.
# nivel_actual -> (dias_espera_para_escalar, canal_notificacion)
ESCALAMIENTO_CONFIG = {
    1: {"dias_espera": 3, "canal": "email", "siguiente_nivel": 2},
    2: {"dias_espera": 4, "canal": "whatsapp", "siguiente_nivel": 3},
    3: {"dias_espera": 8, "canal": "carta_formal", "siguiente_nivel": 4},
    4: {"dias_espera": None, "canal": None, "siguiente_nivel": None},  # Nivel final
}

# Templates de mensajes para cada nivel de escalamiento.
MENSAJES_ESCALAMIENTO = {
    1: {
        "canal": "email",
        "asunto": "Solicitud de correccion de comprobante {serie}-{correlativo}",
        "cuerpo": (
            "Estimado proveedor {nombre_proveedor},\n\n"
            "Hemos detectado errores en el comprobante {tipo} {serie}-{correlativo} "
            "emitido el {fecha_emision} por {moneda} {total}.\n\n"
            "Errores detectados:\n{detalle_errores}\n\n"
            "Le solicitamos emitir la nota de credito correspondiente y el nuevo "
            "comprobante corregido a la brevedad.\n\n"
            "Quedamos atentos a su respuesta.\n"
            "Atentamente,\n{nombre_empresa}"
        ),
    },
    2: {
        "canal": "whatsapp",
        "asunto": "SEGUIMIENTO: Correccion pendiente {serie}-{correlativo}",
        "cuerpo": (
            "Buenos dias {nombre_proveedor}. Le escribimos nuevamente respecto al "
            "comprobante {serie}-{correlativo} del {fecha_emision} por {moneda} {total}. "
            "Han pasado 3 dias desde nuestra solicitud inicial sin recibir respuesta.\n\n"
            "Errores: {detalle_errores}\n\n"
            "SUNAT puede observar este comprobante en fiscalizacion. "
            "Agradecemos su pronta atencion. De no recibir respuesta, "
            "procederemos con la comunicacion formal."
        ),
    },
    3: {
        "canal": "carta_formal",
        "asunto": "CARTA FORMAL: Comprobante errado {serie}-{correlativo} - Ultimo aviso",
        "cuerpo": (
            "Senores {nombre_proveedor} (RUC: {ruc_proveedor}),\n\n"
            "Mediante la presente, le comunicamos formalmente que el comprobante "
            "{serie}-{correlativo} presenta errores que requieren correccion inmediata.\n\n"
            "Errores:\n{detalle_errores}\n\n"
            "De no recibir la nota de credito y comprobante corregido en un plazo "
            "de 8 dias calendario, nos veremos en la necesidad de registrar su "
            "empresa como proveedor bloqueado en nuestro sistema.\n\n"
            "Este comprobante NO sera reconocido en nuestra contabilidad ni como "
            "credito fiscal hasta recibir la Nota de Credito.\n\n"
            "Atentamente,\n{nombre_empresa}\nRUC: {ruc_empresa}"
        ),
    },
    4: {
        "canal": "interno",
        "asunto": "BLOQUEO DEFINITIVO: Proveedor {nombre_proveedor}",
        "cuerpo": (
            "Se ha procedido al bloqueo definitivo del proveedor {nombre_proveedor} "
            "(RUC: {ruc_proveedor}) por no atender la solicitud de correccion del "
            "comprobante {serie}-{correlativo} despues de multiples intentos de "
            "contacto durante {dias_transcurridos} dias.\n\n"
            "El comprobante queda registrado como observado/rechazado y no sera "
            "considerado para credito fiscal."
        ),
    },
}

# Umbral para considerar proveedor reincidente
UMBRAL_REINCIDENCIA = 3
DIAS_VENTANA_REINCIDENCIA = 60


def iniciar_proceso_correccion(
    db: Session, comprobante_id: int
) -> SeguimientoCorreccion:
    """
    Inicia el proceso de correccion para un comprobante errado.

    Crea un registro de SeguimientoCorreccion en nivel 1 y registra el envio
    del email inicial de solicitud.

    Args:
        db: Sesion de SQLAlchemy.
        comprobante_id: ID del comprobante con errores.

    Returns:
        SeguimientoCorreccion creado.

    Raises:
        ValueError: Si el comprobante no existe o ya tiene seguimiento activo.
    """
    # Obtener comprobante
    comprobante = db.execute(
        select(Comprobante).where(
            Comprobante.id == comprobante_id,
            Comprobante.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not comprobante:
        raise ValueError(f"Comprobante {comprobante_id} no encontrado")

    # Verificar que no exista un seguimiento activo para este comprobante
    seguimiento_existente = db.execute(
        select(SeguimientoCorreccion).where(
            SeguimientoCorreccion.comprobante_id == comprobante_id,
            SeguimientoCorreccion.estado.notin_([
                EstadoCorreccion.CORREGIDO,
                EstadoCorreccion.BLOQUEADO_DEFINITIVO,
            ]),
        )
    ).scalar_one_or_none()

    if seguimiento_existente:
        raise ValueError(
            f"Ya existe seguimiento activo #{seguimiento_existente.id} "
            f"para comprobante {comprobante_id}"
        )

    ahora = datetime.now(timezone.utc)

    # Crear seguimiento
    seguimiento = SeguimientoCorreccion(
        comprobante_id=comprobante_id,
        empresa_id=comprobante.empresa_id,
        ruc_proveedor=comprobante.ruc_emisor,
        nombre_proveedor=comprobante.razon_social_emisor,
        nivel_actual=1,
        estado=EstadoCorreccion.CONTACTADO,
        fecha_ultimo_contacto=ahora,
        historial=[
            {
                "fecha": ahora.isoformat(),
                "nivel": 1,
                "accion": "inicio_proceso",
                "canal": "email",
                "detalle": "Proceso de correccion iniciado. Email de solicitud enviado.",
            }
        ],
    )

    db.add(seguimiento)

    # Marcar comprobante como observado
    comprobante.estado = EstadoComprobante.OBSERVADO
    db.add(comprobante)

    db.commit()
    db.refresh(seguimiento)

    # Crear alerta informativa
    crear_alerta_por_tipo(
        db=db,
        empresa_id=comprobante.empresa_id,
        tipo="anomalia_facturacion",
        mensaje=(
            f"Proceso de correccion iniciado para comprobante "
            f"{comprobante.serie}-{comprobante.correlativo} del proveedor "
            f"{comprobante.razon_social_emisor or comprobante.ruc_emisor}"
        ),
        referencia_id=comprobante_id,
        referencia_tabla="comprobantes",
    )

    logger.info(
        "Proceso de correccion iniciado: seguimiento #%d para comprobante %d "
        "(proveedor %s)",
        seguimiento.id, comprobante_id, comprobante.ruc_emisor,
    )

    return seguimiento


def escalar_nivel(db: Session, seguimiento_id: int) -> SeguimientoCorreccion:
    """
    Escala el seguimiento al siguiente nivel de escalamiento.

    Verifica que haya pasado el tiempo minimo de espera del nivel actual
    antes de escalar. Al llegar a nivel 4, el proveedor queda bloqueado.

    Escalamiento:
      Nivel 1 -> 2: despues de 3 dias sin respuesta (WhatsApp)
      Nivel 2 -> 3: despues de 4 dias mas sin respuesta (carta formal)
      Nivel 3 -> 4: despues de 8 dias mas sin respuesta (bloqueo definitivo)

    Args:
        db: Sesion de SQLAlchemy.
        seguimiento_id: ID del seguimiento a escalar.

    Returns:
        SeguimientoCorreccion actualizado.

    Raises:
        ValueError: Si el seguimiento no existe, ya esta en estado final,
                    o no ha pasado suficiente tiempo.
    """
    seguimiento = db.execute(
        select(SeguimientoCorreccion).where(
            SeguimientoCorreccion.id == seguimiento_id,
        )
    ).scalar_one_or_none()

    if not seguimiento:
        raise ValueError(f"Seguimiento {seguimiento_id} no encontrado")

    if seguimiento.estado in (
        EstadoCorreccion.CORREGIDO,
        EstadoCorreccion.BLOQUEADO_DEFINITIVO,
    ):
        raise ValueError(
            f"Seguimiento {seguimiento_id} ya esta en estado final: "
            f"{seguimiento.estado.value}"
        )

    nivel_actual = seguimiento.nivel_actual
    config = ESCALAMIENTO_CONFIG.get(nivel_actual)

    if not config or config["siguiente_nivel"] is None:
        raise ValueError(
            f"Seguimiento {seguimiento_id} ya esta en el nivel maximo ({nivel_actual})"
        )

    # Verificar tiempo minimo de espera
    ahora = datetime.now(timezone.utc)
    if seguimiento.fecha_ultimo_contacto:
        dias_transcurridos = (ahora - seguimiento.fecha_ultimo_contacto).days
        dias_requeridos = config["dias_espera"]

        if dias_transcurridos < dias_requeridos:
            raise ValueError(
                f"No han pasado suficientes dias para escalar. "
                f"Transcurridos: {dias_transcurridos}, requeridos: {dias_requeridos}"
            )

    nuevo_nivel = config["siguiente_nivel"]
    nuevo_config = ESCALAMIENTO_CONFIG[nuevo_nivel]
    nuevo_canal = nuevo_config.get("canal") or "sistema"

    # Actualizar seguimiento
    seguimiento.nivel_actual = nuevo_nivel
    seguimiento.fecha_ultimo_contacto = ahora

    # Determinar nuevo estado
    if nuevo_nivel == 4:
        seguimiento.estado = EstadoCorreccion.BLOQUEADO_DEFINITIVO

        # Alerta de bloqueo definitivo
        crear_alerta_por_tipo(
            db=db,
            empresa_id=seguimiento.empresa_id,
            tipo="anomalia_facturacion",
            mensaje=(
                f"Proveedor {seguimiento.nombre_proveedor or seguimiento.ruc_proveedor} "
                f"BLOQUEADO DEFINITIVAMENTE tras 4 niveles de escalamiento sin respuesta. "
                f"Comprobante #{seguimiento.comprobante_id} no sera considerado como "
                f"credito fiscal."
            ),
            referencia_id=seguimiento.comprobante_id,
            referencia_tabla="comprobantes",
        )
    else:
        seguimiento.estado = EstadoCorreccion.EN_PROCESO

    # Agregar al historial
    historial = list(seguimiento.historial or [])
    historial.append({
        "fecha": ahora.isoformat(),
        "nivel": nuevo_nivel,
        "accion": f"escalamiento_nivel_{nuevo_nivel}",
        "canal": nuevo_canal,
        "detalle": (
            f"Escalado a nivel {nuevo_nivel}. "
            f"Canal: {nuevo_canal}. "
            f"{'PROVEEDOR BLOQUEADO DEFINITIVAMENTE.' if nuevo_nivel == 4 else ''}"
        ).strip(),
    })
    seguimiento.historial = historial

    db.add(seguimiento)
    db.commit()
    db.refresh(seguimiento)

    logger.info(
        "Seguimiento #%d escalado a nivel %d (canal: %s)",
        seguimiento_id, nuevo_nivel, nuevo_canal,
    )

    return seguimiento


def registrar_nc_recibida(
    db: Session, seguimiento_id: int, nc_comprobante_id: int
) -> SeguimientoCorreccion:
    """
    Registra la recepcion de una nota de credito del proveedor, resolviendo
    el proceso de correccion.

    Vincula la NC al seguimiento y cambia el estado a CORREGIDO.

    Args:
        db: Sesion de SQLAlchemy.
        seguimiento_id: ID del seguimiento.
        nc_comprobante_id: ID del comprobante de la nota de credito recibida.

    Returns:
        SeguimientoCorreccion actualizado.

    Raises:
        ValueError: Si el seguimiento o la NC no existen, o la NC no es valida.
    """
    seguimiento = db.execute(
        select(SeguimientoCorreccion).where(
            SeguimientoCorreccion.id == seguimiento_id,
        )
    ).scalar_one_or_none()

    if not seguimiento:
        raise ValueError(f"Seguimiento {seguimiento_id} no encontrado")

    if seguimiento.estado == EstadoCorreccion.CORREGIDO:
        raise ValueError(
            f"Seguimiento {seguimiento_id} ya fue corregido anteriormente"
        )

    # Verificar que la NC existe y es nota de credito
    nc = db.execute(
        select(Comprobante).where(
            Comprobante.id == nc_comprobante_id,
            Comprobante.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not nc:
        raise ValueError(f"Nota de credito {nc_comprobante_id} no encontrada")

    if nc.tipo != TipoComprobante.NOTA_CREDITO:
        raise ValueError(
            f"Comprobante {nc_comprobante_id} no es nota de credito "
            f"(tipo={nc.tipo.value})"
        )

    ahora = datetime.now(timezone.utc)

    # Actualizar seguimiento
    seguimiento.nc_recibida_id = nc_comprobante_id
    seguimiento.estado = EstadoCorreccion.CORREGIDO

    # Agregar al historial
    historial = list(seguimiento.historial or [])
    historial.append({
        "fecha": ahora.isoformat(),
        "nivel": seguimiento.nivel_actual,
        "accion": "nc_recibida",
        "canal": "sistema",
        "detalle": (
            f"Nota de credito recibida: comprobante #{nc_comprobante_id} "
            f"({nc.serie}-{nc.correlativo}). Proceso de correccion finalizado."
        ),
    })
    seguimiento.historial = historial

    db.add(seguimiento)
    db.commit()
    db.refresh(seguimiento)

    # Alerta informativa de resolucion
    crear_alerta_por_tipo(
        db=db,
        empresa_id=seguimiento.empresa_id,
        tipo="comprobante_nuevo",
        mensaje=(
            f"NC recibida de {seguimiento.nombre_proveedor or seguimiento.ruc_proveedor}: "
            f"{nc.serie}-{nc.correlativo}. Proceso de correccion #{seguimiento_id} resuelto."
        ),
        referencia_id=nc_comprobante_id,
        referencia_tabla="comprobantes",
    )

    logger.info(
        "Seguimiento #%d resuelto: NC #%d recibida del proveedor %s",
        seguimiento_id, nc_comprobante_id, seguimiento.ruc_proveedor,
    )

    return seguimiento


def detectar_proveedor_reincidente(
    db: Session, ruc_proveedor: str, empresa_id: int
) -> dict:
    """
    Detecta si un proveedor es reincidente en errores de facturacion.

    Un proveedor se considera reincidente si tiene 3 o mas procesos de correccion
    en los ultimos 60 dias para la misma empresa.

    Args:
        db: Sesion de SQLAlchemy.
        ruc_proveedor: RUC del proveedor a verificar.
        empresa_id: ID de la empresa receptora.

    Returns:
        dict con:
          - es_reincidente: bool
          - total_errores: cantidad de procesos en la ventana
          - errores_recientes: lista resumida de los procesos encontrados
          - recomendacion: texto con la accion sugerida
    """
    fecha_limite = datetime.now(timezone.utc) - timedelta(days=DIAS_VENTANA_REINCIDENCIA)

    seguimientos = db.execute(
        select(SeguimientoCorreccion).where(
            SeguimientoCorreccion.ruc_proveedor == ruc_proveedor,
            SeguimientoCorreccion.empresa_id == empresa_id,
            SeguimientoCorreccion.created_at >= fecha_limite,
        ).order_by(SeguimientoCorreccion.created_at.desc())
    ).scalars().all()

    total_errores = len(seguimientos)
    es_reincidente = total_errores >= UMBRAL_REINCIDENCIA

    errores_recientes = []
    for seg in seguimientos:
        errores_recientes.append({
            "seguimiento_id": seg.id,
            "comprobante_id": seg.comprobante_id,
            "estado": seg.estado.value,
            "nivel_actual": seg.nivel_actual,
            "fecha_inicio": seg.created_at.isoformat() if seg.created_at else None,
        })

    if es_reincidente:
        recomendacion = (
            f"ALERTA: Proveedor {ruc_proveedor} es REINCIDENTE con "
            f"{total_errores} errores en los ultimos {DIAS_VENTANA_REINCIDENCIA} dias. "
            "Se recomienda considerar bloqueo preventivo o solicitar reunion "
            "para establecer controles de calidad en su facturacion."
        )
    elif total_errores > 0:
        recomendacion = (
            f"Proveedor {ruc_proveedor} tiene {total_errores} error(es) recientes. "
            f"Umbral de reincidencia: {UMBRAL_REINCIDENCIA}. Monitorear."
        )
    else:
        recomendacion = f"Proveedor {ruc_proveedor} sin errores recientes."

    resultado = {
        "es_reincidente": es_reincidente,
        "total_errores": total_errores,
        "errores_recientes": errores_recientes,
        "recomendacion": recomendacion,
        "ruc_proveedor": ruc_proveedor,
        "empresa_id": empresa_id,
        "ventana_dias": DIAS_VENTANA_REINCIDENCIA,
        "umbral": UMBRAL_REINCIDENCIA,
    }

    if es_reincidente:
        logger.warning(
            "Proveedor reincidente detectado: %s con %d errores para empresa %d",
            ruc_proveedor, total_errores, empresa_id,
        )

    return resultado
