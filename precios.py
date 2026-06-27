"""
precios.py — alerta.pe (zAlerta-23/24)   (C:\\alertape\\precios.py)
═══════════════════════════════════════════════════════════════════════
FUENTE ÚNICA de la escalera de precios Fundador 2026. La usan: la landing
(mostrar el precio del mes), el CANDADO de precio al capturar el lead, y el
COBRO (monto a validar). NO duplicar la escalera en otros lados.

Escalera (precio según el mes de captura, hora Lima):
  2026-07 → S/5 · 08 → 10 · 09 → 15 · 10 → 20 · 11 → 25 · 12 → 30
  2027-01 en adelante → S/35 (regular).
Antes de 2026-07 (p.ej. junio): precio de entrada S/5 (Julio).
"""

from __future__ import annotations

# (año, mes, etiqueta, precio_soles). Whole soles (enteros).
PASOS_PRECIO = [
    (2026, 7, "Jul", 5), (2026, 8, "Ago", 10), (2026, 9, "Set", 15),
    (2026, 10, "Oct", 20), (2026, 11, "Nov", 25), (2026, 12, "Dic", 30),
    (2027, 1, "Ene 2027", 35),
]
PRECIO_ENTRADA = PASOS_PRECIO[0][3]      # S/5 (Julio)
PRECIO_REGULAR = PASOS_PRECIO[-1][3]     # S/35


def _indice_para(fecha) -> int:
    """Índice del escalón vigente para una fecha (date/datetime). Antes del
    primer mes → 0 (entrada); después del último → último."""
    ym = fecha.year * 12 + fecha.month
    claves = [y * 12 + m for (y, m, _, _) in PASOS_PRECIO]
    idx = 0
    for i, k in enumerate(claves):
        if k <= ym:
            idx = i
    if ym < claves[0]:
        idx = 0
    return idx


def precio_para_fecha(fecha) -> int:
    """Precio (soles, entero) vigente para la fecha dada (zona ya resuelta por
    el llamador). FUENTE ÚNICA del candado, la landing y el cobro."""
    return PASOS_PRECIO[_indice_para(fecha)][3]


def escalera_para(fecha) -> dict:
    """Para la landing: {pasos:[{mes,precio,estado}], precio_actual, mes_actual,
    precio_regular}. estado: 'pasado' | 'actual' | 'futuro'."""
    idx = _indice_para(fecha)
    pasos = []
    for i, (_, _, label, precio) in enumerate(PASOS_PRECIO):
        estado = "actual" if i == idx else ("pasado" if i < idx else "futuro")
        pasos.append({"mes": label, "precio": str(precio), "estado": estado})
    actual = PASOS_PRECIO[idx][3]
    return {"pasos": pasos, "precio_actual": str(actual),
            "mes_actual": PASOS_PRECIO[idx][2], "precio_regular": str(PRECIO_REGULAR),
            # Ahorro mensual frente al precio regular (zAlerta-25); 0 si ya es regular.
            "ahorro": max(0, PRECIO_REGULAR - actual)}
