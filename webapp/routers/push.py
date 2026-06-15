"""
webapp/routers/push.py — alerta.pe
═══════════════════════════════════════════════════════════════════════
Suscripción a notificaciones push (zAlerta-01 B.7).

El ENVÍO real es una fase aparte; aquí dejamos LISTO el endpoint de
suscripción. Como el modelo del motor no tiene tabla de suscripciones,
las guardamos en memoria del proceso (suficiente para el MVP) y dejamos
el hook `guardar_suscripcion` para persistir cuando se agregue la tabla.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..deps import UsuarioActual, usuario_actual

router = APIRouter(tags=["push"])

# Almacén temporal en memoria: {usuario_id: [subscription, ...]}
_SUSCRIPCIONES: dict[str, list[dict]] = {}


@router.post("/push/suscribir")
async def suscribir(request: Request,
                    user: UsuarioActual = Depends(usuario_actual)):
    sub = await request.json()
    if not sub or "endpoint" not in sub:
        return JSONResponse({"ok": False, "error": "Suscripción inválida."},
                            status_code=400)
    lista = _SUSCRIPCIONES.setdefault(str(user.id), [])
    if not any(s.get("endpoint") == sub["endpoint"] for s in lista):
        lista.append(sub)
    # HOOK: aquí se persistiría en BD (tabla push_suscripciones) en la fase de envío.
    return JSONResponse({"ok": True})


@router.post("/push/desuscribir")
async def desuscribir(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    sub = await request.json()
    lista = _SUSCRIPCIONES.get(str(user.id), [])
    _SUSCRIPCIONES[str(user.id)] = [
        s for s in lista if s.get("endpoint") != sub.get("endpoint")]
    return JSONResponse({"ok": True})
