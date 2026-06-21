"""
webapp/routers/pagos.py — alerta.pe (zAlerta-14 + fix zAlerta-15)
═══════════════════════════════════════════════════════════════════════
Cobro de la suscripción (S/5 Yape/Plin) validado vía PagoOK.

  - GET  /pagar              → pantalla de pago (dos flujos: ahora / pagué antes).
  - POST /api/pago/iniciar   → abre "sesión de pago" (marca de tiempo, flujo A).
  - POST /api/pago/buscar    → pagos de S/5 (ventana corta de sesión o amplia 48h).
  - POST /api/pago/reclamar  → reclama el pago elegido y ACTIVA la suscripción.
  - POST /api/pago/cancelar  → limpia la sesión de pago.

FIX zAlerta-15 (zona horaria): PagoOK guarda/filtra en UTC. Aquí calculamos la
ventana desde/hasta en UTC (timezone-aware) y SOLO al mostrar convertimos
recibido_en (UTC) a hora Lima. La key vive solo en el backend (servicios/).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

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
# Ventana corta del flujo A (minutos alrededor del inicio de la sesión de pago).
VENTANA_ANTES_MIN = int(os.getenv("PAGOOK_VENTANA_ANTES_MIN", "2"))
VENTANA_DESPUES_MIN = int(os.getenv("PAGOOK_VENTANA_DESPUES_MIN", "5"))
# Ventana amplia del flujo B (pago tardío), en horas.
VENTANA_TARDIA_HORAS = int(os.getenv("PAGOOK_VENTANA_TARDIA_H", "48"))


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hora_corta(valor) -> str:
    """recibido_en (UTC, naive o con tz) → HH:MM:SS en hora Lima (FIX zAlerta-15)."""
    if not valor:
        return ""
    dt = None
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, str):
        try:
            dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except Exception:
            return valor[-8:] if len(valor) >= 8 else valor
    else:
        return str(valor)
    # PagoOK guarda en UTC; si viene naive, lo tratamos como UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(TZ_LIMA).strftime("%H:%M:%S")
    except Exception:
        return str(valor)


def _titular_corto(p: dict) -> str:
    return str(p.get("titular_corto") or p.get("titular") or "Pago")


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


@router.post("/api/pago/iniciar")
async def iniciar_pago(user: UsuarioActual = Depends(usuario_actual)):
    """Flujo A: marca el inicio de la sesión de pago (UTC) para acotar la
    búsqueda corta a partir de este momento."""
    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)
        if estudio:
            estudio.inicio_sesion_pago = _ahora_utc()
            await session.commit()
    return JSONResponse({"ok": True})


@router.post("/api/pago/cancelar")
async def cancelar_pago(user: UsuarioActual = Depends(usuario_actual)):
    """Limpia la sesión de pago (FIX 3): no dejar marcas viejas que ensucien una
    próxima búsqueda corta."""
    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)
        if estudio and estudio.inicio_sesion_pago is not None:
            estudio.inicio_sesion_pago = None
            await session.commit()
    return JSONResponse({"ok": True})


@router.post("/api/pago/buscar")
async def buscar_pago(request: Request,
                      user: UsuarioActual = Depends(usuario_actual)):
    """Trae los pagos de S/5 para que el usuario se reconozca. Ventana en UTC:
      - flujo 'ahora'  → desde el inicio de la sesión (corta, normalmente 1 pago).
      - flujo 'antes'  → últimas ~48h (autoidentificación entre varios)."""
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    flujo = (data.get("flujo") or "ahora").strip()
    ahora = _ahora_utc()

    if flujo == "antes":
        desde = ahora - timedelta(hours=VENTANA_TARDIA_HORAS)
        hasta = ahora + timedelta(minutes=1)
    else:  # 'ahora' (sesión de pago)
        async with get_session() as session:
            estudio = await session.get(EstudioContable, user.estudio_id)
            inicio = estudio.inicio_sesion_pago if estudio else None
        if inicio is None:
            # Sin sesión (p.ej. recargó): ventana corta de respaldo (últimos 15 min).
            inicio = ahora - timedelta(minutes=15)
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=timezone.utc)
        desde = inicio - timedelta(minutes=VENTANA_ANTES_MIN)
        hasta = ahora + timedelta(minutes=VENTANA_DESPUES_MIN)

    res = await pagook_client.listar_pagos(
        monto=MONTO_SUSCRIPCION, desde=desde, hasta=hasta, limit=50)
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
    si tiene algo vigente, suma 30 días al final actual; si ya venció, 30 desde hoy."""
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

    # Re-traer el pago desde PagoOK (no confiar en el front) en ventana amplia UTC.
    ahora_utc = _ahora_utc()
    listado = await pagook_client.listar_pagos(
        monto=MONTO_SUSCRIPCION,
        desde=ahora_utc - timedelta(hours=VENTANA_TARDIA_HORAS),
        hasta=ahora_utc + timedelta(minutes=1), limit=100)
    detalle = None
    if listado.get("ok"):
        detalle = next((p for p in listado.get("pagos", [])
                        if str(p.get("id")) == pago_id), None)

    ahora = ahora_lima()
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
        estudio.inicio_sesion_pago = None     # FIX 3: cerrar la sesión de pago

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
