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
from models import Contribuyente, EstadoContribuyente, Notificacion
# Reutilizamos la lógica validada del orquestador (scraper + ingesta + dedup).
from run_scraper import procesar_contribuyente, log, FRESCURA_HORAS_DEFAULT
import push_service

TZ_LIMA = ZoneInfo("America/Lima")

INTERVALO_SEGUNDOS = int(os.getenv("WORKER_INTERVALO_SEG", "90"))
FRESCURA_HORAS = int(os.getenv("WORKER_FRESCURA_HORAS", str(FRESCURA_HORAS_DEFAULT)))


async def _procesar_y_notificar(session, contrib, forzar: bool) -> None:
    """Scrapea/ingesta y, si aparecieron notificaciones NUEVAS, envía push.

    Detectamos las nuevas contando antes/después (sin tocar run_scraper, que es
    motor). El push NUNCA rompe el ciclo: cualquier fallo se loguea y se sigue.
    """
    n_antes = await session.scalar(
        select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == contrib.id)) or 0
    await procesar_contribuyente(session, contrib, FRESCURA_HORAS, forzar=forzar)
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
        await _procesar_solicitudes_manuales(session)
        n = await _procesar_fondo(session)
    log(f"Ciclo completo ({n} activo(s) revisado(s)).", "OK")


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
