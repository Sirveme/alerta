"""
webapp/routers/cliente.py — alerta.pe (zAlerta-97 · Vista del cliente para el contador)
═══════════════════════════════════════════════════════════════════════
La pantalla a la que llega el contador al tocar un cliente en /cartera (z-91):
AÍSLA un RUC y lo muestra como el contador lo piensa — la cadena de cumplimiento
período+tributo (tabla tipo Excel) + una capa VIVA de gestión (instrucciones al
equipo, z-90).

Editorial "informo, no asesoro": se muestran HECHOS por celda (hay coactiva /
deuda notificada S/X / pagado S/Y) SIN saldo neto, sin urgencia calculada, sin
interpretar plazos. Las fechas límite las pone el contador.

Capa 1 de gestión: instrucción (texto + destinatario + fecha límite + estado
PENDIENTE/TERMINADO). Evidencias (z-90 Capa 2) y voz (Capa 3) van después.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from db import get_session
from models import (
    Contribuyente, Notificacion, DocumentoValorado, Adjunto, Persona, Asignacion,
    Instruccion, EstadoInstruccion, TipoDocumento, TipoValorado, ahora_lima,
)
from ..core import templates, fecha_lima
from ..deps import UsuarioActual, usuario_actual, contribuyente_accesible
from ..deuda import monto_de_valorado, fmt_soles
from clasificacion import COACTIVO_NO_SUMA

router = APIRouter(tags=["cliente"])

# Período YYYYMM / YYYY-MM en el asunto (05/2026). El parser de valorados aún no
# puebla DetalleValorado; el asunto es la fuente práctica.
_RE_PERIODO = re.compile(r"\b(20\d{2})[-/\s]?(0[1-9]|1[0-2])\b")


def _periodo_de(texto: str | None) -> str | None:
    m = _RE_PERIODO.search(texto or "")
    return f"{m.group(2)}/{m.group(1)}" if m else None


def _tributo_de(asunto: str | None) -> str:
    a = (asunto or "").lower()
    if "igv" in a:
        return "IGV"
    if "renta" in a:
        return "Renta"
    if "essalud" in a or "es salud" in a:
        return "EsSalud"
    if "onp" in a:
        return "ONP"
    if "itan" in a:
        return "ITAN"
    return "General"


async def _puede_ver(session, user: UsuarioActual, contribuyente_id: uuid.UUID):
    """Acceso (z-89): CONTADOR_DUENO/SUPERVISOR ven todo su estudio; el ASISTENTE
    solo si está asignado a ese RUC. Reusa el scoping multi-tenant existente."""
    contrib = await contribuyente_accesible(session, user, contribuyente_id)
    if not contrib:
        return None
    # Asistente: además debe estar asignado a ese RUC (si hay asignaciones).
    from models import RolUsuario
    if user.rol == RolUsuario.ASISTENTE and user.persona_id:
        asignado = await session.scalar(
            select(Asignacion.id).where(
                Asignacion.contribuyente_id == contribuyente_id,
                Asignacion.persona_asistente_id == user.persona_id))
        if not asignado:
            return None
    return contrib


@router.get("/cliente/{contribuyente_id}", response_class=HTMLResponse)
async def vista_cliente(contribuyente_id: uuid.UUID, request: Request,
                        user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        contrib = await _puede_ver(session, user, contribuyente_id)
        if not contrib:
            return RedirectResponse("/cartera", status_code=303)

        notifs = list(await session.scalars(
            select(Notificacion)
            .options(selectinload(Notificacion.adjuntos))
            .where(Notificacion.contribuyente_id == contribuyente_id)
            .order_by(Notificacion.fecha_publica_sunat.desc().nullslast(),
                      Notificacion.creado_at.desc())))
        vals = {v.notificacion_id: v for v in await session.scalars(
            select(DocumentoValorado).where(
                DocumentoValorado.contribuyente_id == contribuyente_id))}
        # Instrucciones (z-90): general (notif NULL) + por documento.
        instrs = list(await session.scalars(
            select(Instruccion).where(
                Instruccion.contribuyente_id == contribuyente_id)))
        instr_por_notif = {}
        instr_general = []
        for it in instrs:
            if it.notificacion_id:
                instr_por_notif.setdefault(it.notificacion_id, []).append(it)
            else:
                instr_general.append(it)

        # Personas del equipo (destinatarios posibles): las del estudio con acceso.
        # Simplificación Capa 1: los asistentes asignados a este RUC + el propio user.
        asigs = list(await session.execute(
            select(Persona.id, Persona.nombre_completo)
            .join(Asignacion, Asignacion.persona_asistente_id == Persona.id)
            .where(Asignacion.contribuyente_id == contribuyente_id)))
        equipo = [{"id": str(pid), "nombre": nom or "Asistente"} for pid, nom in asigs]

        def _instr_dto(it):
            dest = None
            return {
                "id": str(it.id), "texto": it.texto or "",
                "estado": it.estado.value if hasattr(it.estado, "value") else it.estado,
                "fecha_limite": fecha_lima(it.fecha_limite) if it.fecha_limite else None,
                "destinatario": None,
                "terminado_at": fecha_lima(it.terminado_at) if it.terminado_at else None,
            }

        # ── Armar filas período+tributo con la cadena de cumplimiento ──
        grupos: dict = {}
        for n in notifs:
            tdoc = n.tipo_documento_enum
            per = _periodo_de(n.asunto) or "Sin período"
            trib = _tributo_de(n.asunto)
            clave = (per, trib)
            g = grupos.setdefault(clave, {
                "periodo": per, "tributo": trib, "omiso": False,
                "multas": 0, "ops": 0, "coactivas": 0, "coactiva_sub": None,
                "deuda": 0.0, "pagado": 0.0, "docs": [], "instr_pend": 0, "instr_term": 0,
            })
            v = vals.get(n.id)
            monto = monto_de_valorado(v) if v else None
            if tdoc == TipoDocumento.ESQUELA:
                g["omiso"] = True
            elif tdoc == TipoDocumento.MULTA:
                g["multas"] += 1
            elif tdoc == TipoDocumento.ORDEN_PAGO:
                g["ops"] += 1
            elif tdoc == TipoDocumento.COBRANZA_COACTIVA:
                g["coactivas"] += 1
                g["coactiva_sub"] = g["coactiva_sub"] or n.subtipo_coactivo
            # Deuda notificada (separada) vs pagado (separado) — NUNCA neto.
            # Excluye lo que no es deuda: PAGO, esquela, y coactivas de alivio/cierre
            # (levantamiento/conclusión/reducción — COACTIVO_NO_SUMA), como deuda_estudio.
            _no_suma = (tdoc == TipoDocumento.COBRANZA_COACTIVA
                        and n.subtipo_coactivo in COACTIVO_NO_SUMA)
            if tdoc == TipoDocumento.PAGO:
                if monto:
                    g["pagado"] += monto
            elif monto and not _no_suma and v and v.tipo_valorado not in (
                    TipoValorado.PAGO, TipoValorado.ESQUELA_OMISO):
                g["deuda"] += monto
            # Documentos del grupo (para el despliegue).
            its = instr_por_notif.get(n.id, [])
            for it in its:
                if (it.estado.value if hasattr(it.estado, "value") else it.estado) == "terminado":
                    g["instr_term"] += 1
                else:
                    g["instr_pend"] += 1
            g["docs"].append({
                "id": str(n.id), "tipo": _tipo_legible(tdoc, n.subtipo_coactivo),
                "asunto": n.asunto or "—",
                "fecha": fecha_lima(n.fecha_publica_sunat) if n.fecha_publica_sunat else "—",
                "naturaleza": _naturaleza(tdoc),
                "monto": fmt_soles(monto) if monto else None,
                "valorado_id": str(v.id) if v and v.gcs_key else None,
                "adjuntos": [{"id": str(a.id), "nombre": a.nombre_archivo or "Documento.pdf"}
                             for a in n.adjuntos if a.gcs_key],
                "instrucciones": [_instr_dto(it) for it in its],
            })

        # Orden: períodos con "Sin período" al final; dentro, por período desc.
        filas = sorted(grupos.values(),
                       key=lambda g: (g["periodo"] == "Sin período", _orden_periodo(g["periodo"])),
                       reverse=False)
        for g in filas:
            g["deuda_fmt"] = fmt_soles(g["deuda"]) if g["deuda"] else None
            g["pagado_fmt"] = fmt_soles(g["pagado"]) if g["pagado"] else None

    return templates.TemplateResponse(request, "cliente.html", {
        "user": user,
        "contrib": {"id": str(contrib.id), "ruc": contrib.ruc,
                    "razon_social": contrib.razon_social or contrib.ruc},
        "filas": filas,
        "instr_general": [_instr_dto(it) for it in instr_general],
        "equipo": equipo,
        "total_docs": len(notifs),
    })


def _orden_periodo(per: str):
    m = re.match(r"(\d{2})/(\d{4})", per or "")
    return -(int(m.group(2)) * 100 + int(m.group(1))) if m else 0


def _naturaleza(tdoc) -> str:
    if tdoc in (TipoDocumento.COBRANZA_COACTIVA, TipoDocumento.ORDEN_PAGO,
               TipoDocumento.MULTA, TipoDocumento.RESOLUCION_DETERMINACION,
               TipoDocumento.ESQUELA):
        return "deuda"
    return "info"


def _tipo_legible(tdoc, sub) -> str:
    m = {
        TipoDocumento.COBRANZA_COACTIVA: "Resolución Coactiva",
        TipoDocumento.ORDEN_PAGO: "Orden de Pago",
        TipoDocumento.MULTA: "Resolución de Multa",
        TipoDocumento.RESOLUCION_DETERMINACION: "Resolución de Determinación",
        TipoDocumento.ESQUELA: "Esquela",
        TipoDocumento.FRACCIONAMIENTO: "Fraccionamiento",
        TipoDocumento.PAGO: "Pago",
        TipoDocumento.AVISO: "Aviso",
    }
    return m.get(tdoc, "Documento")


# ─────────────────────────────────────────────────────────────────────
# Capa de gestión (z-90) — Capa 1: crear instrucción + marcar TERMINADO.
# ─────────────────────────────────────────────────────────────────────
@router.post("/api/instruccion")
async def crear_instruccion(request: Request,
                            contribuyente_id: uuid.UUID = Form(...),
                            texto: str = Form(...),
                            notificacion_id: str = Form(""),
                            destinatario_persona_id: str = Form(""),
                            fecha_limite: str = Form(""),
                            user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        contrib = await _puede_ver(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False, "error": "Sin acceso."}, status_code=403)
        fl = None
        if fecha_limite:
            try:
                fl = datetime.fromisoformat(fecha_limite)
            except ValueError:
                fl = None
        it = Instruccion(
            estudio_id=contrib.estudio_id, contribuyente_id=contribuyente_id,
            notificacion_id=uuid.UUID(notificacion_id) if notificacion_id else None,
            autor_persona_id=user.persona_id,
            destinatario_persona_id=(uuid.UUID(destinatario_persona_id)
                                     if destinatario_persona_id else None),
            texto=texto.strip(), fecha_limite=fl,
            estado=EstadoInstruccion.PENDIENTE)
        session.add(it)
        await session.commit()
        return JSONResponse({
            "ok": True, "id": str(it.id), "texto": it.texto,
            "estado": "pendiente",
            "fecha_limite": fecha_lima(fl) if fl else None,
            "notificacion_id": notificacion_id or None})


@router.post("/api/instruccion/{instruccion_id}/terminar")
async def terminar_instruccion(instruccion_id: uuid.UUID,
                               user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        it = await session.get(Instruccion, instruccion_id)
        if not it:
            return JSONResponse({"ok": False, "error": "No existe."}, status_code=404)
        contrib = await _puede_ver(session, user, it.contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False, "error": "Sin acceso."}, status_code=403)
        it.estado = EstadoInstruccion.TERMINADO
        it.terminado_at = ahora_lima()
        it.terminado_por_persona_id = user.persona_id
        await session.commit()
        return JSONResponse({"ok": True, "terminado_at": fecha_lima(it.terminado_at)})
