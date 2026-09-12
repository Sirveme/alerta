"""
diag_push_11.py — DIAGNÓSTICO SOLO-LECTURA del push "11 avisos nuevos".
NO escribe nada. Correr: PYTHONIOENCODING=utf-8 python diag_push_11.py
"""
import asyncio
import os
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select, func
from db import get_session
from models import Notificacion, Contribuyente, ahora_lima, TZ_LIMA

RUC = "20103830991"


def _fmt(dt, f="%Y-%m-%d %H:%M"):
    return dt.astimezone(TZ_LIMA).strftime(f) if dt else "—"


async def main():
    u = urlparse(os.getenv("DATABASE_URL", ""))
    print(f"BD → {u.hostname}:{u.port}/{(u.path or '').lstrip('/')}")
    ahora = ahora_lima()
    hoy0 = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"Ahora (Lima): {ahora:%Y-%m-%d %H:%M}\n")

    async with get_session() as session:
        c = await session.scalar(select(Contribuyente).where(Contribuyente.ruc == RUC))
        print(f"Contribuyente {RUC} · {getattr(c,'razon_social','?')}")
        print(f"  ultimo_scrapeo_at   = {_fmt(getattr(c,'ultimo_scrapeo_at',None))}")
        print(f"  ultimo_barrido_full = {_fmt(getattr(c,'ultimo_barrido_full_at',None))}")
        print(f"  actualizar_solicit. = {getattr(c,'actualizar_solicitado',None)}\n")

        # Totales SUNAT de este RUC
        tot = await session.scalar(select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == c.id, Notificacion.fuente == "sunat"))
        pend = await session.scalar(select(func.count(Notificacion.id)).where(
            Notificacion.contribuyente_id == c.id, Notificacion.fuente == "sunat",
            Notificacion.notificado_push.is_(False)))
        print(f"SUNAT notifs de este RUC: {tot}  ·  pendientes (push=False): {pend}\n")

        # Las anunciadas hoy, con creado_at REAL
        anun = list(await session.scalars(
            select(Notificacion)
            .where(Notificacion.contribuyente_id == c.id,
                   Notificacion.notificado_push_at >= hoy0)
            .order_by(Notificacion.creado_at)))
        print(f"── {len(anun)} ANUNCIADAS HOY (creado_at = cuándo entró a la BD) ──")
        for n in anun:
            print(f"  cod={n.cod_mensaje_sunat:<12} fpub={_fmt(n.fecha_publica_sunat,'%Y-%m-%d')}"
                  f"  CREADA={_fmt(n.creado_at)}  push_at={_fmt(n.notificado_push_at,'%m-%d %H:%M')}"
                  f"  leida={n.leida}  tipo={n.tipo_documento_enum}")

        # ¿Cuándo se creó el grueso del histórico de este RUC? (distribución por día)
        print("\n── creado_at de TODO el histórico SUNAT de este RUC (por día) ──")
        filas = list(await session.scalars(
            select(Notificacion.creado_at).where(
                Notificacion.contribuyente_id == c.id, Notificacion.fuente == "sunat")))
        from collections import Counter
        dist = Counter(_fmt(f, "%Y-%m-%d") for f in filas)
        for dia in sorted(dist):
            print(f"  {dia}: {dist[dia]}")


if __name__ == "__main__":
    asyncio.run(main())
