# -*- coding: utf-8 -*-
"""
cronograma.py — alerta.pe  (proyecto decl-diag · estado "pagó fuera de plazo")

Lógica PURA (sin red, sin BD) del cronograma de vencimientos SUNAT. Dado el
conjunto de filas de `cronograma_vencimientos` para un (período, último dígito de
RUC), decide la fecha de vencimiento APLICABLE y clasifica el pago.

Regla de ORO (decisión de Duilio, 15/11/2026): **prefiero no alertar a alertar en
falso**. Si no sabemos si el RUC es Buen Contribuyente, se usa la fecha **MÁS
TARDÍA** (criterio conservador): un pago solo es "fuera de plazo" si cae después
del último vencimiento posible. Una sola alerta falsa y el contador deja de
confiar.

alerta.pe **informa**: compara SOLO fechas. NUNCA dice "debe", NUNCA calcula
intereses ni multas.
"""
from __future__ import annotations

from datetime import date

# Grupos del cronograma. 'general' = el grueso; los otros vencen DESPUÉS.
GRUPO_GENERAL = "general"
GRUPOS_BUEN_CONTRIBUYENTE = ("buenos_contribuyentes", "uesp")

# Estados del pago (claves; el copy va en la vista).
PAGO_DENTRO = "pago_dentro"          # pagó en o antes del vencimiento
PAGO_FUERA = "pago_fuera"            # pagó después del vencimiento
SIN_PAGO = "sin_pago"                # declaró, no hay pago en la fuente
SIN_VENCIMIENTO = "sin_vencimiento"  # período sin cronograma cargado → no se evalúa


def digito_ruc(ruc: str | None) -> int | None:
    """Último dígito del RUC (0..9). None si no es un RUC de 11 dígitos."""
    if not ruc:
        return None
    r = str(ruc).strip()
    if not (r.isdigit() and len(r) == 11):
        return None
    return int(r[-1])


def es_buen_contribuyente(ruc, rucs_bc_vigentes) -> bool | None:
    """¿El RUC es Buen Contribuyente? Decide desde el padrón ya cargado.
      - `rucs_bc_vigentes` = set de RUCs BC vigentes → True/False.
      - `rucs_bc_vigentes` = None (NO hay padrón cargado) → **None**: se aplica la
        regla CONSERVADORA (fecha más tardía), nunca se asume que no lo es."""
    if rucs_bc_vigentes is None or not ruc:
        return None
    return str(ruc).strip() in rucs_bc_vigentes


def _fecha(v):
    """Normaliza una fila: acepta dict o atributo `fecha_vencimiento` (date)."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return None


def _fila_fecha(fila) -> date | None:
    """Extrae la fecha de vencimiento de una fila (dict u ORM)."""
    if isinstance(fila, dict):
        return _fecha(fila.get("fecha_vencimiento"))
    return _fecha(getattr(fila, "fecha_vencimiento", None))


def _fila_campo(fila, campo, defecto=None):
    if isinstance(fila, dict):
        v = fila.get(campo, defecto)
    else:
        v = getattr(fila, campo, defecto)
    return defecto if v is None else v


def _fila_grupo(fila) -> str:
    return _fila_campo(fila, "grupo", GRUPO_GENERAL) or GRUPO_GENERAL


def _fila_ambito(fila) -> str:
    return _fila_campo(fila, "ambito", "general") or "general"


def _fila_vigente(fila) -> bool:
    # Si la fila no trae 'vigente' (dicts de prueba simples), se asume vigente.
    return bool(_fila_campo(fila, "vigente", True))


def vencimiento_aplicable(filas, es_buen_contribuyente: bool | None = None,
                          ambito_no_aplica=None) -> date | None:
    """Fecha de vencimiento a usar para un (período, dígito), dadas sus `filas`
    (base + prórrogas; posiblemente varias por celda).

    Solo se consideran las filas **vigentes** (una prórroga posterior reemplaza a
    la base: la superada queda vigente=False). Regla:
      - es_buen_contribuyente is True  → grupo Buen Contribuyente/UESP (si existe).
      - es_buen_contribuyente is False → grupo 'general'.
      - None (NO sabemos)              → la fecha MÁS TARDÍA (conservador).
    PRÓRROGAS con ámbito acotado (zona/tamaño): si NO podemos determinar que una
    prórroga aplica al RUC, se **incluye** (y el max la toma) → conservador. Solo
    se excluye un ámbito que sabemos que NO aplica (`ambito_no_aplica`: set de
    códigos de ámbito confirmados como no aplicables a este RUC).
    Devuelve None si no hay ninguna fila vigente con fecha (→ SIN_VENCIMIENTO)."""
    no_aplica = set(ambito_no_aplica or ())
    cand = []
    for f in (filas or []):
        d = _fila_fecha(f)
        if d is None or not _fila_vigente(f):
            continue
        if _fila_ambito(f) in no_aplica:          # confirmado que NO aplica
            continue
        cand.append((_fila_grupo(f), d))
    if not cand:
        return None

    if es_buen_contribuyente is True:
        bc = [d for g, d in cand if g in GRUPOS_BUEN_CONTRIBUYENTE]
        gen = [d for g, d in cand if g == GRUPO_GENERAL]
        return max(bc) if bc else (max(gen) if gen else max(d for _, d in cand))

    if es_buen_contribuyente is False:
        gen = [d for g, d in cand if g == GRUPO_GENERAL]
        return max(gen) if gen else max(d for _, d in cand)

    # Desconocido → la MÁS TARDÍA de las vigentes/aplicables (no alertar en falso).
    return max(d for _, d in cand)


def estado_pago(fecha_pago: date | None, vencimiento: date | None) -> str:
    """Clasifica comparando SOLO fechas. Sin importes, sin intereses."""
    if vencimiento is None:
        return SIN_VENCIMIENTO
    if fecha_pago is None:
        return SIN_PAGO
    return PAGO_DENTRO if fecha_pago <= vencimiento else PAGO_FUERA


def clasificar_pago(ruc: str | None, fecha_pago: date | None, filas_cronograma,
                    es_buen_contribuyente: bool | None = None,
                    ambito_no_aplica=None) -> dict:
    """Resultado listo para la vista (sin copy; la vista pone el texto honesto).
    `filas_cronograma` = filas de cronograma_vencimientos (base + prórrogas
    vigentes) para el período y el último dígito del RUC (vacío → SIN_VENCIMIENTO).
    `ambito_no_aplica` = ámbitos de prórroga confirmados como NO aplicables al RUC
    (se excluyen); el resto se incluye (conservador)."""
    venc = vencimiento_aplicable(filas_cronograma, es_buen_contribuyente,
                                 ambito_no_aplica)
    est = estado_pago(fecha_pago, venc)
    return {
        "estado": est,
        "vencimiento": venc,
        "fecha_pago": fecha_pago,
        "digito_ruc": digito_ruc(ruc),
        "conservador": es_buen_contribuyente is None,  # se usó la fecha más tardía
    }


# ── Autotest (sin BD): valida la regla conservadora con el caso real ──
if __name__ == "__main__":
    # RUC 10467016283 (dígito 3). Período 202605.
    # Supongamos cronograma 202605 dígito 3: general 16/06/2026, BC 23/06/2026.
    filas = [
        {"grupo": "general", "fecha_vencimiento": date(2026, 6, 16)},
        {"grupo": "buenos_contribuyentes", "fecha_vencimiento": date(2026, 6, 23)},
    ]
    pago = date(2026, 9, 25)   # pagó el 25/09/2026
    r = clasificar_pago("10467016283", pago, filas, es_buen_contribuyente=None)
    assert r["estado"] == PAGO_FUERA, r
    assert r["vencimiento"] == date(2026, 6, 23), r   # usó la MÁS TARDÍA
    assert r["conservador"] is True

    # Pago dentro de plazo (antes de la fecha más tardía).
    r2 = clasificar_pago("10467016283", date(2026, 6, 20), filas)
    assert r2["estado"] == PAGO_DENTRO, r2

    # Caso borde: pago el mismo día de la fecha conservadora → dentro.
    r3 = clasificar_pago("10467016283", date(2026, 6, 23), filas)
    assert r3["estado"] == PAGO_DENTRO, r3

    # Sin cronograma del período → no se evalúa (nunca se asume).
    r4 = clasificar_pago("10467016283", pago, [])
    assert r4["estado"] == SIN_VENCIMIENTO, r4

    # Declaró, sin pago → nunca "debe".
    r5 = clasificar_pago("10467016283", None, filas)
    assert r5["estado"] == SIN_PAGO, r5

    # Si SÍ sabemos que NO es Buen Contribuyente → usa general (16/06); 20/06 fuera.
    r6 = clasificar_pago("10467016283", date(2026, 6, 20), filas,
                         es_buen_contribuyente=False)
    assert r6["estado"] == PAGO_FUERA and r6["vencimiento"] == date(2026, 6, 16), r6

    # Padrón BC: resolución de es_buen_contribuyente.
    assert es_buen_contribuyente("10467016283", {"10467016283"}) is True
    assert es_buen_contribuyente("10467016283", set()) is False
    assert es_buen_contribuyente("10467016283", None) is None   # sin padrón → conservador

    # ── PRÓRROGAS ──
    # Base 16/06 + prórroga 30/06 (general, vigente). Sin saber si aplica → 30/06.
    filas_pr = [
        {"grupo": "general", "ambito": "general", "vigente": True,
         "fecha_vencimiento": date(2026, 6, 16)},
        {"grupo": "general", "ambito": "zona:loreto", "tipo": "prorroga",
         "vigente": True, "fecha_vencimiento": date(2026, 6, 30)},
    ]
    # Pago 25/06: con la prórroga incluida (conservador) → DENTRO (no alerta falsa).
    assert clasificar_pago("10467016283", date(2026, 6, 25), filas_pr)["estado"] == PAGO_DENTRO
    assert vencimiento_aplicable(filas_pr) == date(2026, 6, 30)
    # Si CONFIRMAMOS que la prórroga de zona NO aplica a este RUC → base 16/06 → FUERA.
    r_pr = clasificar_pago("10467016283", date(2026, 6, 25), filas_pr,
                           ambito_no_aplica={"zona:loreto"})
    assert r_pr["estado"] == PAGO_FUERA and r_pr["vencimiento"] == date(2026, 6, 16), r_pr
    # Una resolución superada (vigente=False) NO cuenta.
    filas_sup = [
        {"grupo": "general", "ambito": "general", "vigente": False,
         "fecha_vencimiento": date(2026, 6, 30)},   # superada
        {"grupo": "general", "ambito": "general", "vigente": True,
         "fecha_vencimiento": date(2026, 6, 16)},   # vigente
    ]
    assert vencimiento_aplicable(filas_sup) == date(2026, 6, 16)

    print("cronograma.py autotest OK")
