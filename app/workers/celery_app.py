"""
workers/celery_app.py — Configuración de Celery con Redis como broker y backend.

Decisiones técnicas:
- Redis como broker Y backend de resultados: simplicidad operacional.
  Un solo servicio para colas + cache + pub/sub de alertas.
- Beat scheduler: polling IMAP cada 60s, recruce de pagos cada hora.
- Prefetch multiplier = 1: procesar un correo a la vez por worker para
  evitar problemas de memoria con adjuntos grandes (PDFs, imágenes).
- Serialización JSON (no pickle): seguridad y depurabilidad.
- Task routes: separar colas para email (IO-bound) y parseo (CPU-bound).
"""

import os
from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "alertape",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    # Serialización segura
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="America/Lima",
    enable_utc=True,

    # Un mensaje a la vez por worker (adjuntos pueden ser pesados)
    worker_prefetch_multiplier=1,

    # Resultados expiran en 24h
    result_expires=86400,

    # Auto-discover tasks en app.workers
    include=[
        "app.workers.email_worker",
        "app.workers.cierre_worker",
        "app.workers.portal_worker",
    ],

    # Rutas de colas
    task_routes={
        "app.workers.email_worker.poll_imap_*": {"queue": "email"},
        "app.workers.email_worker.procesar_*": {"queue": "parsing"},
        "app.workers.email_worker.tarea_*": {"queue": "parsing"},
        "app.workers.cierre_worker.*": {"queue": "parsing"},
        "app.workers.portal_worker.*": {"queue": "parsing"},
    },

    # Beat schedule: tareas periódicas
    beat_schedule={
        # Polling IMAP cada 60 segundos
        "poll-ventas-60s": {
            "task": "app.workers.email_worker.poll_imap_ventas",
            "schedule": 60.0,
        },
        "poll-compras-60s": {
            "task": "app.workers.email_worker.poll_imap_compras",
            "schedule": 60.0,
        },
        # Recalcular cruces pendientes cada hora
        "recruce-pendientes-1h": {
            "task": "app.workers.email_worker.recalcular_cruces",
            "schedule": 3600.0,
        },
        # Estado del sistema cada 5 minutos (portal reenviame.pe)
        "estado-sistema-5min": {
            "task": "app.workers.portal_worker.actualizar_estado_sistema",
            "schedule": 300.0,
        },
        # Pre-cierre mensual: dia 25 de cada mes a las 6:00 AM Lima
        "pre-cierre-mensual": {
            "task": "app.workers.cierre_worker.pre_cierre_todas_empresas",
            "schedule": crontab(hour=6, minute=0, day_of_month=25),
        },
        # RendiPe: verificar rendiciones vencidas diariamente a las 08:00 Lima
        "rendipe-vencimientos-diario": {
            "task": "app.services.rendipe_service.verificar_vencimiento_rendiciones_task",
            "schedule": crontab(hour=8, minute=0),
        },
    },
)
