"""
test_copy_push.py — prueba de humo del frente del push (worker):
  · _copy_push   — título por razón social + copy 1/varias empresas.
  · _es_novedad  — guard de recencia (backlog histórico NO se anuncia).

NO toca BD ni red: usa stubs ligeros que imitan Notificacion y Contribuyente.
Correr:  PYTHONIOENCODING=utf-8 python test_copy_push.py
"""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from worker import _copy_push, _es_novedad, PUSH_VENTANA_BACKLOG
from models import TZ_LIMA


def notif(cid, asunto="", fuente="sunat"):
    return NS(contribuyente_id=cid, asunto=asunto, fuente=fuente)


def contrib(razon, ruc):
    return NS(razon_social=razon, ruc=ruc)


CASOS = [
    ("1 empresa · 1 aviso SUNAT (caso típico)",
     [notif("A", "Se registró Expediente MPV")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678")}, 0),

    ("1 empresa · 1 aviso con DEUDA",
     [notif("A", "Resolución de Cobranza Coactiva")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678")}, 1),

    ("1 empresa · 1 aviso SIN asunto (fallback)",
     [notif("A", "")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678")}, 0),

    ("1 empresa · razón social vacía → RUC",
     [notif("A", "Se registró Expediente MPV")],
     {"A": contrib(None, "20512345678")}, 0),

    ("1 empresa · varios avisos SUNAT",
     [notif("A", "aviso 1"), notif("A", "aviso 2"), notif("A", "aviso 3")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678")}, 2),

    ("1 empresa · varios avisos SUNAFIL",
     [notif("A", "x", "sunafil"), notif("A", "y", "sunafil")],
     {"A": contrib("CONSTRUCTORA ANDINA", "20698765432")}, 0),

    ("2 empresas (mismo destinatario)",
     [notif("A", "x"), notif("B", "y"), notif("B", "z")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678"),
      "B": contrib("CONSTRUCTORA ANDINA", "20698765432")}, 1),

    ("3 empresas → 'y N más'",
     [notif("A", "x"), notif("B", "y"), notif("C", "z"), notif("C", "w")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678"),
      "B": contrib("CONSTRUCTORA ANDINA", "20698765432"),
      "C": contrib("TEXTILES DEL SUR", "20611122233")}, 0),

    ("mixto SUNAT+SUNAFIL, 1 empresa",
     [notif("A", "x", "sunat"), notif("A", "y", "sunafil")],
     {"A": contrib("PERÚ SISTEMAS PRO", "20512345678")}, 0),
]


# ── _es_novedad ──────────────────────────────────────────────────────────────
# Regresión del bug real (2026-09-12): 11 docs de 2015 del RUC 20103830991 (CCPL)
# entraron a la BD hoy 00:01 y el push de las 06:01 los anunció como "11 nuevos".
# Con el guard, TODOS deben ser backlog (_es_novedad == False).
_HOY = datetime(2026, 9, 12, 9, 0, tzinfo=TZ_LIMA)
_CREADA_HOY = datetime(2026, 9, 12, 0, 1, tzinfo=TZ_LIMA)   # cuándo entraron a la BD


def _n(fecha_publica, creado_at=_CREADA_HOY):
    """Notif stub: solo los campos que mira _es_novedad."""
    return NS(fecha_publica_sunat=fecha_publica, creado_at=creado_at)


def _fpub(y, m, d):
    return datetime(y, m, d, tzinfo=TZ_LIMA)


# Los 11 cods REALES de hoy con su fecha_publica (de 2015) — todos backlog.
CODS_11 = {
    "87313861": _fpub(2015, 9, 15), "87292603": _fpub(2015, 9, 15),
    "86132101": _fpub(2015, 8, 31), "86132086": _fpub(2015, 8, 31),
    "85291404": _fpub(2015, 8, 17), "85290879": _fpub(2015, 8, 17),
    "83175132": _fpub(2015, 7, 15), "83174415": _fpub(2015, 7, 15),
    "80874512": _fpub(2015, 6, 15), "80874240": _fpub(2015, 6, 15),
    "78965773": _fpub(2015, 5, 18),
}


def test_es_novedad():
    fallos = []
    # 1) Los 11 reales: todos backlog (NO novedad).
    for cod, fpub in CODS_11.items():
        if _es_novedad(_n(fpub), _HOY) is not False:
            fallos.append(f"cod {cod} (fpub {fpub:%Y-%m-%d}) debió ser BACKLOG")
    # 2) Casos frontera y positivos.
    casos = [
        ("publicada HOY", _n(_HOY, _HOY), True),
        ("publicada hace 20d (dentro de ventana, worker con retraso)",
         _n(_HOY - timedelta(days=20), _HOY), True),
        ("publicada justo en el borde (30d)",
         _n(_HOY - PUSH_VENTANA_BACKLOG, _HOY), True),
        ("publicada hace 40d (fuera de ventana → backlog)",
         _n(_HOY - timedelta(days=40), _HOY), False),
        ("sin fecha_publica (no arriesgar → novedad)", _n(None), True),
    ]
    for desc, n, esperado in casos:
        got = _es_novedad(n, _HOY)
        if got != esperado:
            fallos.append(f"{desc}: esperaba {esperado}, dio {got}")

    print(f"\n── _es_novedad (ventana {PUSH_VENTANA_BACKLOG.days}d) ──")
    print(f"   11 cods reales de hoy → backlog: "
          f"{all(_es_novedad(_n(f), _HOY) is False for f in CODS_11.values())}")
    for desc, n, esperado in casos:
        print(f"   [{'OK' if _es_novedad(n, _HOY) == esperado else 'FALLA'}] {desc}")
    if fallos:
        raise AssertionError("test_es_novedad FALLÓ:\n  - " + "\n  - ".join(fallos))
    print("   ✓ todos los asserts pasan")


if __name__ == "__main__":
    for desc, nuevos, contribs, deuda in CASOS:
        titulo, body = _copy_push(nuevos, contribs, deuda)
        print(f"\n■ {desc}")
        print(f"   TÍTULO: {titulo}")
        print(f"   CUERPO: {body}")
    print()
    test_es_novedad()
    print()
