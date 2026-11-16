# -*- coding: utf-8 -*-
"""
webapp/demo_mask.py — alerta.pe · Modo demostración (server-side, decisión 2A)
═══════════════════════════════════════════════════════════════════════
Para grabar videos del "Club de Contadores" sin exponer datos reales. El toggle
vive en COOKIES (persiste al navegar); el enmascarado se aplica EN EL SERVIDOR, así
sale igual en la plantilla y en el Excel descargado.

Dos interruptores independientes:
  · club_demo        = '1' → enmascara identidades: RUC y razón social del CLIENTE y
                             de los PROVEEDORES.
  · club_demo_montos = '1' → además enmascara los MONTOS (casilla aparte, opcional).

No cambia datos ni toca BD: solo transforma texto para mostrar. Si el modo está
apagado, devuelve los valores tal cual.
"""
from __future__ import annotations

COOKIE_DEMO = "club_demo"
COOKIE_DEMO_MONTOS = "club_demo_montos"
_PLACEHOLDER_NOMBRE = "Cliente demostración"
_MASK_MONTO = "S/ •••"


def demo_activo(request) -> bool:
    try:
        return request.cookies.get(COOKIE_DEMO) == "1"
    except Exception:
        return False


def demo_montos(request) -> bool:
    """Los montos solo se enmascaran si el modo demo está activo Y la casilla de
    montos está marcada."""
    try:
        return (request.cookies.get(COOKIE_DEMO) == "1"
                and request.cookies.get(COOKIE_DEMO_MONTOS) == "1")
    except Exception:
        return False


def estado(request) -> dict:
    """Estado para la plantilla (botón + casilla)."""
    return {"demo": demo_activo(request), "montos": demo_montos(request)}


# ───────────────────────── enmascarado ─────────────────────────
def mask_ruc(ruc, on: bool) -> str:
    """RUC → '20•••••••81' (conserva los 2 primeros y 2 últimos). Fuera de demo,
    tal cual."""
    s = str(ruc or "")
    if not on or len(s) < 5:
        return s
    return s[:2] + "•" * (len(s) - 4) + s[-2:]


def mask_nombre(nombre, on: bool, etiqueta: str | None = None) -> str:
    """Razón social → iniciales enmascaradas ('EMBOTELLADORA LA SELVA' → 'E░ L░ S░').
    `etiqueta` permite un placeholder fijo (p.ej. el cliente protagonista)."""
    s = str(nombre or "").strip()
    if not on or not s:
        return s or (etiqueta or _PLACEHOLDER_NOMBRE if on else s)
    if etiqueta:
        return etiqueta
    palabras = [p for p in s.split() if p]
    if not palabras:
        return _PLACEHOLDER_NOMBRE
    return " ".join((p[0].upper() + "░") for p in palabras[:4])


def mask_monto(valor, on: bool):
    """Monto → 'S/ •••' si se enmascaran montos; si no, devuelve el valor tal cual
    (para que la plantilla/Excel lo formatee)."""
    if on:
        return _MASK_MONTO
    return valor


def aplicar_a_item(fila: dict, demo: bool, montos: bool) -> dict:
    """Devuelve una COPIA de una fila de la vista de Compras con las identidades y
    (opcional) montos enmascarados. Claves esperadas: ruc_proveedor,
    razon_social_proveedor, y los campos de monto en `campos_monto`."""
    out = dict(fila)
    if demo:
        if "ruc_proveedor" in out:
            out["ruc_proveedor"] = mask_ruc(out.get("ruc_proveedor"), True)
        if "razon_social_proveedor" in out:
            out["razon_social_proveedor"] = mask_nombre(
                out.get("razon_social_proveedor"), True)
    if montos:
        for c in ("valor_unitario", "importe_total", "igv", "base_gravada",
                  "descuento", "total"):
            if c in out and out[c] is not None:
                out[c] = _MASK_MONTO
    return out
