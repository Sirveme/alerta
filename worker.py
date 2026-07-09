"""
worker.py — alerta.pe   (C:\\alertape\\worker.py)
═══════════════════════════════════════════════════════════════════════
Worker de scraping (Playwright + Chromium). Proceso SEPARADO de la WebApp.

La WebApp (webapp/) es LIVIANA y NO tiene Playwright: nunca scrapea. Este
worker SÍ tiene Playwright y es el único que toca SUNAT. Comparte la misma
BD (DATABASE_URL) que la web.

Cada ciclo corto (INTERVALO_SEGUNDOS):
  1) PRIORIDAD — contribuyentes con `actualizar_solicitado = true`
     (el botón "Actualizar ahora" de la web): se scrapean YA (forzar=True),
     ignorando frescura, y se limpia el flag tras el intento.
  2) FONDO — todos los contribuyentes activos: se scrapean solo si su
     frescura venció (>FRESCURA_HORAS); si están frescos, se sirven de BD.

Usa el scraper validado (scraper_sunat_playwgth) + ingesta con dedup, vía
run_scraper.procesar_contribuyente. NO sirve HTTP (no necesita healthcheck).

Uso (local o Railway):
    python worker.py

ENV opcionales:
    WORKER_INTERVALO_SEG   (default 90)   — segundos entre ciclos
    WORKER_FRESCURA_HORAS  (default 3)    — frescura del ciclo de fondo
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from db import get_session
from datetime import timedelta

from models import (
    Contribuyente, EstadoContribuyente, Notificacion,
    SolicitudValidacionCredencial, EstadoValidacion, ahora_lima,
    EstudioContable, CredencialSol, EstadoSuscripcion, TipoCuenta,
    Recordatorio, ModoRecordatorio, Usuario, ETIQUETA_TIPO_DOCUMENTO,
    TipoDocumento,
)

# Horarios (hora Lima) en que se permiten los push de recordatorio (no spamear).
HORAS_RECORDATORIO = {8, 12, 17}
# Horarios fijos del PUSH AGRUPADO (zAlerta-48 FASE C). 06:00 avisa lo de la
# madrugada; el resto, lo nuevo desde el push anterior. Nada entre 19h y 06h.
HORAS_PUSH = {6, 9, 11, 14, 17}
# Tipos de deuda a destacar en el push agrupado.
_DEUDA_TIPOS = {TipoDocumento.ORDEN_PAGO, TipoDocumento.MULTA,
                TipoDocumento.COBRANZA_COACTIVA, TipoDocumento.FRACCIONAMIENTO,
                TipoDocumento.RESOLUCION_DETERMINACION}

# Días de prueba (zAlerta-11c D): arrancan al CONFIRMAR la conexión, no antes.
DIAS_PRUEBA_EMPRESARIO = 7
# Reutilizamos la lógica validada del orquestador (scraper + ingesta + dedup).
from run_scraper import procesar_contribuyente, log, FRESCURA_HORAS_DEFAULT
from cifrado import descifrar_clave_sol
# Login-only para "Comprobar conexión" (reusa el login del scraper, no lo toca).
from validar_login import validar_login_sync
import push_service

TZ_LIMA = ZoneInfo("America/Lima")

INTERVALO_SEGUNDOS = int(os.getenv("WORKER_INTERVALO_SEG", "90"))
FRESCURA_HORAS = int(os.getenv("WORKER_FRESCURA_HORAS", str(FRESCURA_HORAS_DEFAULT)))

# Ventana NOCTURNA de lectura (zAlerta-12 P1.a): la conexión a SUNAT es mejor de
# noche. El ciclo de FONDO (scrapeo masivo) solo corre dentro de esta franja
# (hora Lima). Las validaciones y las solicitudes manuales (botón "Actualizar
# ahora") corren SIEMPRE. Configurable por env. Si INICIO==FIN, fondo 24h.
VENTANA_INICIO = int(os.getenv("WORKER_VENTANA_INICIO", "23"))   # 23:00
VENTANA_FIN = int(os.getenv("WORKER_VENTANA_FIN", "6"))          # 06:00


def _en_ventana_nocturna() -> bool:
    if VENTANA_INICIO == VENTANA_FIN:
        return True   # sin restricción
    h = datetime.now(TZ_LIMA).hour
    if VENTANA_INICIO < VENTANA_FIN:
        return VENTANA_INICIO <= h < VENTANA_FIN
    # Franja que cruza medianoche (ej. 23 → 6)
    return h >= VENTANA_INICIO or h < VENTANA_FIN


async def _arrancar_prueba_si_corresponde(session, contrib) -> None:
    """Prueba diferida del empresario (zAlerta-11c D): si este contribuyente
    pertenece a una cuenta-empresario en PRUEBA cuyo vence_at aún es NULL y el
    último scrapeo fue OK (login real confirmado), AHORA arranca la prueba:
    fija vence_at = hoy+7 y sella la credencial como verificada. Idempotente
    (si ya tenía vence_at, no hace nada). No toca el motor de login."""
    if not contrib.ultimo_scrapeo_ok:
        return
    estudio = await session.get(EstudioContable, contrib.estudio_id)
    if (estudio and estudio.tipo_cuenta == TipoCuenta.EMPRESARIO.value
            and estudio.estado_suscripcion == EstadoSuscripcion.PRUEBA.value
            and estudio.suscripcion_vence_at is None):
        ahora = ahora_lima()
        estudio.suscripcion_vence_at = ahora + timedelta(days=DIAS_PRUEBA_EMPRESARIO)
        cred = await session.scalar(
            select(CredencialSol).where(
                CredencialSol.contribuyente_id == contrib.id))
        if cred and cred.ultimo_login_ok_at is None:
            cred.ultimo_login_ok_at = ahora
        log(f"  {contrib.ruc}: prueba de 7 días iniciada (conexión confirmada).",
            "OK")
        # zAlerta-18 P4: avisar que el buzón ya quedó VIGILADO.
        for uid in await _usuarios_de(session, contrib):
            try:
                await push_service.notificar_usuario(
                    session, uid, "¡Tu buzón ya está vigilado!",
                    "Conectamos tu buzón SUNAT. Tus 7 días de prueba comienzan hoy.",
                    url="/resumen?from=push")
            except Exception as e:
                log(f"  {contrib.ruc}: aviso 'vigilado' falló (sigo): {e}", "WARN")


async def _revisar_credencial(session, contrib) -> None:
    """Marca ERROR_CREDENCIAL y avisa por push cuando una credencial deja de
    servir (zAlerta-13 P2). Al reconectar, vuelve a ACTIVO y limpia el aviso.

    Heurística prudente: solo aplica si el contribuyente TIENE credencial (un
    fallo con credencial cargada es, casi siempre, clave caducada). Si fue un
    fallo transitorio de red, el próximo scrapeo OK lo reactiva."""
    cred = await session.scalar(
        select(CredencialSol).where(CredencialSol.contribuyente_id == contrib.id))
    if not cred:
        return
    if contrib.ultimo_scrapeo_ok is False:
        if contrib.estado != EstadoContribuyente.ERROR_CREDENCIAL:
            contrib.estado = EstadoContribuyente.ERROR_CREDENCIAL
        if not contrib.credencial_error_avisada:
            usuarios = await _usuarios_de(session, contrib)
            for uid in usuarios:
                try:
                    await push_service.notificar_usuario(
                        session, uid, "No pudimos entrar a tu buzón",
                        "¿Cambiaste tu Clave SOL? Actualízala para seguir vigilando.",
                        url="/")
                except Exception as e:
                    log(f"  {contrib.ruc}: aviso credencial falló (sigo): {e}", "WARN")
            contrib.credencial_error_avisada = True
    elif contrib.ultimo_scrapeo_ok:
        # Reconectó: limpiar el estado de error y el flag de aviso.
        if contrib.estado == EstadoContribuyente.ERROR_CREDENCIAL:
            contrib.estado = EstadoContribuyente.ACTIVO
        contrib.credencial_error_avisada = False


async def _usuarios_de(session, contrib) -> list:
    """IDs de usuarios a notificar de este RUC (estudio que vigila + empresario)."""
    ids = list(await session.scalars(
        select(Usuario.id).where(Usuario.estudio_id == contrib.estudio_id,
                                 Usuario.activo.is_(True))))
    if contrib.cuenta_empresario_id:
        ids += list(await session.scalars(
            select(Usuario.id).where(
                Usuario.estudio_id == contrib.cuenta_empresario_id,
                Usuario.activo.is_(True))))
    return ids


async def _procesar_y_notificar(session, contrib, forzar: bool,
                                full: bool = False) -> None:
    """Scrapea/ingesta y, si aparecieron notificaciones NUEVAS, envía push.

    Detectamos las nuevas contando antes/después (sin tocar run_scraper, que es
    motor). El push NUNCA rompe el ciclo: cualquier fallo se loguea y se sigue.

    full (zAlerta-46): True fuerza barrido COMPLETO (nocturno de seguridad);
    False deja que run_scraper elija incremental si ya hubo un full previo.
    """
    n_antes = await session.scalar(
        select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == contrib.id)) or 0
    await procesar_contribuyente(session, contrib, FRESCURA_HORAS,
                                 forzar=forzar, full=full)
    # Prueba diferida del empresario: si el login real recién se confirmó, arranca.
    await _arrancar_prueba_si_corresponde(session, contrib)
    # ¿La credencial dejó de servir? marcar ERROR_CREDENCIAL + avisar (1 vez).
    await _revisar_credencial(session, contrib)
    n_despues = await session.scalar(
        select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == contrib.id)) or 0
    nuevas = n_despues - n_antes
    # zAlerta-48 FASE C: NO se envía push aquí. Las notificaciones nuevas quedan
    # con notificado_push=False y el push se AGRUPA en horarios fijos
    # (_enviar_push_agrupado). Así no se despierta al usuario a media madrugada ni
    # se le manda un push por cada revisión.
    if nuevas > 0:
        log(f"  {contrib.ruc}: {nuevas} nueva(s) — push diferido a horario fijo.", "OK")


async def _procesar_solicitudes_manuales(session) -> int:
    """Scrapea YA los contribuyentes con flag de actualización (prioridad)."""
    solicitados = list(await session.scalars(
        select(Contribuyente)
        .options(selectinload(Contribuyente.credencial))
        .where(Contribuyente.actualizar_solicitado.is_(True))
        .order_by(Contribuyente.actualizar_solicitado_at)))

    if not solicitados:
        return 0

    log(f"PRIORIDAD: {len(solicitados)} actualización(es) solicitada(s).")
    for contrib in solicitados:
        try:
            # Manual "Actualizar ahora" → INCREMENTAL (rápido). zAlerta-46.
            await _procesar_y_notificar(session, contrib, forzar=True, full=False)
        except Exception as e:
            log(f"  {contrib.ruc}: error inesperado (manual): {e}", "ERROR")
        finally:
            # Limpiar el flag tras el INTENTO (éxito o no) para no reprocesar en
            # bucle. Si falló, el ciclo de fondo lo reintentará por frescura.
            contrib.actualizar_solicitado = False
            await session.commit()
    return len(solicitados)


async def _procesar_validaciones_credencial(session) -> int:
    """Procesa las solicitudes de "Comprobar conexión" (zAlerta-10 B).

    Hace un login-only REAL a SUNAT con la credencial cifrada que encoló la
    web, escribe el resultado (CONECTA / NO_CONECTA / ERROR) y BORRA la clave
    cifrada (higiene: la solicitud es efímera). Cada fallo se aísla: una
    validación que reviente no detiene a las demás ni al ciclo.
    """
    pendientes = list(await session.scalars(
        select(SolicitudValidacionCredencial)
        .where(SolicitudValidacionCredencial.estado == EstadoValidacion.PENDIENTE)
        .order_by(SolicitudValidacionCredencial.creado_at)))

    if not pendientes:
        return 0

    log(f"VALIDACIÓN: {len(pendientes)} credencial(es) por comprobar.")
    for sol in pendientes:
        sol.estado = EstadoValidacion.COMPROBANDO
        await session.commit()
        try:
            clave = descifrar_clave_sol(sol.clave_sol_cifrada)
            conecta = await asyncio.to_thread(
                validar_login_sync, sol.ruc, sol.usuario_sol, clave)
            sol.estado = (EstadoValidacion.CONECTA if conecta
                          else EstadoValidacion.NO_CONECTA)
            log(f"  {sol.ruc}: {'conecta' if conecta else 'NO conecta'}.",
                "OK" if conecta else "WARN")
        except Exception as e:
            sol.estado = EstadoValidacion.ERROR
            log(f"  {sol.ruc}: error al validar (sigo): {e}", "ERROR")
        finally:
            # Nunca conservar la clave cifrada más allá del intento.
            sol.clave_sol_cifrada = None
            sol.procesado_at = ahora_lima()
            await session.commit()
    return len(pendientes)


def _debe_recordar(modo, dias_a_vencer: int, dias_desde_activacion: int) -> bool:
    """¿Toca recordar hoy según el modo? (dias_a_vencer = venc - hoy)."""
    if dias_a_vencer < 0:
        return False                       # ya venció
    if modo == ModoRecordatorio.HASTA_VENCER:
        return True
    if modo == ModoRecordatorio.ULTIMOS_3:
        return 0 <= dias_a_vencer <= 3
    if modo == ModoRecordatorio.PROXIMOS_3:
        return dias_desde_activacion < 3
    return False


async def _procesar_recordatorios(session) -> int:
    """"Recuérdame esto" (zAlerta-13 P1): reenvía push recordatorio según el modo,
    máximo 1 vez al día y solo en los horarios definidos. Desactiva los vencidos."""
    ahora = ahora_lima()
    if ahora.hour not in HORAS_RECORDATORIO:
        return 0   # fuera de los horarios 8/12/17: no spamear
    hoy = ahora.date()
    activos = list(await session.scalars(
        select(Recordatorio).where(Recordatorio.activo.is_(True))))
    enviados = 0
    for rec in activos:
        venc = rec.fecha_vencimiento
        if not venc:
            continue
        dias_a_vencer = (venc.date() - hoy).days
        if dias_a_vencer < 0:
            rec.activo = False             # venció → dejar de recordar
            continue
        # Ya enviado hoy → no repetir.
        if rec.ultimo_envio_at and rec.ultimo_envio_at.date() == hoy:
            continue
        dias_desde = (hoy - rec.creado_at.date()).days
        if not _debe_recordar(rec.modo, dias_a_vencer, dias_desde):
            continue
        notif = await session.get(Notificacion, rec.notificacion_id)
        if not notif:
            rec.activo = False
            continue
        etq = ETIQUETA_TIPO_DOCUMENTO.get(
            notif.tipo_documento_enum.value if notif.tipo_documento_enum else None,
            notif.tipo_documento or "Notificación")
        fecha_txt = venc.astimezone(TZ_LIMA).strftime("%d/%m/%Y")
        try:
            await push_service.notificar_usuario(
                session, rec.usuario_id, "Recordatorio de alerta.pe",
                f"{etq} vence el {fecha_txt}. No dejes pasar el plazo.",
                url="/resumen")
            enviados += 1
        except Exception as e:
            log(f"  recordatorio {rec.id}: push falló (sigo): {e}", "WARN")
        rec.ultimo_envio_at = ahora
    await session.commit()
    if enviados:
        log(f"RECORDATORIOS: {enviados} push enviado(s).", "OK")
    return enviados


async def _procesar_fondo(session, full: bool = False) -> int:
    """Scrapea por frescura vencida (ciclo de fondo, no fuerza).

    zAlerta-48 FASE B: corre SIEMPRE (día y noche); la FRESCURA (FRESCURA_HORAS)
    limita a ~1 revisión/RUC cada pocas horas, sin martillear. De DÍA va
    INCREMENTAL (full=False, ~20s/RUC); de NOCHE va FULL (full=True), que es la
    red de seguridad de zAlerta-46 (recupera cualquier hueco + sella
    ultimo_barrido_full_at). El push NO se envía aquí (se agrupa por horario).
    """
    activos = list(await session.scalars(
        select(Contribuyente)
        .options(selectinload(Contribuyente.credencial))
        .where(Contribuyente.estado == EstadoContribuyente.ACTIVO)))

    for contrib in activos:
        try:
            await _procesar_y_notificar(session, contrib, forzar=False, full=full)
        except Exception as e:
            log(f"  {contrib.ruc}: error inesperado (fondo): {e}", "ERROR")
    return len(activos)


async def _enviar_push_agrupado(session) -> int:
    """PUSH AGRUPADO por horarios fijos (zAlerta-48 FASE C).

    Solo en HORAS_PUSH. Reúne las notificaciones NUEVAS aún no avisadas
    (notificado_push=False), las agrupa por usuario y envía UN push por usuario
    ("Tienes N avisos nuevos…", destacando deuda). Luego marca esas notificaciones
    como avisadas → un solo push por notificación, sin repetir aunque el usuario no
    lo abra. No depende de "¿vio el push?"."""
    ahora = ahora_lima()
    if ahora.hour not in HORAS_PUSH:
        return 0
    notifs = list(await session.scalars(
        select(Notificacion).where(Notificacion.notificado_push.is_(False))))
    if not notifs:
        return 0

    por_contrib = defaultdict(list)
    for n in notifs:
        por_contrib[n.contribuyente_id].append(n)
    contribs = {c.id: c for c in await session.scalars(
        select(Contribuyente).where(Contribuyente.id.in_(list(por_contrib.keys()))))}

    # Acumular por usuario (un usuario puede vigilar varios RUCs).
    por_usuario = defaultdict(lambda: {"total": 0, "deuda": 0})
    for cid, lst in por_contrib.items():
        contrib = contribs.get(cid)
        if not contrib:
            continue
        deuda = sum(1 for n in lst if n.tipo_documento_enum in _DEUDA_TIPOS)
        for uid in await _usuarios_de(session, contrib):
            por_usuario[uid]["total"] += len(lst)
            por_usuario[uid]["deuda"] += deuda

    enviados = 0
    for uid, d in por_usuario.items():
        total, deuda = d["total"], d["deuda"]
        if deuda > 0:
            body = (f"Tienes {total} aviso(s) nuevo(s), {deuda} con deuda por "
                    f"atender. Toca RESUMEN para revisarlos.")
        else:
            body = f"Tienes {total} aviso(s) nuevo(s) en tu Buzón SUNAT."
        try:
            await push_service.notificar_usuario(
                session, uid, "Novedades en tu Buzón SUNAT", body,
                url="/resumen?from=push", acciones=True)
            enviados += 1
        except Exception as e:
            log(f"  push agrupado usuario {uid}: falló (sigo): {e}", "WARN")

    # Marcar TODAS como avisadas (un push por notificación, próximo horario).
    for n in notifs:
        n.notificado_push = True
        n.notificado_push_at = ahora
    await session.commit()
    log(f"PUSH AGRUPADO ({ahora.hour}h): {len(notifs)} notif nuevas → "
        f"{enviados} usuario(s).", "OK")
    return enviados


async def ciclo() -> None:
    """Un ciclo: primero solicitudes manuales (prioridad), luego frescura."""
    async with get_session() as session:
        # Validaciones, solicitudes manuales y recordatorios: SIEMPRE (los
        # recordatorios solo disparan en sus horarios 8/12/17).
        await _procesar_validaciones_credencial(session)
        await _procesar_recordatorios(session)
        await _procesar_solicitudes_manuales(session)
        # Revisión de fondo SIEMPRE (zAlerta-48 FASE B): de noche FULL (seguridad),
        # de día INCREMENTAL (~20s/RUC). La frescura limita la frecuencia (~cada
        # FRESCURA_HORAS h), sin martillear. El push va aparte, por horario.
        full = _en_ventana_nocturna()
        n = await _procesar_fondo(session, full=full)
        log(f"Ciclo completo ({n} activo(s); modo {'FULL nocturno' if full else 'incremental diurno'}).", "OK")
        # Push AGRUPADO en horarios fijos (zAlerta-48 FASE C).
        await _enviar_push_agrupado(session)


async def main() -> None:
    log("═══ alerta.pe — worker de scraping (Playwright) ═══")
    log(f"Intervalo: {INTERVALO_SEGUNDOS}s · Frescura fondo: {FRESCURA_HORAS}h")
    while True:
        try:
            await ciclo()
        except Exception as e:
            # Un fallo de ciclo NUNCA debe matar al worker.
            log(f"Error en el ciclo (continúo): {e}", "ERROR")
        await asyncio.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    asyncio.run(main())
