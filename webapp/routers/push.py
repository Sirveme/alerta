"""
webapp/routers/push.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Suscripción a Web Push (zAlerta-07 C).

Persiste las suscripciones en la tabla push_suscripciones (una por
dispositivo/navegador). El ENVÍO lo hace el worker vía push_service.
Multi-tenant: cada suscripción cuelga del estudio del usuario.
"""

from __future__ import annotations

import os
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

import asyncio

from db import get_session
from models import PushSuscripcion, Usuario, ahora_lima
from ..deps import UsuarioActual, usuario_actual, usuario_actual_opcional
import push_service

router = APIRouter(tags=["push"])

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
# Token opcional para el push de prueba sin sesión (además de SOPORTE_GLOBAL).
PUSH_TEST_TOKEN = os.getenv("PUSH_TEST_TOKEN", "")


@router.get("/push/clave-publica")
async def clave_publica():
    """Clave pública VAPID para que el navegador cree la suscripción."""
    return JSONResponse({"public_key": VAPID_PUBLIC_KEY})


@router.get("/admin/push-test")
async def admin_push_test(
        request: Request, dni: str = "05393776", token: str = "",
        user: "UsuarioActual | None" = Depends(usuario_actual_opcional)):
    """Herramienta de diagnóstico (zAlerta-66): dispara un push de prueba DESDE
    PRODUCCIÓN (Railway, red limpia a FCM) a las suscripciones de UNA persona,
    por el MISMO camino que el push agrupado real (`_enviar_webpush_sync`, con
    Urgency: high). Devuelve la hora exacta de envío + status por suscripción,
    para medir la latencia real FCM→dispositivo.

    Protección: SOLO SOPORTE_GLOBAL (Duilio) o con ?token=<PUSH_TEST_TOKEN>.
    Nunca abierto al público. Envía SOLO a las suscripciones del DNI indicado."""
    autorizado = (user is not None and user.es_soporte_global) or (
        bool(PUSH_TEST_TOKEN) and token == PUSH_TEST_TOKEN)
    if not autorizado:
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    if not push_service._vapid_private_key():
        return JSONResponse({"ok": False, "error": "VAPID no configurada en este servicio."},
                            status_code=500)

    hora = ahora_lima()
    hhmmss = hora.strftime("%H:%M:%S")
    payload = json.dumps({
        "title": f"⏱ Prueba alerta.pe {hhmmss}",
        "body": f"Enviado {hhmmss} (hora Lima) desde producción. ¿A qué hora llegó?",
        "url": "/resumen?from=push",
        "acciones": True,
        "tag": "alertape-buzon",   # mismo tag/prioridad que el push real
        "requiere": True,
    })

    async with get_session() as session:
        uids = [str(u) for u in (await session.scalars(
            select(Usuario.id).where(Usuario.dni == dni)))]
        if not uids:
            return JSONResponse(
                {"ok": False, "error": f"Sin usuario con dni={dni}."}, status_code=404)
        subs = list(await session.scalars(
            select(PushSuscripcion).where(
                PushSuscripcion.usuario_id.in_(uids),
                PushSuscripcion.activa.is_(True))))
        resultados = []
        for s in subs:
            status = await asyncio.to_thread(
                push_service._enviar_webpush_sync, s, payload)
            muerta = status in (400, 404, 410)
            if muerta:
                s.activa = False
            resultados.append({"sub": str(s.id)[:8],
                               "status": status or 201,
                               "estado": "muerta" if muerta else "aceptada FCM"})
        await session.commit()

    return JSONResponse({
        "ok": True,
        "dni": dni,
        "enviado_hora_lima": hhmmss,
        "enviado_iso": hora.isoformat(),
        "suscripciones": len(subs),
        "resultados": resultados,
        "nota": "El '201/aceptada' es que FCM la recibió. Anota la hora en que "
                "aparece en el celular; la diferencia con enviado_hora_lima es la "
                "latencia real FCM→dispositivo desde producción.",
    })


@router.post("/push/suscribir")
async def suscribir(request: Request,
                    user: UsuarioActual = Depends(usuario_actual)):
    sub = await request.json()
    endpoint = (sub or {}).get("endpoint")
    keys = (sub or {}).get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return JSONResponse({"ok": False, "error": "Suscripción inválida."},
                            status_code=400)

    async with get_session() as session:
        existente = await session.scalar(
            select(PushSuscripcion).where(
                PushSuscripcion.usuario_id == user.id,
                PushSuscripcion.endpoint == endpoint))
        if existente:
            # Reactivar/actualizar claves (pueden rotar al re-suscribirse).
            existente.p256dh = p256dh
            existente.auth = auth
            existente.activa = True
        else:
            # Personas sin fila en `usuarios` (acceso institucional) no guardan
            # suscripción push (FK usuario_id). No-op amable.
            if not user.tiene_usuario:
                return JSONResponse({"ok": True, "skip": True})
            session.add(PushSuscripcion(
                estudio_id=user.estudio_id, usuario_id=user.id,
                endpoint=endpoint, p256dh=p256dh, auth=auth, activa=True))
        await session.commit()
    return JSONResponse({"ok": True})


@router.post("/push/desuscribir")
async def desuscribir(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    sub = await request.json()
    endpoint = (sub or {}).get("endpoint")
    if not endpoint:
        return JSONResponse({"ok": True})
    async with get_session() as session:
        existente = await session.scalar(
            select(PushSuscripcion).where(
                PushSuscripcion.usuario_id == user.id,
                PushSuscripcion.endpoint == endpoint))
        if existente:
            existente.activa = False
            await session.commit()
    return JSONResponse({"ok": True})
