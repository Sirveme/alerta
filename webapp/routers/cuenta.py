"""
webapp/routers/cuenta.py — alerta.pe (zAlerta-22 · "Mi Cuenta")
═══════════════════════════════════════════════════════════════════════
"Mi Cuenta": el tablero de tranquilidad del empresario. UNIFICA lo que ya
existía disperso (estado de conexión, conectar/actualizar buzón, recordatorios,
cambiar clave, pago). NO duplica lógica: solo arma el contenedor y calcula la
ACCIÓN dominante contextual al estado. Reusa:
  - estados.estado_conexion (VIGILADO/VERIFICANDO/PENDIENTE/ERROR).
  - /contribuyentes/{id}/cred/validar y /cred/guardar (resumen.py).
  - /contribuyentes/{id}/desconectar (resumen.py).
  - /api/recordatorio (resumen.py, UI en /resumen).
  - /cambiar-clave (auth.py).
  - /pagar + /api/pago/* (pagos.py)  ← antes huérfano; aquí se enlaza.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select

from db import get_session
from models import (
    Contribuyente, CredencialSol, EstudioContable, Pago, EstadoSuscripcion,
    ahora_lima,
)
from ..core import templates, fecha_lima, WHATSAPP_SOPORTE
from ..deps import UsuarioActual, usuario_actual, contribuyente_accesible
from ..estados import estado_conexion
from ..deuda import anio_deuda_desde_default

router = APIRouter(tags=["cuenta"])

# Umbral para "prueba por vencer" (días).
DIAS_AVISO_VENCE = 3
# Límite de documentos del rango elegido antes de avisar "es mucho" (zAlerta-83).
LIMITE_DOCS_AVISO = 150


def _celda_censo(v) -> tuple[int, int, int]:
    """Normaliza una celda de censo_json a (total, con_pdf, pendientes).
    Acepta el formato detallado (dict, zAlerta-85) y el viejo plano (int, z-83)."""
    if isinstance(v, dict):
        return (int(v.get("total", 0)), int(v.get("con_pdf", 0)),
                int(v.get("pendientes", 0)))
    return (int(v or 0), 0, 0)


def _desglose_censo(censo_json) -> tuple[list, int, int, int]:
    """Filas por año (desc) + totales del buzón, desde censo_json (zAlerta-85)."""
    censo = censo_json or {}
    filas, t_tot, t_cp, t_pend = [], 0, 0, 0
    for anio in sorted((a for a in censo if str(a).isdigit()),
                       key=int, reverse=True):
        total, con_pdf, pend = _celda_censo(censo[anio])
        filas.append({"anio": int(anio), "total": total,
                      "con_pdf": con_pdf, "pendientes": pend})
        t_tot += total; t_cp += con_pdf; t_pend += pend
    return filas, t_tot, t_cp, t_pend


def _docs_en_rango(censo_json, y0: int, y1: int) -> int:
    """Total de docs del censo en [y0..y1] (tolera detallado o plano)."""
    tot = 0
    for a, v in (censo_json or {}).items():
        if str(a).isdigit() and y0 <= int(a) <= y1:
            tot += _celda_censo(v)[0]
    return tot


def _accion_dominante(estado_clave: str, estudio) -> dict:
    """UNA acción contextual al estado (zAlerta-22 sección 2). El sistema indica
    qué toca ahora; no se muestran muchos botones de igual peso."""
    if estado_clave == "pendiente":
        return {"tipo": "conectar", "label": "Conectar mi buzón", "href": "#conectar",
                "icono": "link", "clase": "ambar"}
    if estado_clave == "error":
        return {"tipo": "actualizar", "label": "Actualizar mi Clave SOL",
                "href": "#conectar", "icono": "key", "clase": "rojo"}
    if estado_clave == "verificando":
        return {"tipo": "verificando", "label": "Estamos verificando tu conexión…",
                "href": "#conectar", "icono": "hourglass_empty", "clase": "ambar",
                "suave": True}

    # VIGILADO: decide según la suscripción.
    estado_susc = estudio.estado_suscripcion if estudio else ""
    vence = estudio.suscripcion_vence_at if estudio else None
    ahora = ahora_lima()
    dias = (vence - ahora).days if vence else None
    if estado_susc == EstadoSuscripcion.ACTIVA.value and vence and ahora <= vence:
        return {"tipo": "aldia", "label": "Renovar mi plan", "href": "/pagar",
                "icono": "verified", "clase": "verde", "suave": True,
                "nota": "Activo hasta el " + fecha_lima(vence)}
    # Prueba (o vencida / por vencer): empujar a activar el plan.
    nota = None
    if estado_susc == EstadoSuscripcion.PRUEBA.value and vence:
        nota = ("Prueba hasta el " + fecha_lima(vence)
                + (" · vence pronto" if dias is not None and dias <= DIAS_AVISO_VENCE else ""))
    return {"tipo": "pagar", "label": "Activar mi plan · S/5", "href": "/pagar",
            "icono": "bolt", "clase": "verde", "nota": nota}


@router.get("/mi-cuenta", response_class=HTMLResponse)
async def mi_cuenta(request: Request,
                    user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        estudio = await session.get(EstudioContable, user.estudio_id)
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        contribs = list(await session.scalars(
            select(Contribuyente).where(cond)
            .order_by(Contribuyente.razon_social).limit(20)))
        rucs = []
        for ct in contribs:
            cred = await session.scalar(
                select(CredencialSol).where(CredencialSol.contribuyente_id == ct.id))
            cx = estado_conexion(ct, cred)
            # Censo por año: detallado {año:{total,con_pdf,pendientes}} (zAlerta-85)
            # o el viejo plano {año:int} (zAlerta-83). Normalizamos a filas.
            filas_censo, c_total, c_conpdf, c_pend = _desglose_censo(ct.censo_json)
            rucs.append({
                "id": str(ct.id), "ruc": ct.ruc,
                "razon_social": ct.razon_social or ct.ruc,
                "conexion": cx, "usuario_sol": cred.usuario_sol if cred else "",
                "tiene_cred": cred is not None,
                # Filtro de años de deuda por buzón (zAlerta-72).
                "anio_deuda_desde": ct.anio_deuda_desde or anio_deuda_desde_default(),
                # Censo del buzón (zAlerta-83/85): desglose por año sin descargar.
                "censo_total": c_total or None,
                "censo_anios": len(filas_censo) or None,
                "censo_con_pdf": c_conpdf,
                "censo_pendientes": c_pend,
                "censo_filas": filas_censo,
            })
        # Historial de pagos (simple).
        pagos = list(await session.scalars(
            select(Pago).where(Pago.estudio_id == user.estudio_id)
            .order_by(Pago.creado_at.desc()).limit(12)))
        historial = [{
            "fecha": fecha_lima(p.creado_at), "metodo": (p.metodo or "").capitalize(),
            "monto": p.monto or "5.00",
            "vence": fecha_lima(p.vence_resultante) if p.vence_resultante else "—",
        } for p in pagos]

    # Estado y acción "global" (el RUC principal; el empresario tiene uno).
    estado_principal = rucs[0]["conexion"]["clave"] if rucs else "pendiente"
    accion = _accion_dominante(estado_principal, estudio)

    return templates.TemplateResponse(request, "mi_cuenta.html", {
        "user": user,
        "rucs": rucs,
        "accion": accion,
        "estado_principal": estado_principal,
        "ruc_principal": rucs[0] if rucs else None,
        "estudio_estado": estudio.estado_suscripcion if estudio else "",
        "vence": fecha_lima(estudio.suscripcion_vence_at) if estudio and estudio.suscripcion_vence_at else None,
        "historial": historial,
        "whatsapp_soporte": WHATSAPP_SOPORTE,
        "anio_actual": ahora_lima().year,
        "anio_min": 2010,   # SUNAT rara vez tiene deuda vía buzón más antigua
    })


@router.post("/api/buzon/{contribuyente_id}/anio-deuda")
async def set_anio_deuda(contribuyente_id: uuid.UUID, request: Request,
                         anio: int = Form(...),
                         user: UsuarioActual = Depends(usuario_actual)):
    """Fija el año-desde de deuda de un buzón (zAlerta-72). Ampliar (año más
    antiguo que lo cubierto) → FULL dirigido + aviso "trayendo historial".
    Reducir/ampliar dentro de lo cubierto → solo cambia el filtro (sin re-scrapear,
    la deuda vieja se CONSERVA en BD)."""
    ahora = ahora_lima()
    try:
        nuevo = int(anio)
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "Año inválido."}, status_code=400)
    nuevo = max(2010, min(nuevo, ahora.year))   # límites sensatos

    async with get_session() as session:
        contrib = await contribuyente_accesible(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False, "error": "Buzón no encontrado."},
                                status_code=404)
        cubierto = contrib.anio_deuda_cubierto_desde or anio_deuda_desde_default()
        contrib.anio_deuda_desde = nuevo
        ampliado = nuevo < cubierto
        if ampliado:
            # Traer historial faltante: baja el piso cubierto y fuerza un FULL.
            contrib.anio_deuda_cubierto_desde = nuevo
            contrib.ultimo_barrido_full_at = None
        elif contrib.anio_deuda_cubierto_desde is None:
            contrib.anio_deuda_cubierto_desde = cubierto
        # ¿El rango pedido es MUCHO? (zAlerta-83/85) Estima docs [nuevo..actual].
        en_rango = _docs_en_rango(contrib.censo_json, nuevo, ahora.year)
        await session.commit()

    if en_rango > LIMITE_DOCS_AVISO:
        msg = (f"Traer todo desde {nuevo} son ~{en_rango} documentos: es mucho y "
               f"puede tardar. Se traerá por tandas (años recientes primero). "
               f"¿Prefieres reducir a los últimos años?")
        return JSONResponse({"ok": True, "anio": nuevo, "ampliado": ampliado,
                             "mucho": True, "docs_estimados": en_rango, "mensaje": msg})
    if ampliado:
        msg = (f"Estamos trayendo tu historial de deuda desde {nuevo}. "
               f"Puede tardar unos minutos; se irá completando solo.")
    else:
        msg = f"Listo. Ahora ves tu deuda desde {nuevo}."
    return JSONResponse({"ok": True, "anio": nuevo, "ampliado": ampliado,
                         "mensaje": msg})
