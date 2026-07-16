"""
run_scraper.py — alerta.pe   (C:\\alertape\\run_scraper.py)
═══════════════════════════════════════════════════════════════════════
Orquestador: cierra el circuito scraper → BD.

Por cada contribuyente ACTIVO con credencial válida:
  1. descifra la clave SOL (Fernet)
  2. corre el scraper Playwright (login + buzón + mensajes + PDFs)
  3. ingesta el resultado en la BD con dedup
  4. (opcional) acumula pistas para investigar httpx

Uso:
    python run_scraper.py                 # todos los contribuyentes activos
    python run_scraper.py <RUC>           # solo ese RUC

Pensado para correr en el scheduler (Railway beat) en producción.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import get_session
from models import (Contribuyente, CredencialSol, EstadoContribuyente,
                    Notificacion, BarridoMetrica)
from cifrado import descifrar_clave_sol
from ingesta import ingestar_resultado

# El scraper validado (Playwright puro). Ajustar al nombre real del archivo.
import scraper_sunat_playwgth as scraper

TZ_LIMA = ZoneInfo("America/Lima")

# Frescura para el SCHEDULER automático de fondo (detección de novedades).
# NO aplica al botón "Actualizar ahora" del contador, que usa forzar=True
# y scrapea siempre, sin importar este valor.
FRESCURA_HORAS_DEFAULT = 3


def log(msg: str, nivel: str = "INFO") -> None:
    print(f"[{datetime.now(TZ_LIMA).strftime('%d/%m/%Y %H:%M:%S')}] "
          f"[{nivel}] {msg}", flush=True)


async def _contribuyentes_a_scrapear(session, ruc_filtro: str | None):
    q = (select(Contribuyente)
         .options(selectinload(Contribuyente.credencial))
         .where(Contribuyente.estado == EstadoContribuyente.ACTIVO))
    if ruc_filtro:
        q = q.where(Contribuyente.ruc == ruc_filtro)
    return list(await session.scalars(q))


def _scrapear_sync(ruc: str, usuario_sol: str, clave_sol: str,
                   conocidos: set | None = None,
                   anio_desde: int | None = None,
                   solo_censo: bool = False) -> dict:
    """Llama al scraper Playwright (sync) con las credenciales descifradas.
    conocidos (zAlerta-46): set de cod_mensaje ya en BD → lectura incremental.
    anio_desde (zAlerta-72): año más antiguo de deuda a descargar (por buzón).
    solo_censo (Tandas CCPL): lista y cuenta por año SIN descargar nada."""
    cfg = scraper.SunatConfig(
        ruc=ruc, usuario_sol=usuario_sol, clave_sol=clave_sol,
        headless=True,
    )
    return scraper.scrapear_ruc(cfg, conocidos=conocidos, anio_desde=anio_desde,
                                solo_censo=solo_censo)


async def procesar_contribuyente(session, contrib: Contribuyente,
                                 frescura_horas: int, forzar: bool,
                                 full: bool = False,
                                 solo_censo: bool = False) -> None:
    # ── Control de frescura: ¿hace falta tocar SUNAT? ──
    if not forzar and contrib.ultimo_scrapeo_ok and contrib.ultimo_scrapeo_at:
        antiguedad = datetime.now(TZ_LIMA) - contrib.ultimo_scrapeo_at
        if antiguedad < timedelta(hours=frescura_horas):
            horas = round(antiguedad.total_seconds() / 3600, 1)
            log(f"  {contrib.ruc}: fresco (scrapeado hace {horas}h), "
                f"sirvo desde BD.", "INFO")
            return

    cred = contrib.credencial
    if not cred or not cred.valida:
        log(f"  {contrib.ruc}: sin credencial válida, salteado.", "WARN")
        return

    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        log(f"  {contrib.ruc}: error descifrando clave: {e}", "ERROR")
        return

    # ── Decidir FULL vs INCREMENTAL (zAlerta-46) ──
    # Full si: se pidió (barrido nocturno de seguridad) o NUNCA hubo un full
    # exitoso (primer scan = base). Si no, incremental: solo lo nuevo.
    # solo_censo (Tandas CCPL): lista TODO (conocidos=None) para contar por año,
    # pero NO es un full de descarga y NO sella base.
    if solo_censo:
        hacer_full = False
        conocidos = None
    else:
        hacer_full = full or (contrib.ultimo_barrido_full_at is None)
        conocidos = None
        if not hacer_full:
            cods = await session.scalars(
                select(Notificacion.cod_mensaje_sunat).where(
                    Notificacion.contribuyente_id == contrib.id))
            conocidos = {str(c) for c in cods}
    modo = ("CENSO" if solo_censo else
            "FULL" if hacer_full else f"incremental ({len(conocidos)} conocidos)")
    log(f"  {contrib.ruc}: scrapeando [{modo}]...")

    # Año-desde de deuda POR BUZÓN (zAlerta-72): el scraper baja desde el año
    # CUBIERTO (piso de descarga); si no hay, default año_actual − 2.
    anio_desde = (contrib.anio_deuda_cubierto_desde
                  or contrib.anio_deuda_desde
                  or (datetime.now(TZ_LIMA).year - 2))

    # El scraper es sync (Playwright sync_api); lo corremos en un thread
    # para no bloquear el loop async.
    resultado = await asyncio.to_thread(
        _scrapear_sync, contrib.ruc, cred.usuario_sol, clave, conocidos,
        anio_desde, solo_censo)

    if not resultado.get("exito"):
        log(f"  {contrib.ruc}: scraping falló.", "ERROR")
        contrib.ultimo_scrapeo_at = datetime.now(TZ_LIMA)
        contrib.ultimo_scrapeo_ok = False
        await session.commit()
        return

    # ── CENSO puro (Tandas CCPL): NO se ingesta (registrar notifs volvería a
    # TODO "conocido" y las tandas incrementales saltarían todo → nunca bajarían
    # PDFs). Solo se guarda la foto por año + una métrica, y se sale. ──
    if solo_censo:
        censo = resultado.get("censo") or {}
        contrib.censo_json = {str(k): v for k, v in censo.items()}
        contrib.censo_at = datetime.now(TZ_LIMA)
        met = resultado.get("metricas") or {}
        session.add(BarridoMetrica(
            contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
            modo="censo", peticiones=met.get("peticiones", 0),
            duracion_seg=met.get("duracion_seg"),
            docs_procesados=met.get("docs_procesados", 0),
            pdfs_descargados=0, limite_alcanzado=False, exito=True))
        await session.commit()
        total = sum(int(v) for v in censo.values()) if censo else 0
        log(f"  {contrib.ruc}: CENSO — {total} docs en {len(censo)} año(s), "
            f"{met.get('peticiones', 0)} peticiones, "
            f"{met.get('duracion_seg', 0)}s. (sin descargar)", "OK")
        return

    stats = await ingestar_resultado(
        session, contrib.estudio_id, contrib.id, resultado)
    # ── Métricas de barrido + censo (zAlerta-83): tablero de riesgo de ban ──
    met = resultado.get("metricas") or {}
    session.add(BarridoMetrica(
        contribuyente_id=contrib.id, estudio_id=contrib.estudio_id,
        modo=("full" if hacer_full else "incremental"),
        peticiones=met.get("peticiones", 0),
        duracion_seg=met.get("duracion_seg"),
        docs_procesados=met.get("docs_procesados", 0),
        pdfs_descargados=met.get("pdfs_descargados", 0),
        limite_alcanzado=bool(met.get("limite_alcanzado")),
        exito=True))
    # Censo del buzón: solo en FULL es completo. El incremental salta los
    # conocidos ANTES de contar, así que su censo es parcial → no sobrescribir.
    censo = resultado.get("censo")
    if censo and hacer_full:   # mapa {año: nº docs} — tamaño del buzón, sin descargar
        contrib.censo_json = {str(k): v for k, v in censo.items()}
        contrib.censo_at = datetime.now(TZ_LIMA)
    # Un FULL exitoso sella la base contra la que compara el incremental.
    if hacer_full:
        contrib.ultimo_barrido_full_at = datetime.now(TZ_LIMA)
    await session.commit()
    log(f"  {contrib.ruc}: OK [{modo}] — {stats['mensajes_nuevos']} nuevos, "
        f"{stats['mensajes_duplicados']} duplicados, "
        f"{stats['adjuntos_nuevos']} adjuntos nuevos.", "OK")


async def main(ruc_filtro: str | None = None, forzar: bool = False,
               frescura_horas: int = FRESCURA_HORAS_DEFAULT,
               solo_censo: bool = False) -> None:
    log("═══ alerta.pe — orquestador scraper → BD ═══")
    if solo_censo:
        log("modo CENSO: lista y cuenta por año SIN descargar (foto previa).", "WARN")
    elif forzar:
        log("modo FORZAR: se scrapea aunque esté fresco.", "WARN")
    async with get_session() as session:
        contribs = await _contribuyentes_a_scrapear(session, ruc_filtro)
        log(f"{len(contribs)} contribuyente(s) candidato(s).")
        for contrib in contribs:
            try:
                await procesar_contribuyente(
                    session, contrib, frescura_horas,
                    forzar=(forzar or solo_censo), solo_censo=solo_censo)
            except Exception as e:
                log(f"  {contrib.ruc}: error inesperado: {e}", "ERROR")
    log("Orquestación completa.", "OK")


if __name__ == "__main__":
    # Uso:
    #   python run_scraper.py                → todos, respeta frescura 12h
    #   python run_scraper.py <RUC>          → solo ese RUC, respeta frescura
    #   python run_scraper.py <RUC> forzar   → ignora frescura, scrapea sí o sí
    #   python run_scraper.py <RUC> censo    → foto por año SIN descargar (Tandas)
    ruc = None
    forzar = False
    solo_censo = False
    for arg in sys.argv[1:]:
        if arg.lower() in ("forzar", "--forzar", "-f"):
            forzar = True
        elif arg.lower() in ("censo", "--censo", "-c"):
            solo_censo = True
        else:
            ruc = arg
    asyncio.run(main(ruc, forzar, solo_censo=solo_censo))