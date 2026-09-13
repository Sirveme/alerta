"""
limpiar_demo_video.py — alerta.pe · borra la DATA DE DEMO del video
═══════════════════════════════════════════════════════════════════════
Elimina TODO lo ficticio del seed (`seed_demo_video.py`): el estudio demo con sus
contribuyentes/grupos/notificaciones/asignaciones + los asistentes y el contador
demo (por DNI). NO toca ninguna fila real (otras organizaciones, personas reales,
notificaciones reales): el borrado está acotado por id del estudio demo y por los
DNIs demo.

Uso:
    python limpiar_demo_video.py
"""
from __future__ import annotations
import asyncio, sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass
from sqlalchemy import select, delete, func
from db import get_session
from models import (EstudioContable, Persona, Acceso, Contribuyente, Grupo,
                    ContribuyenteGrupo, Asignacion, AsignacionGrupo, Notificacion,
                    Auditoria)

DEMO_ESTUDIO = "ESTUDIO DEMO — alerta.pe"
DEMO_DNIS = ["90000000", "90000001", "90000002", "90000003"]


async def main():
    async with get_session() as s:
        eids = list(await s.scalars(select(EstudioContable.id).where(EstudioContable.razon_social == DEMO_ESTUDIO)))
        pids = list(await s.scalars(select(Persona.id).where(Persona.dni.in_(DEMO_DNIS))))
        reales_antes = await s.scalar(select(func.count()).select_from(Contribuyente))

        borrado = {}
        if eids:
            for modelo, filtro in [
                (AsignacionGrupo, AsignacionGrupo.estudio_id.in_(eids)),
                (Asignacion, Asignacion.estudio_id.in_(eids)),
                (ContribuyenteGrupo, ContribuyenteGrupo.estudio_id.in_(eids)),
                (Notificacion, Notificacion.estudio_id.in_(eids)),
                (Grupo, Grupo.estudio_id.in_(eids)),
                (Contribuyente, Contribuyente.estudio_id.in_(eids)),
                (Acceso, Acceso.estudio_id.in_(eids)),
                (Auditoria, Auditoria.estudio_id.in_(eids)),
            ]:
                r = await s.execute(delete(modelo).where(filtro))
                borrado[modelo.__name__] = borrado.get(modelo.__name__, 0) + (r.rowcount or 0)
        if pids:
            for modelo, filtro in [
                (AsignacionGrupo, AsignacionGrupo.persona_asistente_id.in_(pids)),
                (Asignacion, Asignacion.persona_asistente_id.in_(pids)),
                (Acceso, Acceso.persona_id.in_(pids)),
                (Auditoria, Auditoria.persona_id.in_(pids)),
            ]:
                r = await s.execute(delete(modelo).where(filtro))
                borrado[modelo.__name__] = borrado.get(modelo.__name__, 0) + (r.rowcount or 0)
        if eids:
            r = await s.execute(delete(EstudioContable).where(EstudioContable.id.in_(eids)))
            borrado["EstudioContable"] = r.rowcount or 0
        if pids:
            r = await s.execute(delete(Persona).where(Persona.id.in_(pids)))
            borrado["Persona"] = r.rowcount or 0
        await s.commit()
        reales_despues = await s.scalar(select(func.count()).select_from(Contribuyente))

    print("═══ DEMO LIMPIADO ═══")
    for k, v in borrado.items():
        if v:
            print(f"  {k}: {v} borrado(s)")
    if not any(borrado.values()):
        print("  (no había data demo)")
    print(f"  Contribuyentes en BD: antes={reales_antes} después={reales_despues} "
          f"(reales intactos; solo se quitó el demo)")


if __name__ == "__main__":
    asyncio.run(main())
