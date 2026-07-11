"""
push_test.py — alerta.pe (zAlerta-63)
═══════════════════════════════════════════════════════════════════════
Envía un PUSH DE PRUEBA a las suscripciones activas de UNA sola persona
(por DNI) o de un usuario_id. Sirve para demos y diagnóstico: confirma que
la cadena VAPID → proveedor (FCM) → dispositivo → sw.js funciona, sin
esperar al horario agrupado y SIN spamear a las demás suscripciones.

Requiere las VAPID en el entorno (VAPID_PRIVATE_KEY, VAPID_CLAIM_EMAIL) —
las mismas que usa el worker. NO toca el flujo agrupado ni marca
notificado_push. NO es parte del ciclo normal del worker.

Uso:
    python push_test.py --dni 05393776
    python push_test.py --usuario <uuid>
    python push_test.py --dni 05393776 --title "Hola" --body "Prueba"
    python push_test.py --dni 05393776 --dry   # no envía, solo lista subs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from models import PushSuscripcion, Usuario
import push_service


def _url() -> str:
    u = os.environ["DATABASE_URL"]
    return (u.replace("postgresql+asyncpg://", "postgresql://")
             .replace("postgres://", "postgresql://")
             .replace("postgresql://", "postgresql+asyncpg://"))


async def main() -> None:
    ap = argparse.ArgumentParser(description="Push de prueba a UNA persona/usuario.")
    ap.add_argument("--dni", help="DNI de la persona (busca su usuario).")
    ap.add_argument("--usuario", help="usuario_id (UUID) directo.")
    ap.add_argument("--title", default="✅ alerta.pe — prueba de push")
    ap.add_argument("--body", default="Si ves esto, el push llega bien. (zAlerta-63)")
    ap.add_argument("--url", default="/resumen?from=push")
    ap.add_argument("--dry", action="store_true", help="Solo listar, no enviar.")
    args = ap.parse_args()

    if not args.dni and not args.usuario:
        ap.error("Pasa --dni o --usuario.")
    if not os.getenv("VAPID_PRIVATE_KEY"):
        print("✗ VAPID_PRIVATE_KEY no está en el entorno. No se puede enviar.")
        return

    eng = create_async_engine(_url())
    Sm = async_sessionmaker(eng, expire_on_commit=False)
    async with Sm() as session:
        # Resolver usuario(s) objetivo.
        if args.usuario:
            uids = [args.usuario]
        else:
            uids = [str(u) for u in (await session.scalars(
                select(Usuario.id).where(Usuario.dni == args.dni)))]
        if not uids:
            print(f"✗ No hay usuario con dni={args.dni} (¿solo tiene persona, sin fila en usuarios?).")
            return

        subs = list(await session.scalars(
            select(PushSuscripcion).where(
                PushSuscripcion.usuario_id.in_(uids),
                PushSuscripcion.activa.is_(True))))
        print(f"Objetivo: usuario(s)={uids}")
        print(f"Suscripciones activas: {len(subs)}")
        for s in subs:
            print(f"  · {str(s.id)[:8]}  {s.endpoint[:55]}…")
        if not subs:
            print("✗ Sin suscripciones activas para ese objetivo.")
            return
        if args.dry:
            print("(--dry: no se envió nada)")
            return

        payload = json.dumps({
            "title": args.title, "body": args.body,
            "url": args.url, "acciones": True,
        })
        ok = falla = 0
        for s in subs:
            status = await asyncio.to_thread(push_service._enviar_webpush_sync, s, payload)
            if status in (400, 404, 410):
                print(f"  ✗ {str(s.id)[:8]}  muerta (status {status})")
                falla += 1
            else:
                print(f"  ✓ {str(s.id)[:8]}  enviada (status {status or 'OK/201'})")
                ok += 1
        print(f"\nResultado: {ok} enviada(s), {falla} muerta(s). "
              f"Revisa el dispositivo — debe aparecer la notificación.")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
