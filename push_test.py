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
    ap.add_argument("--show-headers", action="store_true",
                    help="Imprime los headers reales que van a FCM (prueba Urgency: high) y no envía.")
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
            "tag": "alertape-buzon", "requiere": True,   # mismo camino que el push real
        })

        # Prueba de prioridad: muestra los headers EXACTOS que van a FCM (no envía).
        if args.show_headers:
            from pywebpush import webpush
            import re
            s = subs[0]
            cmd = webpush(
                subscription_info={"endpoint": s.endpoint,
                                   "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                data=payload, vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
                vapid_claims={"sub": f"mailto:{push_service._vapid_claim_email()}"},
                ttl=86400, headers={"Urgency": "high"}, curl=True)
            print("Headers reales hacia FCM:")
            for h in re.findall(r'-H "([^"]+)"', cmd):
                print("  ", h.split(",")[0][:80])
            print("→ Debe verse 'urgency: high'. (no se envió nada)")
            return

        # Envío real, con marca de tiempo para MEDIR la latencia hasta el celular.
        from datetime import datetime, timezone, timedelta
        t_lima = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        print(f"\n⏱  ENVIADO a las {t_lima:%H:%M:%S} (hora Lima). "
              f"Anota a qué hora aparece en el celular → esa diferencia es la latencia FCM→dispositivo.")
        ok = falla = 0
        for s in subs:
            status = await asyncio.to_thread(push_service._enviar_webpush_sync, s, payload)
            if status in (400, 404, 410):
                print(f"  ✗ {str(s.id)[:8]}  muerta (status {status})")
                falla += 1
            else:
                print(f"  ✓ {str(s.id)[:8]}  aceptada por FCM (201)")
                ok += 1
        print(f"\nResultado: {ok} aceptada(s) por FCM, {falla} muerta(s). "
              f"El '201' es solo que FCM la recibió; la latencia real se mide en el celular.")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
