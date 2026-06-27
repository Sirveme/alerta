"""
clasificacion.py — alerta.pe   (C:\\alertape\\clasificacion.py)
═══════════════════════════════════════════════════════════════════════
Clasifica una notificación a partir de la CARPETA de SUNAT (zAlerta-28).

La carpeta es la señal OFICIAL y limpia: SUNAT ya agrupa los mensajes en
"Órdenes de Pago", "Resoluciones de Ejecución Coactiva", "Multas", etc. En
vez de adivinar por el asunto, mapeamos el nombre de carpeta → (tipo de
documento, urgencia) usando los enums YA existentes (TipoDocumento, Urgencia).

Reglas: se normaliza el nombre (minúsculas, sin tildes) y se busca por
palabras clave; gana la PRIMERA regla que matchea. Si ninguna matchea, cae en
OTRO + INFORMATIVA (fallback seguro, sin inventar urgencia).
"""

from __future__ import annotations

import unicodedata

from models import TipoDocumento, Urgencia


def _norm(texto: str | None) -> str:
    """minúsculas + sin tildes, para comparar nombres de carpeta robustamente."""
    base = (texto or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", base)
                   if unicodedata.category(c) != "Mn")


# Orden IMPORTA: la primera regla cuyas claves estén TODAS en el nombre gana.
# (claves_que_deben_aparecer, tipo_documento, urgencia)
_REGLAS: list[tuple[tuple[str, ...], TipoDocumento, Urgencia]] = [
    (("coactiv",),                    TipoDocumento.COBRANZA_COACTIVA,        Urgencia.CRITICA),
    (("orden", "pago"),               TipoDocumento.ORDEN_PAGO,               Urgencia.URGENTE),
    (("multa",),                      TipoDocumento.MULTA,                    Urgencia.URGENTE),
    (("resolucion", "determinacion"), TipoDocumento.RESOLUCION_DETERMINACION, Urgencia.IMPORTANTE),
    (("fraccionamiento",),            TipoDocumento.FRACCIONAMIENTO,          Urgencia.IMPORTANTE),
    (("esquela",),                    TipoDocumento.ESQUELA,                  Urgencia.IMPORTANTE),
    (("aviso",),                      TipoDocumento.AVISO,                    Urgencia.INFORMATIVA),
]

# Urgencias que NO conviene rebajar si SUNAT marcó el mensaje como urgente.
_URG_BAJAS = (Urgencia.SIN_CLASIFICAR, Urgencia.INFORMATIVA)


def clasificar_por_carpeta(nombre_carpeta: str | None,
                           urgente: bool = False) -> tuple[TipoDocumento, Urgencia, bool]:
    """Devuelve (tipo_documento, urgencia, matched).

    matched=False indica que la carpeta no estaba en el mapeo (cayó en el
    fallback OTRO/INFORMATIVA) — útil para loguear y afinar el mapeo luego.
    `urgente` es el indUrg de SUNAT: si la carpeta dio una urgencia baja pero
    SUNAT lo marcó urgente, se escala a URGENTE (no se baja una alta).
    """
    n = _norm(nombre_carpeta)
    tipo, urg, matched = TipoDocumento.OTRO, Urgencia.INFORMATIVA, False
    if n:
        for claves, t, u in _REGLAS:
            if all(k in n for k in claves):
                tipo, urg, matched = t, u, True
                break
    if urgente and urg in _URG_BAJAS:
        urg = Urgencia.URGENTE
    return tipo, urg, matched
