# -*- coding: utf-8 -*-
"""
guardian_aceptacion.py — alerta.pe  (SEGURIDAD, transversal)
═══════════════════════════════════════════════════════════════════════════
REGLA DURA: alerta.pe solo NAVEGA y CONSULTA en SOL/SUNAT. NUNCA marca casillas,
acepta, continúa, registra ni presenta nada a nombre del contribuyente.

Este guardián detecta una pantalla de ACEPTACIÓN/CONSENTIMIENTO (p.ej. "Declaro
tener conocimiento y acepto las condiciones para ser Generador Electrónico", que es
una condición PERMANENTE). Ante cualquiera de esas señales, el llamador debe
ABORTAR esa pantalla de inmediato, SIN hacer clic en nada, y reportar
`pantalla_de_aceptacion`. Se usa en TODOS los modos (diag y worker).

No hace clics ni cambia estado: solo LEE texto/estructura y responde sí/no.
"""
from __future__ import annotations

import re

# Señales de texto de una pantalla de aceptación/consentimiento (minúsculas, sin
# tildes ya normalizadas por el llamador o cubiertas con variantes).
MARCAS_ACEPTACION = (
    "declaro", "acepto", "aceptación de", "aceptacion de",
    "generador electrónico", "generador electronico", "generador de comprobantes",
    "condiciones para ser", "términos y condiciones", "terminos y condiciones",
    "doy mi consentimiento", "consentimiento informado",
    "declaro tener conocimiento", "declaro conocer", "declaro bajo juramento",
    "afiliación", "afiliacion", "autorizo el", "autorizo a",
)
_RE_ESPACIOS = re.compile(r"\s+")


def texto_es_aceptacion(texto: str) -> str | None:
    """Devuelve la marca encontrada (para reportar) o None. `texto` debe venir en
    minúsculas; se normalizan espacios."""
    if not texto:
        return None
    t = _RE_ESPACIOS.sub(" ", texto.lower())
    for m in MARCAS_ACEPTACION:
        if m in t:
            return m
    return None


def _texto_y_checkbox(page) -> tuple[str, bool]:
    """Junta el texto visible de la página + sus frames y detecta si hay un checkbox
    (señal típica de 'marcar para aceptar'). No interactúa."""
    partes: list[str] = []
    hay_checkbox = False
    scopes = [page]
    try:
        scopes += list(page.frames)
    except Exception:
        pass
    vistos = set()
    for sc in scopes:
        key = id(sc)
        if key in vistos:
            continue
        vistos.add(key)
        try:
            partes.append(sc.inner_text("body"))
        except Exception:
            pass
        try:
            if sc.query_selector("input[type=checkbox]"):
                hay_checkbox = True
        except Exception:
            pass
    return " ".join(partes), hay_checkbox


def detectar_aceptacion(page) -> tuple[bool, str | None]:
    """Escanea la página (y frames). Devuelve (es_aceptacion, detalle).
    es_aceptacion=True si hay una marca de consentimiento, o un checkbox JUNTO a
    palabras de aceptación. NUNCA hace clic. El llamador debe abortar la pantalla."""
    try:
        texto, hay_checkbox = _texto_y_checkbox(page)
    except Exception:
        return (False, None)
    marca = texto_es_aceptacion(texto)
    if marca:
        return (True, f"marca='{marca}'" + (" + checkbox" if hay_checkbox else ""))
    # Checkbox + palabra de aceptación suelta (acepto/aceptar/condiciones).
    if hay_checkbox and re.search(r"\b(acept\w+|condiciones|declaro)\b",
                                  (texto or "").lower()):
        return (True, "checkbox de aceptación")
    return (False, None)
