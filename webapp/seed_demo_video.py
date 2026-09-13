"""
seed_demo_video.py — alerta.pe · DATA DE DEMO para el video
═══════════════════════════════════════════════════════════════════════
Crea un ESTUDIO DEMO AUTOCONTENIDO y aislado:
  - Contador dueño demo + 3 asistentes ficticios (Ana, Beto, Carla).
  - Contribuyentes = tus CLIENTES REALES (razón social + RUC reales, con
    consentimiento) COPIADOS como filas nuevas EN EL ESTUDIO DEMO + varios
    clientes ficticios de variedad marcados "(Ejemplo)".
  - Grupos por rubro y régimen, con clientes asignados.
  - Notificaciones FICTICIAS y visiblemente marcadas ("Ficticia"/"Ejemplo"),
    con notificado_push=True (NUNCA disparan push a nadie).

SEGURIDAD: todo vive bajo el estudio demo (o personas demo por DNI). Las filas
REALES de tus clientes (en sus propias organizaciones) NO se tocan ni se leen para
escribir — solo se copian su razón social + RUC (datos públicos con consentimiento).
Limpieza total con `limpiar_demo_video.py`.

Re-ejecutable: borra el demo previo y lo recrea.

Uso:
    python seed_demo_video.py
"""
from __future__ import annotations
import asyncio, sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass
from datetime import timedelta
from sqlalchemy import select, delete
from db import get_session
from models import (
    EstudioContable, Persona, Acceso, Contribuyente, Grupo, ContribuyenteGrupo,
    Asignacion, AsignacionGrupo, Notificacion, Auditoria, RolUsuario, TipoCuenta,
    EstadoContribuyente, TipoDocumento, Urgencia, ahora_lima,
)
from webapp.auth import hash_clave

# ── Marcadores (para limpiar sin ambigüedad) ──
DEMO_ESTUDIO = "ESTUDIO DEMO — alerta.pe"
DEMO_DNIS = ["90000000", "90000001", "90000002", "90000003"]  # contador + Ana/Beto/Carla
DEMO_CLAVE = "demo1234"          # clave del contador demo (para grabar el video)
COD_PREFIJO = "DEMO-"            # cod_mensaje de notifs ficticias

# ── Clientes REALES (razón social + RUC reales, consentidos) ──
REALES = [
    ("COLEGIO DE CONTADORES PUBLICOS DE LORETO", "20103830991"),
    ("OFICINAS & DEPAS EIRL", "20600488121"),
    ("PERU SISTEMAS PRO E.I.R.L.", "20615446565"),
    ("MACRO INVERSIONES TECNOLOGICAS AURORA S.A.C.", "20616219686"),
    ("MULTISERVICIOS SHEVALCHE E.I.R.L.", "20615643735"),
    ("CASTILLO FLORIAN MILTON ERNANI", "10736459791"),
    ("RESTUCCIA ESLAVA DUILIO CESAR", "10053937760"),
]
# ── Clientes FICTICIOS de variedad (marcados "(Ejemplo)") ──  razon, ruc, rubro, régimen
FICTICIOS = [
    ("Restaurante El Sabor Norteño (Ejemplo)", "20999000011", "Restaurantes", "RER"),
    ("Cevichería La Marea (Ejemplo)", "20999000012", "Restaurantes", "RER"),
    ("Pollería Brasa Real (Ejemplo)", "20999000013", "Restaurantes", "RMYPE"),
    ("Ferretería Construmax (Ejemplo)", "20999000014", "Ferreterías", "RMYPE"),
    ("Ferretería El Perno (Ejemplo)", "20999000015", "Ferreterías", "Régimen General"),
    ("Boutique Tacna Moda (Ejemplo)", "20999000016", "Comercio", "Régimen General"),
    ("Farmacia Vida Sana (Ejemplo)", "20999000017", "Salud", "RER"),
    ("Transportes Andinos (Ejemplo)", "20999000018", "Transporte", "Régimen General"),
    ("Café Amazonía (Ejemplo)", "20999000019", "Restaurantes", "NRUS"),
]
# Rubro/régimen de los reales (para agruparlos también).  ruc → (rubro, régimen)
REAL_RUBRO = {
    "20103830991": ("Servicios", "Régimen General"),
    "20600488121": ("Servicios", "Régimen General"),
    "20615446565": ("Servicios", "RMYPE"),
    "20616219686": ("Comercio", "Régimen General"),
    "20615643735": ("Comercio", "RMYPE"),
    "10736459791": ("Servicios", "RER"),
    "10053937760": ("Servicios", "RER"),
}
GRUPOS_RUBRO = {"Restaurantes": "#F0623C", "Ferreterías": "#F0A93C",
                "Comercio": "#5B8DEF", "Salud": "#3DD68C", "Transporte": "#8B7CF0",
                "Servicios": "#22D3D2"}
GRUPOS_REGIMEN = {"NRUS": "#6B7689", "RER": "#5EEA8C", "RMYPE": "#22D3D2",
                  "Régimen General": "#8B7CF0"}
# Plantillas de notificaciones FICTICIAS (asunto, tipo, urgencia)
NOTIFS = [
    ("Esquela Ficticia N° 154-2025 (Ejemplo)", TipoDocumento.ESQUELA, Urgencia.IMPORTANTE),
    ("Orden de Pago Ficticia N° 145-2026 por S/1.50 (Ejemplo)", TipoDocumento.ORDEN_PAGO, Urgencia.URGENTE),
    ("Resolución de Multa Ficticia N° 022-2026 (Ejemplo)", TipoDocumento.MULTA, Urgencia.URGENTE),
    ("Cobranza Coactiva Ficticia N° 007-2026 (Ejemplo)", TipoDocumento.COBRANZA_COACTIVA, Urgencia.CRITICA),
    ("Aviso (Ejemplo): Registro de Compras RVIE período 2026-06", TipoDocumento.AVISO, Urgencia.INFORMATIVA),
    ("Aviso (Ejemplo): Constancia de presentación PDT", TipoDocumento.AVISO, Urgencia.INFORMATIVA),
]


async def _borrar_demo(s):
    """Borra TODO lo demo (estudio + personas demo), sin tocar nada real."""
    eids = list(await s.scalars(select(EstudioContable.id).where(EstudioContable.razon_social == DEMO_ESTUDIO)))
    pids = list(await s.scalars(select(Persona.id).where(Persona.dni.in_(DEMO_DNIS))))
    if eids:
        await s.execute(delete(AsignacionGrupo).where(AsignacionGrupo.estudio_id.in_(eids)))
        await s.execute(delete(Asignacion).where(Asignacion.estudio_id.in_(eids)))
        await s.execute(delete(ContribuyenteGrupo).where(ContribuyenteGrupo.estudio_id.in_(eids)))
        await s.execute(delete(Notificacion).where(Notificacion.estudio_id.in_(eids)))
        await s.execute(delete(Grupo).where(Grupo.estudio_id.in_(eids)))
        await s.execute(delete(Contribuyente).where(Contribuyente.estudio_id.in_(eids)))
        await s.execute(delete(Acceso).where(Acceso.estudio_id.in_(eids)))
        await s.execute(delete(Auditoria).where(Auditoria.estudio_id.in_(eids)))
    if pids:
        await s.execute(delete(AsignacionGrupo).where(AsignacionGrupo.persona_asistente_id.in_(pids)))
        await s.execute(delete(Asignacion).where(Asignacion.persona_asistente_id.in_(pids)))
        await s.execute(delete(Acceso).where(Acceso.persona_id.in_(pids)))
        await s.execute(delete(Auditoria).where(Auditoria.persona_id.in_(pids)))
        await s.execute(delete(Persona).where(Persona.id.in_(pids)))
    if eids:
        await s.execute(delete(EstudioContable).where(EstudioContable.id.in_(eids)))


async def main():
    ahora = ahora_lima()
    async with get_session() as s:
        reales_antes = await s.scalar(select(__import__("sqlalchemy").func.count()).select_from(Contribuyente))
        await _borrar_demo(s)     # re-ejecutable
        await s.commit()

        # 1) Estudio demo + contador dueño + 3 asistentes
        est = EstudioContable(razon_social=DEMO_ESTUDIO, tipo_cuenta=TipoCuenta.ESTUDIO.value,
                              estado="activo", segmento="estudio")
        s.add(est); await s.flush()
        contador = Persona(dni="90000000", nombre_completo="Contador Demo (video)",
                           clave_hash=hash_clave(DEMO_CLAVE), debe_cambiar_clave=False, sesion_version=1)
        ana = Persona(dni="90000001", nombre_completo="Ana Quispe (Demo)", clave_hash=hash_clave(DEMO_CLAVE), debe_cambiar_clave=False, sesion_version=1)
        beto = Persona(dni="90000002", nombre_completo="Beto Ríos (Demo)", clave_hash=hash_clave(DEMO_CLAVE), debe_cambiar_clave=False, sesion_version=1)
        carla = Persona(dni="90000003", nombre_completo="Carla Núñez (Demo)", clave_hash=hash_clave(DEMO_CLAVE), debe_cambiar_clave=False, sesion_version=1)
        s.add_all([contador, ana, beto, carla]); await s.flush()
        hoy = ahora.date()
        s.add(Acceso(persona_id=contador.id, estudio_id=est.id, rol=RolUsuario.CONTADOR_DUENO, vigencia_inicio=hoy, es_solo_lectura=False))
        for a in (ana, beto, carla):
            s.add(Acceso(persona_id=a.id, estudio_id=est.id, rol=RolUsuario.ASISTENTE, vigencia_inicio=hoy, es_solo_lectura=True))

        # 2) Grupos (rubro + régimen)
        grupos = {}
        orden = 0
        for nombre, color in list(GRUPOS_RUBRO.items()) + list(GRUPOS_REGIMEN.items()):
            g = Grupo(estudio_id=est.id, nombre=nombre, color=color, orden=orden); orden += 1
            s.add(g); grupos[nombre] = g
        await s.flush()

        # 3) Contribuyentes (reales copiados + ficticios) + membresía a grupos
        contribs = []   # (contrib, rubro, regimen)
        for razon, ruc in REALES:
            rubro, regimen = REAL_RUBRO.get(ruc, ("Servicios", "Régimen General"))
            c = Contribuyente(estudio_id=est.id, ruc=ruc, razon_social=razon, estado=EstadoContribuyente.ACTIVO)
            s.add(c); contribs.append((c, rubro, regimen))
        for razon, ruc, rubro, regimen in FICTICIOS:
            c = Contribuyente(estudio_id=est.id, ruc=ruc, razon_social=razon, estado=EstadoContribuyente.ACTIVO)
            s.add(c); contribs.append((c, rubro, regimen))
        await s.flush()
        for c, rubro, regimen in contribs:
            for gn in (rubro, regimen):
                if gn in grupos:
                    s.add(ContribuyenteGrupo(contribuyente_id=c.id, grupo_id=grupos[gn].id, estudio_id=est.id))

        # 4) Notificaciones FICTICIAS (marcadas, notificado_push=True → sin push)
        n_notif = 0
        for i, (c, _, _) in enumerate(contribs):
            # 1–3 notifs variadas por cliente (rotando la plantilla)
            cuantas = 1 + (i % 3)
            for k in range(cuantas):
                asunto, td, urg = NOTIFS[(i + k) % len(NOTIFS)]
                n_notif += 1
                s.add(Notificacion(
                    estudio_id=est.id, contribuyente_id=c.id,
                    cod_mensaje_sunat=f"{COD_PREFIJO}{n_notif:04d}", tipo_msj=1, fuente="sunat",
                    asunto=asunto, tipo_documento_enum=td, urgencia=urg,
                    fecha_publica_sunat=ahora - timedelta(days=(k * 3 + i % 5)),
                    leida=False, notificado_push=True, notificado_push_at=ahora,
                    clasificado_at=ahora))
        await s.commit()

        reales_despues = await s.scalar(select(__import__("sqlalchemy").func.count()).select_from(Contribuyente))
        n_contribs = len(contribs)

    print("═══ DEMO SEEDED ═══")
    print(f"  Estudio demo: {DEMO_ESTUDIO}")
    print(f"  Login contador demo → DNI 90000000 · clave {DEMO_CLAVE}")
    print(f"  Asistentes: Ana (90000001), Beto (90000002), Carla (90000003) · clave {DEMO_CLAVE}")
    print(f"  Contribuyentes en el demo: {n_contribs} ({len(REALES)} reales + {len(FICTICIOS)} ejemplo)")
    print(f"  Grupos: {len(grupos)} (rubro + régimen) · Notificaciones ficticias: {n_notif}")
    print(f"  Contribuyentes en BD antes={reales_antes} después={reales_despues} (+{reales_despues-reales_antes} = solo demo)")
    print("  Limpieza: python limpiar_demo_video.py")


if __name__ == "__main__":
    asyncio.run(main())
