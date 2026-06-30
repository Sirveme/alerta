"""
validar_dryrun_zAlerta36.py — alerta.pe   (SOLO LECTURA)
═══════════════════════════════════════════════════════════════════════
Valida el dry-run del flujo nuevo (zAlerta-34/35/36) para el RUC de prueba.
NO escribe nada. Correr ANTES (baseline) y DESPUÉS de pulsar "Actualizar ahora".

Cubre los 6 puntos de zAlerta-36 TAREA 3:
  1. Paginación (nº notificaciones por bandeja).
  2. documentos_valorados (filas, gcs_key, pdf_texto, parseado_ok).
  3. gcs_key presentes (la existencia en el bucket se verifica aparte/Railway).
  4. Integridad por tipo (num_documento en pdf_texto).
  5. Conteos de control (pendientes/integridad — del log del worker).
  6. Self-check (del log del worker).

Uso:  python validar_dryrun_zAlerta36.py
"""

from __future__ import annotations

import asyncio
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import text
from db import engine

RUC = "20103830991"


async def main() -> None:
    async with engine.connect() as c:
        cid = (await c.execute(text(
            "SELECT id FROM contribuyentes WHERE ruc=:r LIMIT 1"), {"r": RUC})).scalar()
        print(f"contribuyente_id={cid}\n")

        print("===== 1. PAGINACIÓN — notificaciones por bandeja =====")
        rows = (await c.execute(text(
            "SELECT tipo_msj, count(*) FROM notificaciones "
            "WHERE contribuyente_id=:c GROUP BY tipo_msj ORDER BY tipo_msj"),
            {"c": cid})).all()
        for t, n in rows:
            etq = "Notificaciones" if t == 2 else "Mensajes" if t == 1 else "?"
            print(f"  tipo_msj={t} ({etq}): {n}")
        total = (await c.execute(text(
            "SELECT count(*) FROM notificaciones WHERE contribuyente_id=:c"),
            {"c": cid})).scalar()
        print(f"  TOTAL: {total}   (esperado ~651; antes ~40)\n")

        print("===== 2-3. documentos_valorados =====")
        dv = (await c.execute(text(
            "SELECT count(*) total, "
            "count(*) FILTER (WHERE gcs_key IS NOT NULL) con_gcs, "
            "count(*) FILTER (WHERE pdf_texto IS NOT NULL) con_texto, "
            "count(*) FILTER (WHERE parseado_ok) parseados "
            "FROM documentos_valorados WHERE contribuyente_id=:c"), {"c": cid})).first()
        print(f"  filas={dv[0]}  con_gcs_key={dv[1]}  con_pdf_texto={dv[2]}  parseado_ok={dv[3]}")
        por_tipo = (await c.execute(text(
            "SELECT tipo_valorado, count(*) FROM documentos_valorados "
            "WHERE contribuyente_id=:c GROUP BY tipo_valorado ORDER BY 2 DESC"),
            {"c": cid})).all()
        for tv, n in por_tipo:
            print(f"    {tv}: {n}")
        print()

        print("===== 4. INTEGRIDAD por tipo (num_documento dentro de pdf_texto) =====")
        muestras = (await c.execute(text(
            "SELECT DISTINCT ON (tipo_valorado) tipo_valorado, num_documento, "
            "left(pdf_texto, 4000) FROM documentos_valorados "
            "WHERE contribuyente_id=:c AND pdf_texto IS NOT NULL "
            "ORDER BY tipo_valorado, creado_at DESC"), {"c": cid})).all()
        if not muestras:
            print("  (sin documentos con pdf_texto todavía — corre el dry-run)")
        for tv, num, txt in muestras:
            ok = bool(num) and (num in (txt or ""))
            montos = re.findall(r"S/\s*[\d.,]+", txt or "")[:3]
            print(f"  [{tv}] num={num} integridad={'OK' if ok else 'REVISAR'} montos={montos}")
        print()

        print("===== 5-6. Control =====")
        print("  valorados_pendientes / integridad_error / self-check: ver LOG del worker")
        print("  (líneas 'SELF-CHECK', 'INTEGRIDAD', 'sin_goarchivo'). Esperado ~0 pendientes.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
