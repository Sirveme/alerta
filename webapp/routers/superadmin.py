"""
webapp/routers/superadmin.py — alerta.pe (zAlerta-06 C.4)
═══════════════════════════════════════════════════════════════════════
Panel mínimo de SOPORTE (Perú Sistemas Pro) para el flujo MANUAL de claves
del empresario, mientras no haya WhatsApp API oficial.

- Listar cuentas-empresario con clave pendiente (sin exponer ninguna clave).
- Asignar una clave (Argon2) a un empresario para entregársela por WhatsApp.

Protección: token secreto en env SUPER_ADMIN_TOKEN (header X-Super-Admin-Token
o query ?token=). Si no está definido, TODO se deniega (no hay default
inseguro). Dejado PREPARADO para automatizar con bot más adelante.
"""

from __future__ import annotations

import hmac
import os
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from db import get_session
from models import Usuario, EstudioContable, TipoCuenta
from ..auth import hash_clave

router = APIRouter(tags=["superadmin"])


def _autorizado(request: Request) -> bool:
    esperado = os.getenv("SUPER_ADMIN_TOKEN")
    if not esperado:
        return False  # sin token configurado: denegar todo
    dado = (request.headers.get("x-super-admin-token")
            or request.query_params.get("token") or "")
    return hmac.compare_digest(dado, esperado)


@router.get("/superadmin/empresarios-pendientes")
async def empresarios_pendientes(request: Request):
    if not _autorizado(request):
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    async with get_session() as session:
        filas = (await session.execute(
            select(Usuario.id, Usuario.nombre, Usuario.whatsapp,
                   Usuario.estudio_id, Usuario.creado_at)
            .where(Usuario.clave_pendiente.is_(True))
            .order_by(Usuario.creado_at))).all()
    return JSONResponse({"ok": True, "pendientes": [
        {"usuario_id": str(f.id), "nombre": f.nombre, "whatsapp": f.whatsapp,
         "estudio_id": str(f.estudio_id)}
        for f in filas]})


@router.post("/superadmin/empresarios/{usuario_id}/clave")
async def asignar_clave(usuario_id: uuid.UUID, request: Request):
    if not _autorizado(request):
        return JSONResponse({"ok": False, "error": "No autorizado."}, status_code=403)
    data = await request.json()
    clave = (data.get("clave") or "").strip()
    if len(clave) < 6:
        return JSONResponse(
            {"ok": False, "error": "La clave debe tener al menos 6 caracteres."},
            status_code=400)

    async with get_session() as session:
        usuario = await session.get(Usuario, usuario_id)
        if not usuario:
            return JSONResponse({"ok": False, "error": "Usuario no encontrado."}, status_code=404)
        # Verificar que sea una cuenta empresario (no tocar usuarios de estudio).
        tipo = await session.scalar(
            select(EstudioContable.tipo_cuenta).where(
                EstudioContable.id == usuario.estudio_id))
        if tipo != TipoCuenta.EMPRESARIO.value:
            return JSONResponse({"ok": False, "error": "No es una cuenta de empresario."}, status_code=400)

        usuario.access_code = hash_clave(clave)
        usuario.clave_pendiente = False
        usuario.debe_cambiar_clave = True   # que la cambie en el primer ingreso
        await session.commit()
    # No devolvemos la clave (se entrega por el canal de Soporte).
    return JSONResponse({"ok": True, "mensaje": "Clave asignada. Entrégala por WhatsApp."})
