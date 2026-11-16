# -*- coding: utf-8 -*-
"""
webapp/demo_mask.py — alerta.pe · Modo RESERVADO (vistas del Club)
═══════════════════════════════════════════════════════════════════════
Privacidad por defecto. Al entrar a una vista del Club, los **datos reservados**
(RUC y razón social del cliente y de los proveedores) salen **ENMASCARADOS**. El
contador los revela con una casilla visible ("Mostrar datos reservados"); hay una
casilla aparte para los montos. El estado se guarda en COOKIE DE SESIÓN, así que
**cada sesión nueva arranca enmascarada** (la cookie se borra al cerrar el navegador).

El enmascarado es server-side: sale igual en la pantalla y en el Excel descargado.

Cookies (de sesión, sin expiración persistente):
  · club_mostrar        = '1' → REVELAR identidades (si falta → ocultas, por defecto).
  · club_ocultar_montos = '1' → OCULTAR montos (si falta → visibles, por defecto).
"""
from __future__ import annotations

COOKIE_MOSTRAR = "club_mostrar"
COOKIE_OCULTAR_MONTOS = "club_ocultar_montos"
_PLACEHOLDER_NOMBRE = "•••••• reservado"
_MASK_MONTO = "S/ •••"

TOOLTIP_RESERVA = ("Por tu privacidad y la de tus clientes, los datos reservados se "
                   "muestran ocultos. Márcala solo cuando estés seguro de que nadie "
                   "más ve tu pantalla.")


# ───────────────────────── estado (desde cookies) ─────────────────────────
def mostrar_reservados(request) -> bool:
    """¿El usuario pidió REVELAR identidades? (por defecto NO)."""
    try:
        return request.cookies.get(COOKIE_MOSTRAR) == "1"
    except Exception:
        return False


def identidad_oculta(request) -> bool:
    """¿Se deben ocultar RUC/razón? Por defecto SÍ (modo reservado)."""
    return not mostrar_reservados(request)


def montos_ocultos(request) -> bool:
    """¿Se deben ocultar los montos? Por defecto NO."""
    try:
        return request.cookies.get(COOKIE_OCULTAR_MONTOS) == "1"
    except Exception:
        return False


def estado(request) -> dict:
    """Estado para la plantilla (casillas)."""
    return {"oculto": identidad_oculta(request),
            "mostrar": mostrar_reservados(request),
            "montos_ocultos": montos_ocultos(request)}


# ───────────────────────── enmascarado ─────────────────────────
def mask_ruc(ruc, oculto: bool) -> str:
    """RUC → '20•••••••81' (conserva 2 primeros y 2 últimos). Si no está oculto,
    tal cual."""
    s = str(ruc or "")
    if not oculto or len(s) < 5:
        return s
    return s[:2] + "•" * (len(s) - 4) + s[-2:]


def mask_nombre(nombre, oculto: bool, etiqueta: str | None = None) -> str:
    """Razón social → iniciales enmascaradas ('EMBOTELLADORA LA SELVA' → 'E░ L░ S░').
    `etiqueta` fija un placeholder (p.ej. el cliente protagonista)."""
    s = str(nombre or "").strip()
    if not oculto:
        return s
    if etiqueta:
        return etiqueta
    palabras = [p for p in s.split() if p]
    if not palabras:
        return _PLACEHOLDER_NOMBRE
    return " ".join((p[0].upper() + "░") for p in palabras[:4])


def mask_monto(valor, ocultos: bool):
    """Monto → 'S/ •••' si se ocultan montos; si no, devuelve el valor tal cual."""
    return _MASK_MONTO if ocultos else valor
