"""
consulta_ahora.py — alerta.pe   (C:\\alertape\\consulta_ahora.py)
═══════════════════════════════════════════════════════════════════════
Scraping BAJO DEMANDA: el botón "Actualizar ahora" del contador.

A diferencia del scheduler de fondo (run_scraper.py, que respeta frescura),
esta función scrapea UN contribuyente AL INSTANTE, sin importar cuándo fue
el último scrapeo. El contador nunca recibe "hay que esperar".

Flujo:
    consultar_ahora(estudio_id, contribuyente_id)
      → scrapea ya → ingesta con dedup → devuelve notificaciones frescas

Diseñado para llamarse desde un endpoint FastAPI:
    POST /contribuyentes/{id}/actualizar  → consultar_ahora(...)

Devuelve dict con stats + las notificaciones (para mostrar al instante).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from db import get_session
from models import Contribuyente, Notificacion
from cifrado import descifrar_clave_sol
from ingesta import ingestar_resultado

import scraper_sunat_playwgth as scraper

TZ_LIMA = ZoneInfo("America/Lima")


def _scrapear_sync(ruc: str, usuario_sol: str, clave_sol: str) -> dict:
    cfg = scraper.SunatConfig(
        ruc=ruc, usuario_sol=usuario_sol, clave_sol=clave_sol, headless=True)
    return scraper.scrapear_ruc(cfg)


async def consultar_ahora(
    estudio_id: uuid.UUID,
    contribuyente_id: uuid.UUID,
) -> dict:
    """Scrapea YA un contribuyente y devuelve sus notificaciones frescas.

    Returns:
        {
          "exito": bool,
          "stats": {mensajes_nuevos, mensajes_duplicados, ...},
          "notificaciones": [ {asunto, urgencia, fecha, leida, ...}, ... ],
          "error": str | None,
        }
    """
    async with get_session() as session:
        contrib = await session.scalar(
            select(Contribuyente)
            .options(selectinload(Contribuyente.credencial))
            .where(Contribuyente.id == contribuyente_id,
                   Contribuyente.estudio_id == estudio_id))  # filtro tenant
        if not contrib:
            return {"exito": False, "error": "Contribuyente no encontrado.",
                    "stats": None, "notificaciones": []}

        cred = contrib.credencial
        if not cred or not cred.valida:
            return {"exito": False, "error": "Sin credencial SOL válida.",
                    "stats": None, "notificaciones": []}

        try:
            clave = descifrar_clave_sol(cred.clave_sol_cifrada)
        except Exception as e:
            return {"exito": False, "error": f"Error de cifrado: {e}",
                    "stats": None, "notificaciones": []}

        # Scrapear AL INSTANTE (en thread para no bloquear el loop)
        resultado = await asyncio.to_thread(
            _scrapear_sync, contrib.ruc, cred.usuario_sol, clave)

        if not resultado.get("exito"):
            contrib.ultimo_scrapeo_at = datetime.now(TZ_LIMA)
            contrib.ultimo_scrapeo_ok = False
            await session.commit()
            return {"exito": False,
                    "error": "El scraping de SUNAT falló (reintentar).",
                    "stats": None, "notificaciones": []}

        stats = await ingestar_resultado(
            session, estudio_id, contribuyente_id, resultado)

        # Devolver las notificaciones frescas (para mostrar al instante)
        notifs = await session.scalars(
            select(Notificacion)
            .where(Notificacion.contribuyente_id == contribuyente_id)
            .order_by(desc(Notificacion.fecha_publica_sunat))
            .limit(50))
        notificaciones = [{
            "id": str(n.id),
            "asunto": n.asunto,
            "tipo_msj": n.tipo_msj,
            "urgencia": n.urgencia.value,
            "cant_adjuntos": n.cant_adjuntos,
            "fecha_envio": n.fecha_envio_sunat,
            "leida": n.leida,
        } for n in notifs]

        return {"exito": True, "error": None,
                "stats": stats, "notificaciones": notificaciones}


# Prueba directa por CLI:  python consulta_ahora.py <estudio_id> <contrib_id>
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Uso: python consulta_ahora.py <estudio_id> <contribuyente_id>")
        sys.exit(1)
    r = asyncio.run(consultar_ahora(
        uuid.UUID(sys.argv[1]), uuid.UUID(sys.argv[2])))
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))