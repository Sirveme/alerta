"""
clasificacion.py — alerta.pe   (C:\\alertape\\clasificacion.py)
═══════════════════════════════════════════════════════════════════════
Clasifica una notificación → (tipo_documento, urgencia).

Prioridad ESTRICTA (primera que resuelve gana):
  1. CARPETA de SUNAT (señal oficial; nombre_carpeta) — máxima prioridad.
  2. ASUNTO (capa de palabras clave) — solo si la carpeta no resolvió.
  3. OTRO — fallback.

REGLA DE ORO (zAlerta-32): ante la MENOR duda → INFORMATIVA, nunca urgente.
Un falso-urgente quema la credibilidad del rojo; un falso-informativo solo
molesta. La capa de asunto solo asigna URGENTE/CRÍTICA cuando el match es
INEQUÍVOCO de cobranza/sanción ejecutiva real.

Caso trampa "coactiv": mencionar "coactiva" NO es urgente por sí solo. Solo
es CRÍTICA si además hay un término ejecutivo (ejecución/embargo/medida
cautelar/inicio de cobranza). "Resolución de conclusión", levantamientos o
menciones de expediente → INFORMATIVA.
"""

from __future__ import annotations

import unicodedata

from models import TipoDocumento, Urgencia


def _norm(texto: str | None) -> str:
    """minúsculas + sin tildes, para comparar robustamente."""
    base = (texto or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", base)
                   if unicodedata.category(c) != "Mn")


# ─────────────────────────────────────────────────────────────────────
# CAPA 1 — CARPETA (señal oficial de SUNAT). Orden importa.
# ─────────────────────────────────────────────────────────────────────
_REGLAS_CARPETA: list[tuple[tuple[str, ...], TipoDocumento, Urgencia]] = [
    (("coactiv",),                    TipoDocumento.COBRANZA_COACTIVA,        Urgencia.CRITICA),
    (("orden", "pago"),               TipoDocumento.ORDEN_PAGO,               Urgencia.URGENTE),
    (("multa",),                      TipoDocumento.MULTA,                    Urgencia.URGENTE),
    (("resolucion", "determinacion"), TipoDocumento.RESOLUCION_DETERMINACION, Urgencia.IMPORTANTE),
    (("fraccionamiento",),            TipoDocumento.FRACCIONAMIENTO,          Urgencia.IMPORTANTE),
    (("esquela",),                    TipoDocumento.ESQUELA,                  Urgencia.IMPORTANTE),
    (("aviso",),                      TipoDocumento.AVISO,                    Urgencia.INFORMATIVA),
]

# ─────────────────────────────────────────────────────────────────────
# CAPA 2 — ASUNTO. Términos ejecutivos que SÍ justifican urgencia real.
# ─────────────────────────────────────────────────────────────────────
_EJECUTIVO_COACTIVA = ("ejecucion coactiva", "ejecucion de cobranza",
                       "inicio de cobranza", "inicio de la cobranza",
                       "medida cautelar", "embargo")

# Informativos observados en prod → SIEMPRE AVISO/INFORMATIVA (sin escalar).
_ASUNTO_INFORMATIVO = (
    "puesta a disposicion de comprobantes", "reporte resumen de comprobantes",
    "comprobantes - plataforma", " rhe", " fe ", "rvie", "rce",
    "registro de compras y ventas", "sire", "730f",
    "propuesta del registro", "vencimiento del registro",
    "certificado digital", "emision de certificado",
    "comunicacion informativa", "nuevos inscritos",
    "expediente mpv", "se registro expediente",
)

# PAGOS confirmados (zAlerta-69): constancias de pago con PDF de datos ricos.
# Es categoría propia (ni deuda ni informativo) y entra en velocidad rápida
# (se baja el PDF a GCS). No es urgente: es la confirmación de un pago hecho.
_ASUNTO_PAGO = (
    "formulario 1662", "pago de valores", "constancia de pago",
    "pago de tributos", "pago de fraccionamiento", "pago de deuda",
)


def _por_carpeta(nombre_carpeta: str | None) -> tuple[TipoDocumento, Urgencia, bool]:
    n = _norm(nombre_carpeta)
    if n:
        for claves, t, u in _REGLAS_CARPETA:
            if all(k in n for k in claves):
                return t, u, True
    return TipoDocumento.OTRO, Urgencia.INFORMATIVA, False


def _por_asunto(asunto: str | None) -> tuple[TipoDocumento, Urgencia, bool]:
    a = _norm(asunto)
    if not a:
        return TipoDocumento.OTRO, Urgencia.INFORMATIVA, False

    # 1) Acción ejecutiva INEQUÍVOCA (embargo, medida cautelar, ejecución
    #    coactiva, inicio de cobranza) → CRÍTICA, lleve o no la palabra "coactiv".
    if any(t in a for t in _EJECUTIVO_COACTIVA):
        return TipoDocumento.COBRANZA_COACTIVA, Urgencia.CRITICA, True
    # Caso trampa: menciona "coactiv" SIN acción ejecutiva (conclusión/archivo/
    #    levantamiento/solo informa) → INFORMATIVA, nunca rojo.
    if "coactiv" in a:
        return TipoDocumento.AVISO, Urgencia.INFORMATIVA, True

    # 2) Acciones inequívocas de cobranza/sanción (urgencia real).
    if "orden de pago" in a:
        return TipoDocumento.ORDEN_PAGO, Urgencia.URGENTE, True
    if ("resolucion de multa" in a or "aplicacion de multa" in a
            or "resolucion de sancion" in a):
        return TipoDocumento.MULTA, Urgencia.URGENTE, True
    if "resolucion de determinacion" in a:
        return TipoDocumento.RESOLUCION_DETERMINACION, Urgencia.IMPORTANTE, True
    if "fraccionamiento" in a:
        return TipoDocumento.FRACCIONAMIENTO, Urgencia.IMPORTANTE, True
    if "esquela" in a:
        return TipoDocumento.ESQUELA, Urgencia.IMPORTANTE, True

    # 2.b) PAGO confirmado (constancia con PDF de datos ricos). Categoría propia,
    #      no urgente. Va antes que los informativos: el 1662 NO es un aviso.
    if any(k in a for k in _ASUNTO_PAGO):
        return TipoDocumento.PAGO, Urgencia.INFORMATIVA, True

    # 3) Informativos conocidos → AVISO/INFORMATIVA (nunca escalan).
    if any(k in a for k in _ASUNTO_INFORMATIVO):
        return TipoDocumento.AVISO, Urgencia.INFORMATIVA, True

    return TipoDocumento.OTRO, Urgencia.INFORMATIVA, False


_URG_BAJAS = (Urgencia.SIN_CLASIFICAR, Urgencia.INFORMATIVA)


# ─────────────────────────────────────────────────────────────────────
# SUBTIPOS de Resolución Coactiva (zAlerta-70). 4 grupos con trato distinto:
#   RIESGO (rojo), ALIVIO (verde), CIERRE (neutro), ADMIN (gris).
# Detección por ASUNTO, ORDEN IMPORTA: lo específico antes que lo genérico.
# ─────────────────────────────────────────────────────────────────────
_SUBTIPOS_COACTIVOS: list[tuple[tuple[str, ...], str]] = [
    (("retencion", "tercero"),      "retencion"),      # medida cautelar sobre 3ros
    (("retencion a tercero",),      "retencion"),
    (("levantamiento", "embargo"),  "levantamiento"),  # ALIVIO
    (("levanta", "embargo"),        "levantamiento"),
    (("reduccion", "embargo"),      "reduccion"),       # ALIVIO
    (("reduce", "embargo"),         "reduccion"),
    (("conclusion",),               "conclusion"),      # CIERRE (numeral de sustento)
    (("concluye",),                 "conclusion"),
    (("coactiva fl",),              "fl"),              # ADMIN (actuación interna)
    (("resolucion coactiva fl",),   "fl"),
    (("ejecucion coactiva",),       "ejecucion"),       # REC (la deuda)
    (("ejecucion de cobranza",),    "ejecucion"),
]

# Metadatos por subtipo: grupo, urgencia (color), si CUENTA como deuda, etiqueta,
# acción y orientación. "informa, no asesora" (Conclusión: no afirma extinción).
COACTIVO_META: dict[str, dict] = {
    "ejecucion":    {"grupo": "riesgo", "urgencia": Urgencia.CRITICA,
                     "deuda": True,  "etiqueta": "Cobranza Coactiva",
                     "accion": "Ver deuda y plazo"},
    "retencion":    {"grupo": "riesgo", "urgencia": Urgencia.CRITICA,
                     "deuda": False, "etiqueta": "Retención a terceros",
                     "accion": "Ver retención"},
    "levantamiento": {"grupo": "alivio", "urgencia": Urgencia.INFORMATIVA,
                      "deuda": False, "etiqueta": "Embargo levantado",
                      "accion": "Ver constancia"},
    "reduccion":    {"grupo": "alivio", "urgencia": Urgencia.IMPORTANTE,
                     "deuda": False, "etiqueta": "Embargo reducido",
                     "accion": "Ver detalle"},
    "conclusion":   {"grupo": "cierre", "urgencia": Urgencia.INFORMATIVA,
                     "deuda": False, "etiqueta": "Procedimiento concluido",
                     "accion": "Verifica el estado de la deuda"},
    "fl":           {"grupo": "admin", "urgencia": Urgencia.INFORMATIVA,
                     "deuda": False, "etiqueta": "Actuación administrativa",
                     "accion": "Ver detalle"},
}
# Subtipos coactivos que NO suman monto propio en el panel de deuda (zAlerta-70).
COACTIVO_NO_SUMA = {s for s, m in COACTIVO_META.items() if not m["deuda"]}


def subtipo_coactivo(asunto: str | None) -> str | None:
    """Subtipo de una resolución coactiva por su asunto, o None si genérico.
    Si es coactiva pero no calza ningún patrón, el caller la trata como
    'ejecucion' (REC) por prudencia: ante duda, es riesgo/deuda."""
    a = _norm(asunto)
    if not a:
        return None
    for claves, sub in _SUBTIPOS_COACTIVOS:
        if all(k in a for k in claves):
            return sub
    return None


def _clasificar_base(nombre_carpeta, asunto, urgente):
    """Devuelve (tipo_documento, urgencia, fuente).

    fuente ∈ {"carpeta","asunto","indurg",None}. None = nada resolvió (OTRO puro).

    Interacción con `urgente` (indUrg de SUNAT):
      - Sobre un match de CARPETA con urgencia baja → escala a URGENTE (la carpeta
        es señal oficial fuerte, confiamos en el flag de SUNAT ahí).
      - Sobre un match de ASUNTO → NO escala (regla de oro: nuestra clasificación
        explícita por asunto manda; no convertimos un informativo en rojo).
      - Sobre el fallback OTRO → a lo sumo IMPORTANTE (ámbar), NUNCA rojo: ante
        duda no quemamos credibilidad con un falso-urgente.
    """
    # 1) Carpeta (máxima prioridad).
    tipo, urg, ok = _por_carpeta(nombre_carpeta)
    if ok:
        if urgente and urg in _URG_BAJAS:
            urg = Urgencia.URGENTE
        return tipo, urg, "carpeta"

    # 2) Asunto (solo si la carpeta no resolvió). No escala por indUrg.
    tipo, urg, ok = _por_asunto(asunto)
    if ok:
        return tipo, urg, "asunto"

    # 3) Fallback OTRO. indUrg, a lo sumo, lo marca importante (ámbar), no rojo.
    if urgente:
        return TipoDocumento.OTRO, Urgencia.IMPORTANTE, "indurg"
    return TipoDocumento.OTRO, Urgencia.INFORMATIVA, None


def clasificar(nombre_carpeta: str | None, asunto: str | None,
               urgente: bool = False) -> tuple[TipoDocumento, Urgencia, str | None]:
    """Clasifica → (tipo_documento, urgencia, fuente). zAlerta-70: si es coactiva,
    su SUBTIPO ajusta la urgencia (un Levantamiento es VERDE, no rojo). El tipo
    sigue siendo COBRANZA_COACTIVA; el subtipo (para etiqueta/deuda) se obtiene
    aparte con subtipo_coactivo()."""
    tipo, urg, fuente = _clasificar_base(nombre_carpeta, asunto, urgente)
    if tipo == TipoDocumento.COBRANZA_COACTIVA:
        st = subtipo_coactivo(asunto)
        if st and st in COACTIVO_META:
            urg = COACTIVO_META[st]["urgencia"]
    return tipo, urg, fuente
