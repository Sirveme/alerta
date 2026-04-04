"""
core/rate_limit.py — Rate limiting para el portal público.

Usa slowapi (basado en limits) con Redis como backend.
Límites:
  - 20 envíos/hora por IP
  - 5 envíos/hora por RUC emisor
  - 100 consultas /verificar por hora por IP
  - 30 consultas /api/ruc por hora por IP

Decisión: Redis ya existe para Celery, no agrega infra.
Si Redis no está disponible, fallback a in-memory (dev).
"""

import os
from slowapi import Limiter
from slowapi.util import get_remote_address

# Usar Redis si disponible, sino memoria
REDIS_URL = os.environ.get("REDIS_URL", None)

if REDIS_URL:
    storage_uri = REDIS_URL
else:
    storage_uri = "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=storage_uri,
    default_limits=["200/hour"],
)
