"""
webapp/routers/pagos.py — alerta.pe (zAlerta-14)
═══════════════════════════════════════════════════════════════════════
Cobro de la suscripción (S/5 Yape/Plin) validado vía PagoOK.

  - GET  /pagar              → pantalla de pago (instrucción + "Ya pagué").
  - POST /api/pago/buscar    → pagos de S/5 recientes (auto-identificación).
  - POST /api/pago/reclamar  → reclama el pago elegido y ACTIVA la suscripción.

CONSUMO BACKEND-A-BACKEND: la PAGOOK_API_KEY vive solo en el backend (servicios/
pagook_client). El front llama a estos endpoints propios; el backend llama a PagoOK.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from db import get_session
from models import EstudioContable, Pago, EstadoSuscripcion, ahora_lima, TZ_LIMA
from ..core import templates, fecha_lima
from ..deps import UsuarioActual, usuario_actual
from servicios import pagook_client

router = APIRouter(tags=["pagos"])

NUMERO_COBRO = os.getenv("PAGOOK_NUMERO_COBRO", "967317946")
MONTO_SUSCRIPCION = float(os.getenv("MONTO_SUSCRIPCION", "5.00"))
DIAS_SUSCRIPCION = 30
VENTANA_MIN = int(os.getenv("PAGOOK_VENTANA_MIN", "60"))  # minutos hacia atrás


def _hora_corta(valor) -> str:
    """recibido_en → HH:MM:SS hora Lima (best-effort, sin romper)."""
    if not valor:
        return ""
    if isinstance(valor, str):
        try:
            dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except Exception:
            return valor[-8:] if len(valor) >= 8 else valor
    elif isinstance(valor, datetime):
        dt = valor
    else:
        return str(valor)
    try:
        loc = dt.astimezone(TZ_LIMA) if dt.tzinfo else dt
        return loc.strftime("%H:%M:%S")
    except Exception:
        return str(valor)


def _titular_corto(p: dict) -> str:
    tc = p.get("titular_corto") or p.get("titular") or "Pago"
    return str(tc)


@router.get("/pagar", response_class=HTMLResponse)
async def pagar_page(request: Request,
                     user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)
    return templates.TemplateResponse(request, "pagar.html", {
        "user": user,
        "numero_cobro": NUMERO_COBRO,
        "monto": f"{MONTO_SUSCRIPCION:.2f}",
        "vence_actual": fecha_lima(estudio.suscripcion_vence_at) if estudio and estudio.suscripcion_vence_at else None,
        "estado_suscripcion": estudio.estado_suscripcion if estudio else "",
    })


@router.post("/api/pago/buscar")
async def buscar_pago(user: UsuarioActual = Depends(usuario_actual)):
    """Trae los pagos de S/5 de los últimos VENTANA_MIN minutos para que el
    usuario se reconozca por su nombre y la hora de su voucher. Devuelve SOLO
    lo necesario (método, nombre parcial, hora, id para el reclamo)."""
    ahora = ahora_lima()
    res = await pagook_client.listar_pagos(
        monto=MONTO_SUSCRIPCION, desde=ahora - timedelta(minutes=VENTANA_MIN),
        hasta=ahora, limit=50)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error",
                             "No pudimos consultar los pagos ahora.")}, status_code=502)
    pagos = []
    for p in res.get("pagos", []):
        pid = p.get("id")
        if pid is None:
            continue
        pagos.append({
            "id": str(pid),
            "metodo": (p.get("metodo") or "").lower(),
            "titular": _titular_corto(p),
            "hora": _hora_corta(p.get("recibido_en")),
            "monto": f"{MONTO_SUSCRIPCION:.2f}",
        })
    return JSONResponse({"ok": True, "pagos": pagos})


def _calcular_vence(fin_actual, ahora):
    """Regla unificada de vigencia (zAlerta-14 P3), validada con los ejemplos:
      - paga teniendo algo vigente (prueba o mes) → suma 30 días AL FINAL actual.
      - ya vencido todo                            → 30 días desde el pago.
    Ej.: prueba vence 7, paga 3 → vence 37; paga 10 → vence 40; mes vence 37,
    renueva 30 → vence 67.
    """
    if fin_actual and ahora <= fin_actual:
        return fin_actual + timedelta(days=DIAS_SUSCRIPCION)
    return ahora + timedelta(days=DIAS_SUSCRIPCION)


@router.post("/api/pago/reclamar")
async def reclamar_pago(request: Request,
                        user: UsuarioActual = Depends(usuario_actual)):
    data = await request.json()
    pago_id = str(data.get("pago_id") or "").strip()
    if not pago_id:
        return JSONResponse({"ok": False, "error": "Falta el pago a validar."},
                            status_code=400)

    # Re-traer el pago desde PagoOK (no confiar en datos del front): confirma
    # monto S/5 y obtiene los detalles para la trazabilidad.
    ahora = ahora_lima()
    listado = await pagook_client.listar_pagos(
        monto=MONTO_SUSCRIPCION, desde=ahora - timedelta(minutes=VENTANA_MIN * 2),
        hasta=ahora, limit=100)
    detalle = None
    if listado.get("ok"):
        detalle = next((p for p in listado.get("pagos", [])
                        if str(p.get("id")) == pago_id), None)

    async with get_session() as session:
        # Idempotencia local: si ya registramos este pago para este estudio,
        # devolvemos el estado actual (no recobrar ni re-extender).
        ya = await session.scalar(select(Pago).where(Pago.pagook_id == pago_id))
        if ya:
            estudio = await session.get(EstudioContable, user.estudio_id)
            if ya.estudio_id == user.estudio_id:
                return JSONResponse({"ok": True, "ya_activado": True,
                    "vence": fecha_lima(estudio.suscripcion_vence_at)})
            return JSONResponse({"ok": False, "ya_reclamado": True,
                "error": "Ese pago ya fue registrado. Si crees que es un error, escríbenos."},
                status_code=409)

        # Reclamo ATÓMICO en PagoOK (la garantía dura contra doble uso).
        rec = await pagook_client.reclamar_pago(pago_id)
        if not rec.get("ok"):
            if rec.get("ya_reclamado"):
                return JSONResponse({"ok": False, "ya_reclamado": True,
                    "error": "Ese pago ya fue registrado. Si crees que es un error, escríbenos."},
                    status_code=409)
            return JSONResponse({"ok": False,
                "error": "No pudimos validar ahora, reintenta en un momento."},
                status_code=502)

        # Reclamado: activar la suscripción con la regla de vigencia.
        estudio = await session.get(EstudioContable, user.estudio_id)
        nuevo_vence = _calcular_vence(estudio.suscripcion_vence_at, ahora)
        estudio.suscripcion_vence_at = nuevo_vence
        estudio.estado_suscripcion = EstadoSuscripcion.ACTIVA.value
        estudio.fecha_ultimo_pago = ahora

        d = detalle or {}
        session.add(Pago(
            estudio_id=user.estudio_id, pagook_id=pago_id,
            codigo_operacion=str(d.get("codigo_operacion") or "") or None,
            metodo=(d.get("metodo") or "") or None,
            monto=f"{MONTO_SUSCRIPCION:.2f}",
            titular=str(d.get("titular") or d.get("titular_corto") or "") or None,
            recibido_en=str(d.get("recibido_en") or "") or None,
            vence_resultante=nuevo_vence))
        await session.commit()
        vence_txt = fecha_lima(nuevo_vence)

    return JSONResponse({"ok": True, "vence": vence_txt})
