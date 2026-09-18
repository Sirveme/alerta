"""
webapp/mapa_render.py — alerta.pe · Render del "Mapa Global" (página interna).
═══════════════════════════════════════════════════════════════════════
Convierte `webapp/contenido/mapa_global.md` (Markdown, subconjunto acotado)
en una estructura que la plantilla maqueta como tarjetas. NO usa librería
externa: parser mínimo y SEGURO (escapa HTML antes de formatear).

Convención (ver cabecera del .md):
  # Título · > lede · ## Panel · ### Subtítulo · - viñeta (anida con 2 esp) ·
  **negrita** · `código`
"""

from __future__ import annotations

import html
import re
from pathlib import Path

RUTA_MD = Path(__file__).parent / "contenido" / "mapa_global.md"

# Acento por palabra clave del título del panel (tokens de marca en app.css).
_ACENTOS = [
    (("sistema",), "#8B7CF0"),       # vip / violeta
    (("contable", "contador"), "#3DD68C"),   # al_dia / verde
    (("tributario", "importante"), "#E8A53D"),  # importante / ámbar
    (("asistente",), "#22D3D2"),     # cian
    (("empresario",), "#F0526E"),    # urgente / coral
    (("central", "funcional"), "#3DD68C"),
]
_ACENTO_DEFAULT = "#3DD68C"


def _acento_de(titulo: str) -> str:
    t = titulo.lower()
    for claves, hexv in _ACENTOS:
        if any(k in t for k in claves):
            return hexv
    return _ACENTO_DEFAULT


def _inline(texto: str) -> str:
    """Escapa HTML y aplica **negrita** y `código`. Seguro por diseño."""
    s = html.escape(texto)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def _cuerpo_html(lineas: list[str]) -> str:
    """Convierte las líneas de un panel (sin su '## ') en HTML: ###, viñetas
    (1 nivel de anidación con 2 espacios), y párrafos."""
    out: list[str] = []
    i = 0
    n = len(lineas)
    while i < n:
        ln = lineas[i]
        raw = ln.rstrip()
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("### "):
            out.append(f'<h3 class="mp-h3">{_inline(stripped[4:])}</h3>')
            i += 1
            continue
        if stripped.startswith("> "):
            out.append(f'<p class="mp-lede">{_inline(stripped[2:])}</p>')
            i += 1
            continue
        if stripped.startswith("- "):
            # Bloque de lista con anidación por sangría de 2 espacios.
            out.append('<ul class="mp-ul">')
            abierto_sub = False
            while i < n and lineas[i].strip().startswith("- "):
                indent = len(lineas[i]) - len(lineas[i].lstrip(" "))
                item = _inline(lineas[i].strip()[2:])
                if indent >= 2:
                    if not abierto_sub:
                        out.append('<ul class="mp-ul mp-ul--sub">')
                        abierto_sub = True
                    out.append(f"<li>{item}</li>")
                else:
                    if abierto_sub:
                        out.append("</ul>")
                        abierto_sub = False
                    out.append(f"<li>{item}</li>")
                i += 1
            if abierto_sub:
                out.append("</ul>")
            out.append("</ul>")
            continue
        # Párrafo normal.
        out.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    return "\n".join(out)


def parsear(md: str) -> dict:
    """md → {titulo, intro_html, secciones:[{titulo, acento, cuerpo_html}]}."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.DOTALL)   # quita comentarios HTML
    lineas = md.splitlines()
    titulo = "Mapa Global de alerta.pe"
    intro: list[str] = []
    secciones: list[dict] = []
    actual: dict | None = None
    cuerpo: list[str] = []

    def cerrar():
        nonlocal actual, cuerpo
        if actual is not None:
            actual["cuerpo_html"] = _cuerpo_html(cuerpo)
            secciones.append(actual)
        actual, cuerpo = None, []

    for ln in lineas:
        s = ln.strip()
        if s.startswith("# ") and not s.startswith("## "):
            titulo = s[2:].strip()
            continue
        if s.startswith("## "):
            cerrar()
            t = s[3:].strip()
            actual = {"titulo": t, "acento": _acento_de(t), "cuerpo_html": ""}
            continue
        if actual is None:
            intro.append(ln)          # antes del primer panel = intro
        else:
            cuerpo.append(ln)
    cerrar()

    intro_html = _cuerpo_html(intro)
    return {"titulo": titulo, "intro_html": intro_html, "secciones": secciones}


def cargar_mapa() -> dict:
    """Lee el .md del disco y lo parsea. Si falta, devuelve un aviso legible."""
    try:
        md = RUTA_MD.read_text(encoding="utf-8")
    except OSError:
        return {"titulo": "Mapa Global de alerta.pe",
                "intro_html": "<p>Falta el archivo <code>mapa_global.md</code>.</p>",
                "secciones": []}
    return parsear(md)
