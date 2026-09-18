"""
webapp/routers/cartera.py — alerta.pe (zAlerta-91 · Vista de cartera del contador)
═══════════════════════════════════════════════════════════════════════
Pantalla-hub del CONTADOR_DUENO / SUPERVISOR: toda su cartera de clientes
(RUCs de terceros), con conteos por tipo de documento y el asistente
responsable (asignaciones, zAlerta-89). Navega a cada cliente.

Editorial (zAlerta-91): HECHOS, no juicios de urgencia. Se muestran los
conteos crudos por tipo; el color distingue NATURALEZA (rojo=deuda,
azul=pagos/otros), NUNCA prioridad. El contador juzga qué mirar.

Solo LECTURA/navegación. La capa de gestión (instrucciones) va aparte.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func

from db import get_session
from models import (
    Contribuyente, EstudioContable, Notificacion, Asignacion, Persona,
    TipoDocumento,
)
from ..core import templates
from ..deps import UsuarioActual, usuario_actual

router = APIRouter(tags=["cartera"])

# Grupos de la tarjeta (orden fijo del boceto). naturaleza: 'deuda' (rojo) |
# 'info' (azul). Reusa la clasificación ya en BD; NO inventa tipos nuevos.
GRUPOS_CARTERA = [
    ("coactiva", "Resol. Cbza. Coactiva", "deuda"),
    ("orden_pago", "Órdenes de Pago", "deuda"),
    ("multa", "Res. Multa", "deuda"),
    ("pago", "Pagos", "info"),
    ("otros", "Otros", "info"),
]
# tipo_documento_enum → grupo de la tarjeta.
_TIPO_A_GRUPO = {
    TipoDocumento.COBRANZA_COACTIVA: "coactiva",
    TipoDocumento.ORDEN_PAGO: "orden_pago",
    TipoDocumento.MULTA: "multa",
    TipoDocumento.PAGO: "pago",
}


# Etiquetas de estado del contribuyente (para el export; el HTML no lo muestra).
_ESTADO_LABEL = {
    "activo": "Activo", "inactivo": "Inactivo",
    "error_credencial": "Error de clave SOL",
}


async def cartera_datos(session, user: UsuarioActual):
    """Fuente ÚNICA de la cartera: aplica el MISMO scope que la vista y arma la
    lista de clientes. La usan tanto la página `/cartera` como el export a Excel,
    para que el export nunca muestre más de lo que la pantalla muestra.

    Scope (idéntico a la vista): CONTADOR_DUENO/SUPERVISOR ven todo su estudio;
    el ASISTENTE solo sus RUCs con `Asignacion` (z-89); el empresario no llega
    aquí (se redirige antes). SOPORTE_GLOBAL opera dentro del estudio activo.

    Devuelve (estudio, clientes). Cada cliente trae, además de lo que pinta la
    tarjeta, `estado_label` y `urgentes` (usados solo por el export)."""
    from models import RolUsuario, Asignacion, Urgencia
    estudio = await session.get(EstudioContable, user.estudio_id)
    q = (select(Contribuyente.id, Contribuyente.ruc, Contribuyente.razon_social,
                Contribuyente.estado, EstudioContable.whatsapp)
         .outerjoin(EstudioContable,
                    EstudioContable.id == Contribuyente.cuenta_empresario_id)
         .where(Contribuyente.estudio_id == user.estudio_id)
         .order_by(Contribuyente.razon_social))
    # El ASISTENTE ve SOLO sus RUCs asignados (z-89); el contador/supervisor, todo.
    if user.rol == RolUsuario.ASISTENTE and user.persona_id:
        q = q.join(Asignacion, Asignacion.contribuyente_id == Contribuyente.id).where(
            Asignacion.persona_asistente_id == user.persona_id)
    filas = list(await session.execute(q))
    ids = [f[0] for f in filas]

    # Conteos por tipo (una query agrupada) → se agregan a los 5 grupos.
    conteos: dict = {cid: {g[0]: 0 for g in GRUPOS_CARTERA} for cid in ids}
    if ids:
        for cid, tipo, n in await session.execute(
                select(Notificacion.contribuyente_id,
                       Notificacion.tipo_documento_enum, func.count())
                .where(Notificacion.contribuyente_id.in_(ids))
                .group_by(Notificacion.contribuyente_id,
                          Notificacion.tipo_documento_enum)):
            grupo = _TIPO_A_GRUPO.get(tipo, "otros")
            conteos[cid][grupo] += n

    # Urgentes por cliente (transversal: URGENTE + CRÍTICA). Solo para el export.
    urgentes: dict = {cid: 0 for cid in ids}
    if ids:
        for cid, n in await session.execute(
                select(Notificacion.contribuyente_id, func.count())
                .where(Notificacion.contribuyente_id.in_(ids),
                       Notificacion.urgencia.in_([Urgencia.URGENTE, Urgencia.CRITICA]))
                .group_by(Notificacion.contribuyente_id)):
            urgentes[cid] = n

    # Responsable (asistente principal asignado, zAlerta-89). Sin asignación
    # → "sin asignar" (el SUPERVISOR ve todo igual, no necesita figurar).
    resp: dict = {}
    if ids:
        for cid, nombre, wh in await session.execute(
                select(Asignacion.contribuyente_id, Persona.nombre_completo,
                       Persona.whatsapp)
                .join(Persona, Persona.id == Asignacion.persona_asistente_id)
                .where(Asignacion.contribuyente_id.in_(ids),
                       Asignacion.es_principal.is_(True))):
            resp.setdefault(cid, {"nombre": nombre or "Asistente", "whatsapp": wh})

    clientes = []
    for cid, ruc, razon, estado, whatsapp in filas:
        c = conteos[cid]
        grupos = [{"clave": g[0], "label": g[1], "naturaleza": g[2],
                   "n": c[g[0]]} for g in GRUPOS_CARTERA]
        r = resp.get(cid)
        est = estado.value if hasattr(estado, "value") else (estado or "")
        clientes.append({
            "id": str(cid), "ruc": ruc,
            "razon_social": razon or ruc,
            "whatsapp": whatsapp,
            "grupos": grupos, "total": sum(c.values()),
            "responsable": r["nombre"] if r else None,
            "responsable_tel": r["whatsapp"] if r else None,
            # Solo para el export (el HTML los ignora):
            "coactiva": c["coactiva"], "orden_pago": c["orden_pago"],
            "multa": c["multa"], "pago": c["pago"], "otros": c["otros"],
            "urgentes": urgentes.get(cid, 0),
            "estado_label": _ESTADO_LABEL.get(est, est or "—"),
        })
    return estudio, clientes


@router.get("/cartera", response_class=HTMLResponse)
async def cartera(request: Request,
                  user: UsuarioActual = Depends(usuario_actual)):
    # El empresario no tiene cartera (ve su único RUC) → a su cuenta.
    if user.es_empresario:
        return RedirectResponse("/mi-cuenta", status_code=303)

    async with get_session() as session:
        estudio, clientes = await cartera_datos(session, user)

    return templates.TemplateResponse(request, "cartera.html", {
        "user": user,
        "estudio_nombre": (estudio.razon_social if estudio else "Estudio contable"),
        "clientes": clientes,
        "total_clientes": len(clientes),
    })
