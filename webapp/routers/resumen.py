"""
webapp/routers/resumen.py — alerta.pe (zAlerta-12 P1)
═══════════════════════════════════════════════════════════════════════
Resumen del Buzón SUNAT del usuario logueado, pensado para el flujo de push:

  - GET /resumen          → página con la TABLA resumen (offline tras 1ª entrada).
  - GET /api/resumen      → JSON multi-tenant del resumen (lo cachea IndexedDB).
  - POST /api/alerta/vista → registra la lectura (botón GRACIAS del push, métrica).

Multi-tenant SIEMPRE: el estudio ve sus contribuyentes; el empresario, solo el(los)
RUC vinculado(s) por cuenta_empresario_id.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session
from models import (
    Contribuyente, CredencialSol, Notificacion, EstadoContribuyente,
    ETIQUETA_TIPO_DOCUMENTO, ahora_lima, Usuario, EstudioContable,
    Recordatorio, ModoRecordatorio,
    SolicitudValidacionCredencial, EstadoValidacion,
    DocumentoValorado, LecturaNotificacion, Acceso, Persona,
)
from cifrado import cifrar_clave_sol
from ..core import templates, fecha_lima
from ..deps import UsuarioActual, usuario_actual
from ..estados import estado_conexion
from ..deuda import (extraer_monto, fmt_soles, extraer_pago, deudor_de_retencion,
                     anio_deuda_desde_default, resumen_cabecera)
from clasificacion import COACTIVO_META, COACTIVO_NO_SUMA

router = APIRouter(tags=["resumen"])


def _nombre_corto(nombre: str | None) -> str:
    """'SANTANA SIFUENTES JORGE LUIS' → 'Santana' (apellido, legible en móvil)."""
    if not nombre:
        return "—"
    return nombre.strip().split()[0].capitalize()


def _periodo_de(asunto: str | None) -> str:
    """Heurística suave: si el asunto trae un periodo evidente, mostrarlo; si no,
    '—' (no inventar). Mantener simple y prudente."""
    return "—"


# Monto de deuda: fuente ÚNICA en webapp/deuda.py (zAlerta-52) — sin copias
# divergentes. Alias locales para no tocar el resto del archivo.
_monto_de_texto = extraer_monto
_monto_fmt = fmt_soles


@router.get("/resumen", response_class=HTMLResponse)
async def resumen_page(request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    # Estado de conexión HONESTO por RUC (zAlerta-18), visible siempre.
    conexiones = []
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        contribs = list(await session.scalars(
            select(Contribuyente).where(cond)
            .order_by(Contribuyente.razon_social).limit(20)))
        for ct in contribs:
            cred = await session.scalar(
                select(CredencialSol).where(
                    CredencialSol.contribuyente_id == ct.id))
            cx = estado_conexion(ct, cred)
            cx["id"] = str(ct.id)          # para "Actualizar ahora" (zAlerta-36)
            cx["ruc"] = ct.ruc
            cx["razon_social"] = ct.razon_social or ct.ruc
            # zAlerta-27: primera lectura en curso — credencial válida, conexión
            # sana y aún sin scrapeo (ultimo_scrapeo_at NULL). El worker la trae
            # en su próxima pasada; mostramos un aviso suave mientras tanto.
            cx["primera_lectura"] = bool(
                ct.ultimo_scrapeo_at is None and cred and cred.valida
                and cx.get("clave") not in ("error", "pendiente"))
            conexiones.append(cx)
    # zAlerta-34 Paso 4: indicador de espera estilizado mientras la primera
    # lectura está en curso (cualquier RUC con primera_lectura activa).
    # Identificación del BUZÓN ACTIVO (zAlerta-62): esencial en multi-contexto y
    # SOPORTE_GLOBAL, para saber en qué empresa estás parado.
    if user.es_empresario and contribs:
        buzon_nombre = contribs[0].razon_social or contribs[0].ruc
        buzon_ruc = contribs[0].ruc
    else:
        async with get_session() as session:
            buzon_nombre = await session.scalar(
                select(EstudioContable.razon_social).where(
                    EstudioContable.id == user.estudio_id))
        buzon_ruc = None
    ahora = ahora_lima()
    onboarding = any(c.get("primera_lectura") for c in conexiones)
    return templates.TemplateResponse(request, "resumen.html", {
        "user": user, "conexiones": conexiones,
        "onboarding": onboarding,
        "buzon_nombre": buzon_nombre, "buzon_ruc": buzon_ruc,
        "anio_actual": ahora.year, "anio_anterior": ahora.year - 1})


@router.get("/api/resumen")
async def api_resumen(user: UsuarioActual = Depends(usuario_actual)):
    """Resumen JSON del buzón del usuario (lo que la tabla offline cachea)."""
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        sub = select(Contribuyente.id).where(cond)
        # Las 40 notificaciones más recientes (informativos incluidos)…
        recientes_ids = set(await session.scalars(
            select(Notificacion.id).where(Notificacion.contribuyente_id.in_(sub))
            .order_by(Notificacion.fecha_publica_sunat.desc().nullslast(),
                      Notificacion.creado_at.desc()).limit(40)))
        # …MÁS todas las que tienen DEUDA (documento_valorado). zAlerta-49: la
        # deuda NUNCA se oculta por el LIMIT. zAlerta-72: pero SÍ se filtra por el
        # año-desde del buzón (la deuda más vieja se conserva en BD, no se muestra).
        desde_default = anio_deuda_desde_default()
        _anio_doc = func.coalesce(
            func.extract("year", DocumentoValorado.fecha_emision),
            func.extract("year", Notificacion.fecha_publica_sunat))
        deuda_ids = set(await session.scalars(
            select(DocumentoValorado.notificacion_id)
            .join(Contribuyente, Contribuyente.id == DocumentoValorado.contribuyente_id)
            .join(Notificacion, Notificacion.id == DocumentoValorado.notificacion_id)
            .where(cond, DocumentoValorado.notificacion_id.is_not(None),
                   or_(_anio_doc.is_(None),
                       _anio_doc >= func.coalesce(
                           Contribuyente.anio_deuda_desde, desde_default)))))
        todos_ids = recientes_ids | deuda_ids
        rows = (await session.execute(
            select(Notificacion, Contribuyente.ruc, Contribuyente.razon_social)
            .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
            .options(selectinload(Notificacion.adjuntos))
            .where(Notificacion.id.in_(todos_ids))
            .order_by(Notificacion.fecha_publica_sunat.desc().nullslast(),
                      Notificacion.creado_at.desc()))).all()
        # Recordatorios activos del usuario (notif_id → modo) para pintar estado.
        recs = {str(nid): (modo.value if hasattr(modo, "value") else modo)
                for nid, modo in (await session.execute(
                    select(Recordatorio.notificacion_id, Recordatorio.modo)
                    .where(Recordatorio.usuario_id == user.id,
                           Recordatorio.activo.is_(True)))).all()}
        # Deuda valorada por notificación (zAlerta-38): mapa notif_id → DocumentoValorado.
        # El monto se extrae del pdf_texto (provisional; el parser de zAlerta-39
        # llenará columnas). pdf_texto NO se envía al front (pesado).
        notif_ids = [n.id for n, _, _ in rows]
        vals = {}
        if notif_ids:
            for dv in (await session.scalars(
                    select(DocumentoValorado).where(
                        DocumentoValorado.notificacion_id.in_(notif_ids)))).all():
                vals[dv.notificacion_id] = dv
        # Estado de lectura POR PERSONA (zAlerta-61): si hay persona, "leída" es
        # tener fila en lectura_notificacion. Login viejo (sin persona) → flag global.
        leidas_persona: set = set()
        if user.persona_id and notif_ids:
            leidas_persona = set(await session.scalars(
                select(LecturaNotificacion.notificacion_id).where(
                    LecturaNotificacion.persona_id == user.persona_id,
                    LecturaNotificacion.notificacion_id.in_(notif_ids))))
        # Estado de lectura de EQUIPO (Capa 2, zAlerta-68): personas del buzón
        # (accesos nominales vigentes al estudio activo), SIN soporte. En lote.
        equipo_personas: list = []
        lecturas_equipo: set = set()
        if notif_ids:
            hoy = ahora_lima().date()
            equipo_personas = (await session.execute(
                select(Persona.id, Persona.nombre_completo)
                .join(Acceso, Acceso.persona_id == Persona.id)
                .where(Acceso.estudio_id == user.estudio_id,
                       or_(Acceso.vigencia_fin.is_(None), Acceso.vigencia_fin >= hoy),
                       Persona.rol_sistema.is_(None))   # excluir SOPORTE_GLOBAL
                .distinct())).all()
            if len(equipo_personas) >= 2:
                lecturas_equipo = set((await session.execute(
                    select(LecturaNotificacion.persona_id,
                           LecturaNotificacion.notificacion_id)
                    .where(LecturaNotificacion.persona_id.in_(
                               [p.id for p in equipo_personas]),
                           LecturaNotificacion.notificacion_id.in_(notif_ids)))).all())
        # Señal UNIFICADA de lectura activa (zAlerta-42/43): cubre la primera
        # lectura (ultimo_scrapeo_at NULL) Y las re-lecturas del botón "Actualizar
        # ahora" (actualizar_solicitado=True, que el worker baja al terminar). El
        # spinner —de onboarding o de botón— para cuando esto pasa a false.
        activa = await session.scalar(
            select(Contribuyente.id).where(
                cond,
                (Contribuyente.ultimo_scrapeo_at.is_(None))
                | (Contribuyente.actualizar_solicitado.is_(True))).limit(1))
        hay_lectura_activa = activa is not None
        # Última vez que se revisó SUNAT (zAlerta-48 FASE D): la lectura más
        # reciente entre los RUCs del usuario.
        ultima = await session.scalar(
            select(func.max(Contribuyente.ultimo_scrapeo_at)).where(cond))
        ultima_actualizacion = ultima.isoformat() if ultima else None
        # Cabecera: 3 contadores honestos (zAlerta-76). Multi-tenant por estudio.
        cabecera = await resumen_cabecera(session, user.estudio_id)

    filas = []
    for n, ruc, razon in rows:
        tipo_enum = (n.tipo_documento_enum.value
                     if n.tipo_documento_enum is not None else "otro")
        documento = (n.tipo_documento
                     or ETIQUETA_TIPO_DOCUMENTO.get(tipo_enum, "Aviso"))
        # Adjuntos PDF disponibles (solo los que tienen archivo servible en BD).
        # Se mandan id+nombre; el PDF se sirve bajo demanda por /adjuntos/{id}/ver.
        adjuntos = [{"id": str(a.id), "nombre": a.nombre_archivo}
                    for a in (n.adjuntos or [])
                    if a.bytea_temporal is not None or a.gcs_key]
        # Documento valorado: DEUDA, PAGO (zAlerta-69) o COACTIVA por subtipo (zAlerta-70).
        dv = vals.get(n.id)
        deuda = {"tiene_deuda": False}
        pago = None
        coactivo = None
        # Subtipo coactivo que NO es deuda (retención/alivio/cierre/admin).
        coa_no_deuda = (n.subtipo_coactivo in COACTIVO_NO_SUMA)
        if dv is not None:
            tv = (dv.tipo_valorado.value if hasattr(dv.tipo_valorado, "value")
                  else dv.tipo_valorado)
            if tv == "pago":
                p = extraer_pago(dv.pdf_texto)
                pago = {
                    "valorado_id": str(dv.id),
                    "gcs_disponible": bool(dv.gcs_key),
                    "num_orden": p.get("n_orden") or dv.num_documento,
                    **p,
                }
            elif tv == "cobranza_coactiva" and coa_no_deuda:
                # Coactiva que no es deuda: PDF disponible, sin monto (no infla).
                coactivo = {"valorado_id": str(dv.id),
                            "gcs_disponible": bool(dv.gcs_key)}
            else:
                monto = _monto_de_texto(dv.pdf_texto)
                deuda = {
                    "tiene_deuda": True,
                    "valorado_id": str(dv.id),
                    "tipo_valorado": tv,
                    "num_documento": dv.num_documento,
                    "monto": _monto_fmt(monto),
                    "monto_num": monto,
                    "gcs_disponible": bool(dv.gcs_key),
                }
        # Metadatos del subtipo coactivo (etiqueta/acción/grupo/color) para la UI.
        if n.subtipo_coactivo and n.subtipo_coactivo in COACTIVO_META:
            m = COACTIVO_META[n.subtipo_coactivo]
            coactivo = coactivo or {}
            coactivo.update({
                "subtipo": n.subtipo_coactivo, "grupo": m["grupo"],
                "etiqueta": m["etiqueta"], "accion": m["accion"],
            })
            # Retención a terceros: ¿el deudor del PDF es OTRO RUC? → acción requerida.
            if n.subtipo_coactivo == "retencion" and dv is not None:
                deudor = deudor_de_retencion(dv.pdf_texto)
                if deudor and deudor != ruc:
                    coactivo["tercero_retenedor"] = True
                    coactivo["deudor_ruc"] = deudor
        fila = {
            "id": str(n.id),
            "documento": documento,
            "tipo": tipo_enum,
            "periodo": _periodo_de(n.asunto),
            "detalle": (n.asunto or "—")[:160],
            "asunto": n.asunto or "",            # asunto completo para el modal
            "cod_mensaje": n.cod_mensaje_sunat,  # referencia SUNAT
            "adjuntos": adjuntos,                # [{id, nombre}] PDF servibles
            "fecha": fecha_lima(n.fecha_publica_sunat) if n.fecha_publica_sunat else "—",
            "vence": fecha_lima(n.plazo_vencimiento) if n.plazo_vencimiento else "—",
            "vence_iso": (n.plazo_vencimiento.isoformat()
                          if n.plazo_vencimiento else None),
            "recordatorio": recs.get(str(n.id)),   # modo activo o None
            "urgencia": n.urgencia.value if hasattr(n.urgencia, "value") else "sin_clasificar",
            "ruc": ruc,
            "razon_social": razon or ruc,
            "leida": (n.id in leidas_persona) if user.persona_id else bool(n.leida),
            "es_pago": pago is not None,
            "pago": pago,
            "coactivo": coactivo,
            **deuda,
        }
        # Estado de equipo (Capa 2): solo si el buzón tiene 2+ personas.
        if len(equipo_personas) >= 2:
            miembros, leidos = [], 0
            for pid, nom in equipo_personas:
                vio = (pid, n.id) in lecturas_equipo
                if vio:
                    leidos += 1
                miembros.append({"nombre": _nombre_corto(nom), "leida": vio})
            fila["equipo"] = {"total": len(equipo_personas),
                              "leidos": leidos, "miembros": miembros}
        filas.append(fila)

    return JSONResponse({
        "ok": True,
        "generado_at": ahora_lima().isoformat(),
        "total": len(filas),
        "hay_lectura_activa": hay_lectura_activa,
        "lectura_pendiente": hay_lectura_activa,   # alias compat (JS viejo en caché)
        "ultima_actualizacion": ultima_actualizacion,
        "no_leidas": sum(1 for f in filas if not f.get("leida")),
        "cabecera": cabecera,
        "filas": filas,
    })


@router.post("/contribuyentes/{contribuyente_id}/desconectar")
async def desconectar_ruc(
    contribuyente_id: uuid.UUID,
    user: UsuarioActual = Depends(usuario_actual)):
    """Derecho de corte (zAlerta-12 P3): el dueño desconecta SU RUC. Pausa el
    monitoreo (estado INACTIVO) y borra la credencial SOL cifrada — alerta.pe
    deja de acceder. Scoped: el empresario solo su propio RUC; el estudio los
    suyos. El empresario es solo-lectura para todo lo demás, pero SIEMPRE puede
    cortar el acceso a su información."""
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        contrib = await session.scalar(
            select(Contribuyente).where(
                Contribuyente.id == contribuyente_id, cond))
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        contrib.estado = EstadoContribuyente.INACTIVO
        # Borrar la credencial SOL cifrada: cortar el acceso de raíz.
        cred = await session.scalar(
            select(CredencialSol).where(
                CredencialSol.contribuyente_id == contrib.id))
        if cred:
            await session.delete(cred)
        await session.commit()
    return JSONResponse({"ok": True})


_MODOS_VALIDOS = {m.value for m in ModoRecordatorio}


@router.post("/api/recordatorio")
async def api_recordatorio(request: Request,
                           user: UsuarioActual = Depends(usuario_actual)):
    """"Recuérdame esto" (zAlerta-13 P1): activa/cambia/desactiva el recordatorio
    de una notificación para el usuario. modo=None → desactiva. Solo notificaciones
    de los contribuyentes accesibles por el usuario (multi-tenant)."""
    data = await request.json()
    try:
        notif_id = uuid.UUID(str(data.get("notificacion_id")))
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "Notificación inválida."}, status_code=400)
    modo = (data.get("modo") or "").strip() or None
    if modo and modo not in _MODOS_VALIDOS:
        return JSONResponse({"ok": False, "error": "Modo inválido."}, status_code=400)

    async with get_session() as session:
        # Verificar que la notificación es accesible por el usuario.
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        notif = await session.scalar(
            select(Notificacion)
            .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
            .where(Notificacion.id == notif_id, cond))
        if not notif:
            return JSONResponse({"ok": False}, status_code=404)

        rec = await session.scalar(
            select(Recordatorio).where(
                Recordatorio.notificacion_id == notif_id,
                Recordatorio.usuario_id == user.id))
        if modo is None:
            if rec:
                rec.activo = False
                await session.commit()
            return JSONResponse({"ok": True, "recordatorio": None})
        # Personas sin fila en `usuarios` (acceso institucional solo lectura) no
        # pueden crear recordatorios (FK usuario_id). No-op amable.
        if not rec and not user.tiene_usuario:
            return JSONResponse({"ok": False, "error": "Solo lectura."}, status_code=403)
        if rec:
            rec.modo = ModoRecordatorio(modo)
            rec.activo = True
            rec.fecha_vencimiento = notif.plazo_vencimiento
        else:
            session.add(Recordatorio(
                estudio_id=notif.estudio_id, notificacion_id=notif_id,
                usuario_id=user.id, modo=ModoRecordatorio(modo), activo=True,
                fecha_vencimiento=notif.plazo_vencimiento))
        await session.commit()
    return JSONResponse({"ok": True, "recordatorio": modo})


async def _contrib_propio(session, user: UsuarioActual, contribuyente_id):
    """Devuelve el contribuyente si el usuario es su dueño/vigilante, o None."""
    if user.es_empresario:
        cond = Contribuyente.cuenta_empresario_id == user.estudio_id
    else:
        cond = Contribuyente.estudio_id == user.estudio_id
    return await session.scalar(
        select(Contribuyente).where(Contribuyente.id == contribuyente_id, cond))


@router.post("/contribuyentes/{contribuyente_id}/cred/validar")
async def cred_validar(contribuyente_id: uuid.UUID, request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    """Actualizar credenciales SOL (zAlerta-13 P2) · paso 1: VALIDAR sin guardar.
    Encola un login-only (flag→worker→resultado) con las credenciales NUEVAS. NO
    toca la credencial actual: así no dejamos al usuario sin nada si fallan."""
    data = await request.json()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Ingresa usuario y clave SOL."},
                            status_code=400)
    async with get_session() as session:
        contrib = await _contrib_propio(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        sol = SolicitudValidacionCredencial(
            estudio_id=user.estudio_id, ruc=contrib.ruc, usuario_sol=usuario_sol,
            clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            estado=EstadoValidacion.PENDIENTE)
        session.add(sol)
        await session.commit()
        sol_id = str(sol.id)
    return JSONResponse({"ok": True, "id": sol_id})


@router.post("/contribuyentes/{contribuyente_id}/cred/guardar")
async def cred_guardar(contribuyente_id: uuid.UUID, request: Request,
                       user: UsuarioActual = Depends(usuario_actual)):
    """Actualizar credenciales SOL · paso 2: GUARDAR (tras confirmar que conecta).
    Reemplaza la credencial (Fernet), reactiva el contribuyente si estaba en
    ERROR_CREDENCIAL y limpia el aviso. La clave nunca se devuelve ni se loguea."""
    data = await request.json()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Ingresa usuario y clave SOL."},
                            status_code=400)
    async with get_session() as session:
        contrib = await _contrib_propio(session, user, contribuyente_id)
        if not contrib:
            return JSONResponse({"ok": False}, status_code=404)
        cred = await session.scalar(
            select(CredencialSol).where(
                CredencialSol.contribuyente_id == contrib.id))
        if cred:
            cred.usuario_sol = usuario_sol
            cred.clave_sol_cifrada = cifrar_clave_sol(clave_sol)
            cred.valida = True
            cred.ultimo_login_ok_at = ahora_lima()
        else:
            session.add(CredencialSol(
                contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
                usuario_sol=usuario_sol,
                clave_sol_cifrada=cifrar_clave_sol(clave_sol),
                tipo_usuario=2, quien_cargo=user.id, valida=True,
                ultimo_login_ok_at=ahora_lima()))
        # Guardar una credencial válida es RECONECTAR: reactiva el RUC venga de
        # ERROR_CREDENCIAL o de INACTIVO (zAlerta-51: antes quedaba pegado en
        # INACTIVO tras desconectar+reconectar → el fondo, que filtra estado=ACTIVO,
        # lo saltaba del ciclo automático).
        if contrib.estado in (EstadoContribuyente.ERROR_CREDENCIAL,
                              EstadoContribuyente.INACTIVO):
            contrib.estado = EstadoContribuyente.ACTIVO
        contrib.credencial_error_avisada = False
        # Default del filtro de años de deuda (zAlerta-72): al conectar, año
        # actual − 2 (arranque rápido). Ajustable luego en la config del buzón.
        if contrib.anio_deuda_desde is None:
            contrib.anio_deuda_desde = anio_deuda_desde_default()
            contrib.anio_deuda_cubierto_desde = contrib.anio_deuda_desde
        # Primera lectura inmediata (zAlerta-27): guardar una credencial válida
        # (alta o reconexión) encola una lectura fresca en la cola diurna del
        # botón "Actualizar ahora"; el worker la procesa y limpia el flag.
        contrib.actualizar_solicitado = True
        contrib.actualizar_solicitado_at = ahora_lima()
        await session.commit()
    return JSONResponse({"ok": True})


@router.get("/api/valor/{valorado_id}/asociados")
async def api_valor_asociados(valorado_id: uuid.UUID,
                              user: UsuarioActual = Depends(usuario_actual)):
    """Documentos ASOCIADOS a un valor de deuda (zAlerta-73): pagos que lo pagan
    + resoluciones coactivas que lo ejecutan. Al vuelo por número normalizado,
    SIN calcular saldo (solo yuxtapone hechos). Multi-tenant."""
    from ..deuda import asociados_de_valor
    async with get_session() as session:
        dv = await session.get(DocumentoValorado, valorado_id)
        if not dv:
            return JSONResponse({"ok": False}, status_code=404)
        # Multi-tenant: el valor debe colgar de un contribuyente accesible.
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        ok = await session.scalar(
            select(Contribuyente.id).where(
                Contribuyente.id == dv.contribuyente_id, cond))
        if not ok:
            return JSONResponse({"ok": False}, status_code=404)
        subs = {n: st for n, st in (await session.execute(
            select(Notificacion.id, Notificacion.subtipo_coactivo)
            .where(Notificacion.contribuyente_id == dv.contribuyente_id))).all()}
        asoc = await asociados_de_valor(session, dv, subs)
    return JSONResponse({"ok": True, "num_documento": dv.num_documento, **asoc})


@router.post("/api/alerta/vista")
async def api_alerta_vista(user: UsuarioActual = Depends(usuario_actual)):
    """Registra que el usuario confirmó la lectura del push (botón GRACIAS).
    Métrica sutil; no obligatorio. Solo sella la fecha en el usuario."""
    async with get_session() as session:
        u = await session.get(Usuario, user.id)
        if u:
            u.ultima_alerta_vista_at = ahora_lima()
            await session.commit()
    return JSONResponse({"ok": True})


@router.post("/api/notificacion/{notif_id}/leida")
async def api_notificacion_leida(notif_id: uuid.UUID,
                                 user: UsuarioActual = Depends(usuario_actual)):
    """Marca UNA notificación como leída (zAlerta-47). Se llama al ABRIR su modal
    en el buzón (no al cargar la lista), para que lo NUEVO siga resaltado hasta que
    el usuario realmente lo mire. Estado server-side → compartido entre equipos.
    Multi-tenant: solo notificaciones de los RUCs accesibles por el usuario."""
    async with get_session() as session:
        if user.es_empresario:
            cond = Contribuyente.cuenta_empresario_id == user.estudio_id
        else:
            cond = Contribuyente.estudio_id == user.estudio_id
        notif = await session.scalar(
            select(Notificacion)
            .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
            .where(Notificacion.id == notif_id, cond))
        if not notif:
            return JSONResponse({"ok": False}, status_code=404)

        # ── PERSONA (zAlerta-61): estado por-persona, no toca el flag global ──
        if user.persona_id:
            # SOPORTE_GLOBAL no contamina el estado del equipo: solo se registra
            # la lectura si la persona tiene acceso NOMINAL vigente a este buzón.
            hoy = ahora_lima().date()
            nominal = await session.scalar(
                select(Acceso.id).where(
                    Acceso.persona_id == user.persona_id,
                    Acceso.estudio_id == user.estudio_id,
                    (Acceso.vigencia_fin.is_(None)) | (Acceso.vigencia_fin >= hoy)))
            if nominal:
                await session.execute(
                    pg_insert(LecturaNotificacion)
                    .values(persona_id=user.persona_id, notificacion_id=notif_id,
                            leida_at=ahora_lima())
                    .on_conflict_do_nothing(
                        index_elements=["persona_id", "notificacion_id"]))
                await session.commit()
            return JSONResponse({"ok": True})

        # ── USUARIO viejo (sin persona): flag global, como hasta hoy ──
        if not notif.leida:
            notif.leida = True
            notif.leida_at = ahora_lima()
            await session.commit()
    return JSONResponse({"ok": True})
