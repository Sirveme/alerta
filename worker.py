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
)

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


async def _procesar_y_notificar(session, contrib, forzar: bool) -> None:
    """Scrapea/ingesta y, si aparecieron notificaciones NUEVAS, envía push.

    Detectamos las nuevas contando antes/después (sin tocar run_scraper, que es
    motor). El push NUNCA rompe el ciclo: cualquier fallo se loguea y se sigue.
    """
    n_antes = await session.scalar(
        select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == contrib.id)) or 0
    await procesar_contribuyente(session, contrib, FRESCURA_HORAS, forzar=forzar)
    # Prueba diferida del empresario: si el login real recién se confirmó, arranca.
    await _arrancar_prueba_si_corresponde(session, contrib)
    n_despues = await session.scalar(
        select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == contrib.id)) or 0
    nuevas = n_despues - n_antes
    if nuevas > 0:
        try:
            res = await push_service.notificar_nuevas(session, contrib, nuevas)
            log(f"  {contrib.ruc}: {nuevas} nueva(s) → push a {res['usuarios']} "
                f"usuario(s) ({res['enviadas']} enviada(s)).", "OK")
        except Exception as e:
            log(f"  {contrib.ruc}: push falló (sigo): {e}", "WARN")


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
            await _procesar_y_notificar(session, contrib, forzar=True)
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


async def _procesar_fondo(session) -> int:
    """Scrapea por frescura vencida (ciclo de fondo, no fuerza)."""
    activos = list(await session.scalars(
        select(Contribuyente)
        .options(selectinload(Contribuyente.credencial))
        .where(Contribuyente.estado == EstadoContribuyente.ACTIVO)))

    for contrib in activos:
        try:
            await _procesar_y_notificar(session, contrib, forzar=False)
        except Exception as e:
            log(f"  {contrib.ruc}: error inesperado (fondo): {e}", "ERROR")
    return len(activos)


async def ciclo() -> None:
    """Un ciclo: primero solicitudes manuales (prioridad), luego frescura."""
    async with get_session() as session:
        # Validaciones y solicitudes manuales: SIEMPRE (no esperan a la noche).
        await _procesar_validaciones_credencial(session)
        await _procesar_solicitudes_manuales(session)
        # Lectura masiva de buzones: SOLO en la ventana nocturna (zAlerta-12 P1.a).
        if _en_ventana_nocturna():
            n = await _procesar_fondo(session)
            log(f"Ciclo completo ({n} activo(s) revisado(s)).", "OK")
        else:
            log(f"Fuera de ventana nocturna ({VENTANA_INICIO}h–{VENTANA_FIN}h "
                "Lima): fondo en pausa; validaciones/manuales activas.", "INFO")


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
