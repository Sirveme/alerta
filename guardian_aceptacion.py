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

PRECISIÓN (no relajar, afinar): el menú de SOL y el workspace repiten palabras
sueltas ("afiliación", "autorizo"…) en frames que NO son una pantalla de
aceptación. Para NO dar falsos positivos, se exige, EN EL MISMO frame, **texto de
aceptación + un control para aceptar** (checkbox o botón de aceptar), SALVO una
frase FUERTE e inequívoca (la condición PERMANENTE del Generador), que dispara sola.
Una palabra suelta JAMÁS dispara.

No hace clics ni cambia estado: solo LEE texto/estructura y responde sí/no.
"""
from __future__ import annotations

import re

# Frases FUERTES e inequívocas de aceptación: disparan SOLAS (aunque no se detecte
# el control). Son multi-palabra → sin falso positivo posible con el menú de SOL.
MARCAS_FUERTES = (
    "declaro tener conocimiento y acepto",
    "acepto las condiciones para ser generador",
    "condiciones para ser generador electronico",
    "condiciones para ser generador electrónico",
    "declaro bajo juramento",
    "doy mi consentimiento",
)

# Texto de aceptación que SOLO dispara JUNTO a un control (checkbox/botón de
# aceptar) en el MISMO frame. NO se incluyen palabras sueltas y ambiguas como
# "afiliación"/"autorizo" (aparecen en el menú sin ser una pantalla de aceptación).
MARCAS_ACEPTACION = (
    "declaro tener conocimiento", "declaro conocer",
    "acepto las condiciones", "acepto los terminos", "acepto los términos",
    "condiciones para ser", "terminos y condiciones", "términos y condiciones",
    "consentimiento informado",
    "generador electronico", "generador electrónico", "generador de comprobantes",
)
_RE_ESPACIOS = re.compile(r"\s+")
# Un botón/enlace es "de aceptar" si su etiqueta trae estas raíces (no 'continuar'
# ni 'buscar' sueltos, que son de pantallas normales).
_RE_BOTON_ACEPTA = re.compile(
    r"(acept|declaro|consient|consent|confirmo|afili[ao])", re.I)


def _norm(texto: str) -> str:
    return _RE_ESPACIOS.sub(" ", (texto or "").lower())


def texto_es_aceptacion(texto: str) -> str | None:
    """Devuelve la marca encontrada (para reportar) o None. Primero las FUERTES,
    luego las que requieren control. `texto` puede venir en cualquier caja."""
    t = _norm(texto)
    for m in MARCAS_FUERTES:
        if m in t:
            return m
    for m in MARCAS_ACEPTACION:
        if m in t:
            return m
    return None


def _controles(scope) -> tuple[bool, bool]:
    """(hay_checkbox, hay_boton_de_aceptar) en este scope. No interactúa."""
    hay_chk = hay_btn = False
    try:
        if scope.query_selector("input[type=checkbox]"):
            hay_chk = True
    except Exception:
        pass
    try:
        for b in scope.query_selector_all(
                "button, input[type=submit], input[type=button], a[onclick]"):
            etiqueta = ((b.get_attribute("value") or "") + " "
                        + (b.inner_text() or ""))
            if _RE_BOTON_ACEPTA.search(etiqueta):
                hay_btn = True
                break
    except Exception:
        pass
    return hay_chk, hay_btn


def detectar_aceptacion(page) -> tuple[bool, str | None]:
    """Escanea la página y CADA frame por separado. Devuelve (es_aceptacion, detalle).
    Dispara si, en UN frame: (a) hay una frase FUERTE, o (b) hay texto de aceptación
    **y** un control para aceptar (checkbox o botón de aceptar). Una palabra suelta
    NO dispara. NUNCA hace clic; el llamador debe abortar la pantalla."""
    scopes = [page]
    try:
        scopes += list(page.frames)
    except Exception:
        pass
    vistos = set()
    for sc in scopes:
        k = id(sc)
        if k in vistos:
            continue
        vistos.add(k)
        try:
            texto = sc.inner_text("body")
        except Exception:
            continue
        t = _norm(texto)
        fuerte = next((m for m in MARCAS_FUERTES if m in t), None)
        if fuerte:
            return (True, f"frase fuerte='{fuerte}'")
        marca = texto_es_aceptacion(texto)
        if not marca:
            continue
        hay_chk, hay_btn = _controles(sc)
        if hay_chk or hay_btn:
            ctl = "checkbox" if hay_chk else "botón-aceptar"
            return (True, f"marca='{marca}' + {ctl}")
    return (False, None)
