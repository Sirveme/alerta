"""
routers/ws.py — WebSocket para alertas en tiempo real.

El worker Celery publica en Redis channel 'alertas:{empresa_id}'.
Este WebSocket escucha Redis y reenvía al cliente conectado.
El frontend actualiza el badge y reproduce sonido según nivel.

Decisiones técnicas:
- Un WebSocket por empresa_id (no por usuario). Todos los usuarios
  de la misma empresa reciben las mismas alertas.
- Si Redis no está disponible, el WS funciona en modo polling
  (el cliente hace GET /alertas/no-leidas/count cada 30s como fallback).
- No se valida JWT en el WS por simplicidad (se haría en producción
  con un token en query string).
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# Conexiones activas: {empresa_id: set(websocket)}
_connections: dict[int, set[WebSocket]] = {}


@router.websocket("/ws/{empresa_id}")
async def websocket_alertas(websocket: WebSocket, empresa_id: int):
    """
    WebSocket para recibir alertas en tiempo real de una empresa.
    Escucha Redis pub/sub channel 'alertas:{empresa_id}'.
    """
    await websocket.accept()

    # Registrar conexión
    if empresa_id not in _connections:
        _connections[empresa_id] = set()
    _connections[empresa_id].add(websocket)

    logger.info(f"WS conectado: empresa={empresa_id}, total={len(_connections[empresa_id])}")

    # Intentar suscribirse a Redis
    redis_task = None
    try:
        redis_task = asyncio.create_task(
            _escuchar_redis(empresa_id, websocket)
        )
    except Exception:
        logger.debug("Redis no disponible para WS, modo solo-recepción")

    try:
        while True:
            # Mantener conexión abierta. El cliente puede enviar pings.
            data = await websocket.receive_text()
            # Ignorar mensajes del cliente (solo es para keep-alive)
    except WebSocketDisconnect:
        pass
    finally:
        _connections[empresa_id].discard(websocket)
        if not _connections[empresa_id]:
            del _connections[empresa_id]
        if redis_task:
            redis_task.cancel()
        logger.info(f"WS desconectado: empresa={empresa_id}")


async def _escuchar_redis(empresa_id: int, websocket: WebSocket):
    """
    Escucha el channel de Redis y reenvía mensajes al WebSocket.
    Se ejecuta como tarea asyncio mientras el WS está conectado.
    """
    import os

    try:
        import redis.asyncio as aioredis

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = aioredis.from_url(redis_url)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"alertas:{empresa_id}")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    await websocket.send_text(message["data"].decode())
                except Exception:
                    break
    except ImportError:
        logger.debug("redis.asyncio no disponible")
    except Exception as e:
        logger.debug(f"Error en Redis pub/sub: {e}")


async def broadcast_alerta(empresa_id: int, data: dict):
    """
    Envía una alerta a todos los WebSockets conectados de una empresa.
    Llamar desde servicios cuando se crea una alerta sin Redis.
    """
    connections = _connections.get(empresa_id, set())
    dead = set()
    for ws in connections:
        try:
            await ws.send_text(json.dumps(data))
        except Exception:
            dead.add(ws)
    connections -= dead
