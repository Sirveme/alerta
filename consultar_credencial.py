# -*- coding: utf-8 -*-
"""
consultar_credencial.py — alerta.pe  (utilidad de SOLO LECTURA)
═══════════════════════════════════════════════════════════════════════
Dado un RUC (11 dígitos) o un fragmento de razón social, imprime el/los
contribuyentes que calzan y el USUARIO SOL registrado en su credencial (para
saber qué usuario usará el worker / decl-diag). NO escribe nada en la BD.

Usa DATABASE_PUBLIC_URL si existe (para correr en local con `railway run`, igual
que decl-diag; el host interno de Railway no resuelve desde tu PC).

Uso (local):
    railway run -s alertape-worker python consultar_credencial.py "Automotriz Castillo"
    railway run -s alertape-worker python consultar_credencial.py 20615643735
"""
import asyncio
import os
import sys

# Solo-lectura, pero igual necesita la URL pública para conectar desde local.
if os.getenv("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
    print("[consultar] Conectando por DATABASE_PUBLIC_URL...")

from sqlalchemy import select  # noqa: E402

from db import get_session  # noqa: E402
from models import Contribuyente, CredencialSol  # noqa: E402


async def main(frag: str) -> None:
    frag = (frag or "").strip()
    async with get_session() as session:
        q = select(Contribuyente)
        if frag.isdigit() and len(frag) == 11:
            q = q.where(Contribuyente.ruc == frag)
        else:
            q = q.where(Contribuyente.razon_social.ilike(f"%{frag}%"))
        contribs = list(await session.scalars(q.limit(15)))
        if not contribs:
            print(f"Sin coincidencias para: {frag!r}")
            return
        for c in contribs:
            cred = await session.scalar(
                select(CredencialSol).where(
                    CredencialSol.contribuyente_id == c.id))
            print(f"\nRUC {c.ruc} | {c.razon_social or '(sin razón social)'} "
                  f"| estado={getattr(c.estado, 'value', c.estado)}")
            if cred:
                print(f"   usuario_sol = {cred.usuario_sol}")
                print(f"   tipo_usuario = {cred.tipo_usuario}  "
                      f"(1=principal, 2=secundario)")
                print(f"   valida = {cred.valida}  "
                      f"ultimo_login_ok_at = {cred.ultimo_login_ok_at}")
            else:
                print("   (SIN credencial registrada → decl-diag no podrá loguear)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('uso: python consultar_credencial.py "<RUC o fragmento de razón social>"')
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
