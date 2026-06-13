"""APScheduler: dispara verificaciones del buzón según horarios configurados."""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.timezone import LIMA_TZ, lima_now
from app.models import ClienteRuc, ConfiguracionPolling, Suscripcion, Usuario
from app.services.polling_service import verificar_buzon_cliente
from app.services.sunat.session_pool import sunat_pool

logger = logging.getLogger(__name__)

# Scheduler global
scheduler = AsyncIOScheduler(timezone=LIMA_TZ)


def _es_dia_polling(tipo_dias: str, fecha_lima: datetime) -> bool:
    """Determina si hoy toca polling según tipo_dias."""
    if tipo_dias == "diario":
        return True
    if tipo_dias == "interdiario":
        # Lun (0), Mié (2), Vie (4)
        return fecha_lima.weekday() in (0, 2, 4)
    # personalizado: TODO en 13d
    return True


def _horario_actual_minuto() -> str:
    """Devuelve 'HH:MM' en hora Lima del momento actual (truncado al minuto)."""
    ahora = lima_now()
    return ahora.strftime("%H:%M")


async def _verificar_clientes_que_tocan() -> None:
    """Tarea que corre cada minuto: identifica qué clientes RUC toca verificar ahora."""
    horario_objetivo = _horario_actual_minuto()
    ahora_lima = lima_now()

    async with AsyncSessionLocal() as db:
        # Obtener configuraciones activas
        config_result = await db.execute(
            select(ConfiguracionPolling)
            .where(ConfiguracionPolling.activo == True)
            .options(selectinload(ConfiguracionPolling.usuario))
        )
        configs = list(config_result.scalars().all())

        clientes_a_verificar = []

        for config in configs:
            # ¿Toca este horario?
            horarios = config.horarios or []
            if horario_objetivo not in horarios:
                continue

            # ¿Toca este día?
            if not _es_dia_polling(config.tipo_dias, ahora_lima):
                continue

            # Listar clientes activos del contador
            clientes_result = await db.execute(
                select(ClienteRuc)
                .where(ClienteRuc.contador_id == config.usuario_id)
                .where(ClienteRuc.activo == True)
            )
            clientes = list(clientes_result.scalars().all())
            for c in clientes:
                clientes_a_verificar.append(c)

        if not clientes_a_verificar:
            return

        logger.info(
            f"[scheduler] {horario_objetivo} - {len(clientes_a_verificar)} clientes para verificar"
        )

        # Verificar cada uno (con pequeño espaciado para no golpear SUNAT)
        for i, cliente in enumerate(clientes_a_verificar):
            try:
                resultado = await verificar_buzon_cliente(db, cliente)
                if resultado["exito"]:
                    logger.info(
                        f"[scheduler] RUC {cliente.ruc}: "
                        f"{resultado['mensajes_nuevos']} nuevos, reusada={resultado['sesion_reusada']}"
                    )
                    # Si hubo mensajes nuevos, disparar push (Fase 7)
                    if resultado["mensajes_nuevos"] > 0:
                        from app.services.push_service import (
                            notificar_mensajes_nuevos,
                        )
                        await notificar_mensajes_nuevos(
                            db=db,
                            usuario_id=cliente.contador_id,
                            cliente=cliente,
                            cantidad=resultado["mensajes_nuevos"],
                        )
                else:
                    logger.warning(
                        f"[scheduler] RUC {cliente.ruc} ERROR: {resultado['error']}"
                    )
            except Exception:
                logger.exception(f"[scheduler] Excepción verificando RUC {cliente.ruc}")


async def _limpiar_pool() -> None:
    """Cada 10 min: cierra sesiones SUNAT vencidas."""
    try:
        n = sunat_pool.limpiar_vencidas()
        if n > 0:
            logger.info(f"[scheduler] Pool: {n} sesiones vencidas cerradas")
    except Exception:
        logger.exception("[scheduler] Error limpiando pool")


def iniciar_scheduler() -> None:
    """Registra y arranca jobs."""
    if scheduler.running:
        return

    # Job 1: verificar clientes cada minuto (chequea horarios)
    scheduler.add_job(
        _verificar_clientes_que_tocan,
        IntervalTrigger(minutes=1),
        id="verificar_clientes",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )

    # Job 2: limpiar pool cada 10 min
    scheduler.add_job(
        _limpiar_pool,
        IntervalTrigger(minutes=10),
        id="limpiar_pool",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info("[scheduler] APScheduler iniciado")


def detener_scheduler() -> None:
    """Apaga el scheduler limpiamente."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] APScheduler detenido")
