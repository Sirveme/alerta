"""
webapp/deuda.py — alerta.pe (zAlerta-52)
═══════════════════════════════════════════════════════════════════════
Fuente ÚNICA de la lógica de deuda valorada:
  - extracción del monto desde el pdf_texto (provisional, hasta el parser de
    zAlerta-39). La misma que usa /api/resumen (zAlerta-38/49) — sin copias
    divergentes: resumen.py importa de aquí.
  - motor de AGREGACIÓN por estudio (panel del contador + futuro export Excel).

Principio (zAlerta-49): la deuda NUNCA se oculta ni se muestra falsamente baja.
Los documentos sin monto parseado cuentan como "por confirmar", no se omiten.
"""

from __future__ import annotations

import html as _html
import re
from datetime import timedelta
from urllib.parse import unquote

from sqlalchemy import select, func

from models import (
    DocumentoValorado, Contribuyente, Notificacion, TipoValorado, ahora_lima)
from clasificacion import COACTIVO_NO_SUMA


def anio_deuda_desde_default() -> int:
    """Año-desde por defecto (zAlerta-72): año actual − 2 (arranque rápido)."""
    return ahora_lima().year - 2


# ─────────────────────────────────────────────────────────────────────
# Cuerpo FIEL del mensaje SUNAT (zAlerta-82). Se muestra LITERAL, sin resumir
# ni interpretar (la fidelidad literal ES la forma de no asesorar).
# ─────────────────────────────────────────────────────────────────────
_RE_TAG = re.compile(r"<[^>]+>")


def cuerpo_fiel(texto_html: str | None) -> list[str]:
    """Cuerpo del mensaje SUNAT como lista de PÁRRAFOS, literal. Limpia el HTML
    (<br>→salto, entidades) y, si existe, recorta al bloque 'Estimado…Atentamente,
    SUNAT'. Devuelve [] si no hay cuerpo (p. ej. metadata JSON). NO resume."""
    if not texto_html:
        return []
    t = texto_html
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)</\s*p\s*>", "\n\n", t)
    t = _RE_TAG.sub("", t)
    # Entidades: %26%23243; (url-encoded) → &#243; → ó
    try:
        t = _html.unescape(unquote(t))
    except Exception:
        t = _html.unescape(t)
    low = t.lower()
    if not low.strip() or low.strip().startswith("{"):
        return []   # metadata JSON u otro no-cuerpo → sin cuerpo fiel
    i = low.find("estimado")
    if i >= 0:
        t = t[i:]
        low = t.lower()
        j = low.rfind("atentamente")
        if j >= 0:
            k = low.find("sunat", j)
            t = t[:(k + 5)] if k >= 0 else t[:j].rstrip()
    # Párrafos: bloques separados por línea(s) en blanco; dentro, une saltos simples.
    paras = []
    for bloque in re.split(r"\n\s*\n", t):
        p = " ".join(x.strip() for x in bloque.splitlines() if x.strip()).strip()
        if p:
            paras.append(p)
    # Si no hubo marcador "Estimado" y quedó una sola línea corta, no forzar.
    return paras if (i >= 0 or len("".join(paras)) > 40) else []


def _anio_de_valorado(dv, fecha_pub) -> int | None:
    """Año del documento valorado (para el filtro de años). Usa fecha_emisión;
    si no, la fecha de publicación de la notificación."""
    if getattr(dv, "fecha_emision", None):
        return dv.fecha_emision.year
    if fecha_pub:
        return fecha_pub.year
    return None


# ─────────────────────────────────────────────────────────────────────
# Monto: extracción provisional del pdf_texto (zAlerta-38/49)
# ─────────────────────────────────────────────────────────────────────
def _num(s: str) -> float | None:
    try:
        return float((s or "").replace(",", "").strip())
    except ValueError:
        return None


_RE_MONTO_TOTAL3 = re.compile(
    r"Monto\s*Total\s*S/\s*[\d.,]+\s*S/\s*[\d.,]+\s*S/\s*([\d.,]+)", re.I)
_RE_MONTO_ANCLA = re.compile(
    r"(?:total\s+deuda(?:\s+exigible)?|deuda\s+exigible|monto\s+total)"
    r"[^S]{0,40}S/\s*([\d.,]+)", re.I)
_RE_MONTO_TODOS = re.compile(r"S/\s*([\d.,]+)")


def extraer_monto(texto: str | None) -> float | None:
    """MONTO TOTAL de deuda desde el pdf_texto crudo (provisional).
    (1) 'Monto Total S/ a S/ b S/ c' → c (OP/Multa); (2) ancla 'total deuda';
    (3) fallback: el mayor S/ (Coactiva/Fraccionamiento)."""
    if not texto:
        return None
    m = _RE_MONTO_TOTAL3.search(texto)
    if m:
        return _num(m.group(1))
    m = _RE_MONTO_ANCLA.search(texto)
    if m:
        return _num(m.group(1))
    montos = [v for v in (_num(x) for x in _RE_MONTO_TODOS.findall(texto))
              if v is not None]
    return max(montos) if montos else None


def fmt_soles(monto: float | None) -> str | None:
    """1164.0 → 'S/ 1,164'. None si no hay monto."""
    if monto is None:
        return None
    entero = abs(monto - round(monto)) < 0.005
    return "S/ " + (f"{monto:,.0f}" if entero else f"{monto:,.2f}")


def monto_de_valorado(dv) -> float | None:
    """Monto de un DocumentoValorado: usa columnas parseadas si existen
    (parser zAlerta-39), si no lo extrae del pdf_texto."""
    if dv.monto_total is not None:
        return float(dv.monto_total)
    if dv.importe is not None:
        return float(dv.importe)
    return extraer_monto(dv.pdf_texto)


# ─────────────────────────────────────────────────────────────────────
# PAGOS confirmados: extracción de datos del Formulario 1662 (zAlerta-69)
# ─────────────────────────────────────────────────────────────────────
# Best-effort sobre el pdf_texto de la constancia (provisional, como el monto de
# deuda). Cada campo es opcional: si no se detecta, la UI muestra "Ver PDF".
# Se afina contra el texto real tras el primer re-scrapeo del 1662.
_RE_PG_PERIODO = re.compile(
    r"per[ií]odo\s*(?:tributario)?\s*:?\s*(\d{2}\s*[/-]\s*\d{4}|\d{4}\s*[-/]\s*\d{2}|\d{6})", re.I)
_RE_PG_IMPORTE = re.compile(
    r"(?:importe(?:\s*(?:total|pagado))?|total\s*(?:pagado|a\s*pagar)?)"
    r"\s*:?\s*S?\s*/?\s*\.?\s*(\d[\d.,]*)", re.I)
# Frontera: corta el capturado al llegar a la siguiente etiqueta del formulario
# (o a 2+ espacios / salto de línea). Robusto aunque el pdf_texto venga en 1 línea.
_PG_STOP = (r"(?=\s{2,}|\n|$|\bimporte\b|\bbanco\b|\bfecha\b|\bnumero\b|\bn[°ºo]\b|"
            r"\bc[oó]digo\b|\bruc\b|\bper[ií]odo\b|\bdocumento\b|\bvalor\b|\boperaci)")
_RE_PG_TRIBUTO = re.compile(
    r"(?:tributo|concepto|c[oó]digo\s*de\s*tributo)\s*:?\s*(?:\d{3,6}\s*[-–\s]\s*)?"
    r"([A-Za-zÁÉÍÓÚÑáéíóúñ][A-Za-zÁÉÍÓÚÑáéíóúñ .]{3,55}?)" + _PG_STOP, re.I)
_RE_PG_ORDEN = re.compile(
    r"(?:n[°ºo]?\s*(?:de\s*)?orden|n[uú]mero\s*de\s*orden)\s*:?\s*(\d{6,})", re.I)
_RE_PG_OPERACION = re.compile(
    r"(?:n[°ºo]?\s*(?:de\s*)?operaci[oó]n|n[uú]mero\s*de\s*operaci[oó]n)"
    r"[^:\n]*:\s*(\d{4,})", re.I)
_RE_PG_BANCO = re.compile(
    r"(?:banco|entidad(?:\s*(?:bancaria|financiera))?)\s*:?\s*"
    r"([A-Za-zÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ .]{1,28}?)" + _PG_STOP, re.I)
_RE_PG_VALOR = re.compile(
    r"(?:n[uú]mero\s*(?:de\s*)?(?:documento|valor)|valor|documento)\s*:?\s*(\d{10,})", re.I)
_RE_PG_RUC = re.compile(r"\bruc\b\s*:?\s*(\d{11})", re.I)
_RE_PG_FECHA = re.compile(
    r"fecha\s*(?:de\s*pago|de\s*presentaci[oó]n)?\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})", re.I)


def _grp(rx, texto):
    m = rx.search(texto or "")
    return m.group(1).strip() if m else None


# Retención a Terceros (zAlerta-70): RUC del DEUDOR TRIBUTARIO en el PDF. Si
# difiere del RUC del buzón, el contribuyente es el TERCERO RETENEDOR (acción).
_RE_DEUDOR = re.compile(r"deudor\s*(?:tributario)?[^0-9]{0,60}(\d{11})", re.I)


def deudor_de_retencion(texto: str | None) -> str | None:
    """RUC del deudor tributario en una Retención a Terceros (o None)."""
    m = _RE_DEUDOR.search(texto or "")
    return m.group(1) if m else None


# ─────────────────────────────────────────────────────────────────────
# Esquela de Omiso (zAlerta-81): extrae período(s)+tributo(s) del PDF.
# Best-effort sobre el pdf_texto del DOCUMENTO real de la esquela (2º PDF).
# Puede haber VARIAS filas (varios período+tributo). Se afina contra el texto
# real tras el primer FULL que capture la esquela. Degrada con gracia.
# ─────────────────────────────────────────────────────────────────────
_RE_ESQ_PERIODO = re.compile(r"\b(20\d{2})\s*[-/]?\s*(0[1-9]|1[0-2])\b")   # YYYYMM / YYYY-MM
_RE_ESQ_TRIB_SANC = re.compile(r"tributo\s*sanci[oó]n\s*:?\s*(\d{3,5})", re.I)
_RE_ESQ_TRIB_ASOC = re.compile(r"tributo\s*asociad[oa]\s*:?\s*(\d{3,5})", re.I)


def _periodo_fmt(anio: str, mes: str) -> str:
    return f"{mes}/{anio}"


def extraer_esquela(texto: str | None) -> dict:
    """Datos de una Esquela de Omiso: período(s) y tributo(s) omitidos.
    Devuelve {periodos:[...], tributo_sancion, tributo_asociado, n_omisos,
    resumen}. Indexable por período+tributo (llave de la cadena de cumplimiento).
    Best-effort: si el texto real difiere, muestra lo que salga + link al PDF."""
    t = texto or ""
    periodos = []
    vistos = set()
    for anio, mes in _RE_ESQ_PERIODO.findall(t):
        p = _periodo_fmt(anio, mes)
        if p not in vistos:
            vistos.add(p)
            periodos.append(p)
    sanc = _RE_ESQ_TRIB_SANC.search(t)
    asoc = _RE_ESQ_TRIB_ASOC.search(t)
    resumen = None
    if periodos:
        resumen = "Período(s): " + ", ".join(periodos[:6])
    return {
        "periodos": periodos,
        "tributo_sancion": sanc.group(1) if sanc else None,
        "tributo_asociado": asoc.group(1) if asoc else None,
        "n_omisos": len(periodos),
        "resumen": resumen,
    }


# ─────────────────────────────────────────────────────────────────────
# Asociación valor ↔ pago ↔ coactiva (zAlerta-73). AL VUELO por número
# normalizado. Diagnóstico (CCPL): el match por dígitos es FIABLE —
#   · REC lista en su pdf_texto el nº de la OP que ejecuta (123-001-0700325).
#   · El pago 1662 guarda `valor_pagado` (1230010700325 = ese mismo nº).
# NO se inventan vínculos: solo si el núcleo de dígitos coincide.
# ─────────────────────────────────────────────────────────────────────
_RE_NUMDOC = re.compile(r"\d{3}-\d{3}-\d{6,7}")


def _norm_doc(s: str | None) -> str:
    """Núcleo comparable de un nº de documento: solo dígitos.
    '123-001-0700325' → '1230010700325'; '1230010700325' → '1230010700325'."""
    return re.sub(r"\D", "", s or "")


def _nums_en_texto(texto: str | None) -> set[str]:
    """Números de documento (formato 123-00X-XXXXXXX) hallados en un texto,
    normalizados a solo dígitos."""
    return {_norm_doc(x) for x in _RE_NUMDOC.findall(texto or "")}


async def asociados_de_valor(session, dv, subtipo_de: dict | None = None) -> dict:
    """Documentos ASOCIADOS a un valor de deuda (al vuelo, por número normalizado):
    pagos que lo pagan + resoluciones coactivas que lo ejecutan/refieren. Mismo
    contribuyente. Degrada con gracia: vínculo solo si el núcleo coincide.

    subtipo_de: mapa opcional {notificacion_id: subtipo_coactivo} para etiquetar
    las coactivas asociadas con su subtipo (zAlerta-70)."""
    nucleo = _norm_doc(dv.num_documento)
    if not nucleo:
        return {"pagos": [], "coactivas": []}
    otros = list(await session.scalars(
        select(DocumentoValorado).where(
            DocumentoValorado.contribuyente_id == dv.contribuyente_id,
            DocumentoValorado.id != dv.id)))
    pagos, coactivas = [], []
    for o in otros:
        tv = (o.tipo_valorado.value if hasattr(o.tipo_valorado, "value")
              else o.tipo_valorado)
        if tv == "pago":
            p = extraer_pago(o.pdf_texto)
            if p.get("valor_pagado") and _norm_doc(p["valor_pagado"]) == nucleo:
                pagos.append({
                    "valorado_id": str(o.id), "gcs": bool(o.gcs_key),
                    "importe_fmt": p.get("importe_fmt"), "fecha": p.get("fecha"),
                    "n_operacion": p.get("n_operacion"), "banco": p.get("banco"),
                    "periodo": p.get("periodo"), "tributo": p.get("tributo"),
                })
        elif tv == "cobranza_coactiva" and o.notificacion_id != dv.notificacion_id:
            # La coactiva ejecuta/menciona este valor si su texto trae el núcleo.
            if nucleo in _nums_en_texto(o.pdf_texto):
                sub = (subtipo_de or {}).get(o.notificacion_id)
                coactivas.append({
                    "valorado_id": str(o.id), "gcs": bool(o.gcs_key),
                    "num_documento": o.num_documento, "subtipo": sub,
                })
    return {"pagos": pagos, "coactivas": coactivas}


def extraer_pago(texto: str | None) -> dict:
    """Datos estructurados de una constancia de pago (Formulario 1662).
    Devuelve un dict con los campos detectados (None si no se hallan). El
    'valor_pagado' + 'periodo' + 'tributo' son la clave para cruzar con la
    deuda original en el prompt siguiente (SIN calcular saldo)."""
    t = texto or ""
    importe = _grp(_RE_PG_IMPORTE, t)
    imp_num = _num(importe) if importe else None
    return {
        "ruc": _grp(_RE_PG_RUC, t),
        "periodo": (_grp(_RE_PG_PERIODO, t) or "").replace(" ", "") or None,
        "valor_pagado": _grp(_RE_PG_VALOR, t),
        "tributo": (_grp(_RE_PG_TRIBUTO, t) or "").strip() or None,
        "importe_num": imp_num,
        "importe_fmt": fmt_soles(imp_num),
        "banco": (_grp(_RE_PG_BANCO, t) or "").strip() or None,
        "n_operacion": _grp(_RE_PG_OPERACION, t),
        "n_orden": _grp(_RE_PG_ORDEN, t),
        "fecha": _grp(_RE_PG_FECHA, t),
    }


# Etiqueta legible por TipoValorado (reusa el catálogo de tipos de documento).
ETIQUETA_VALORADO = {
    "cobranza_coactiva": "Cobranza Coactiva",
    "orden_pago": "Órdenes de Pago",
    "resolucion_multa": "Resoluciones de Multa",
    "fraccionamiento": "Fraccionamientos",
    "resolucion_determinacion": "Resoluciones de Determinación",
    "pago": "Pagos confirmados",
}
# Orden de presentación de los bloques (los 4 principales primero).
ORDEN_TIPOS = ["cobranza_coactiva", "orden_pago", "resolucion_multa",
               "fraccionamiento", "resolucion_determinacion"]


# ─────────────────────────────────────────────────────────────────────
# Cabecera del buzón: 3 contadores HONESTOS (zAlerta-76). Yuxtapone hechos,
# NUNCA calcula saldo. Reusa los montos de este mismo módulo (fuente única).
# ─────────────────────────────────────────────────────────────────────
async def resumen_cabecera(session, estudio_id) -> dict:
    """Devuelve los 3 contadores de la cabecera del buzón:
      - notificadas: deuda que SUNAT notificó (docs + monto).
      - con_pago:    deuda con ≥1 pago asociado (cruce por número; docs + monto).
      - con_plazo:   docs con plazo próximo (≈ este mes).
    Respeta el filtro de años por buzón y excluye las coactivas que no son deuda
    (Retención/alivio/cierre/admin). SIN restar: notificadas y con-pago aparte."""
    desde_default = anio_deuda_desde_default()
    hoy = ahora_lima()

    rows = (await session.execute(
        select(DocumentoValorado, Contribuyente.anio_deuda_desde,
               Notificacion.fecha_publica_sunat, Notificacion.subtipo_coactivo)
        .join(Contribuyente, Contribuyente.id == DocumentoValorado.contribuyente_id)
        .join(Notificacion, Notificacion.id == DocumentoValorado.notificacion_id,
              isouter=True)
        .where(Contribuyente.estudio_id == estudio_id,
               DocumentoValorado.tipo_valorado.not_in(
                   [TipoValorado.PAGO, TipoValorado.ESQUELA_OMISO])))).all()

    # Set de números de valor PAGADOS (normalizados) para el cruce doc↔pago.
    pago_nums: set[str] = set()
    for pg in (await session.scalars(
            select(DocumentoValorado)
            .join(Contribuyente, Contribuyente.id == DocumentoValorado.contribuyente_id)
            .where(Contribuyente.estudio_id == estudio_id,
                   DocumentoValorado.tipo_valorado == TipoValorado.PAGO))):
        vp = extraer_pago(pg.pdf_texto).get("valor_pagado")
        if vp:
            pago_nums.add(_norm_doc(vp))

    notif_n = pago_n = 0
    notif_monto = pago_monto = 0.0
    for dv, anio_desde, fecha_pub, subtipo in rows:
        anio = _anio_de_valorado(dv, fecha_pub)
        if anio is not None and anio < (anio_desde or desde_default):
            continue
        tv = (dv.tipo_valorado.value if hasattr(dv.tipo_valorado, "value")
              else dv.tipo_valorado)
        if tv == "cobranza_coactiva" and subtipo in COACTIVO_NO_SUMA:
            continue
        monto = monto_de_valorado(dv) or 0.0
        notif_n += 1
        notif_monto += monto
        if dv.num_documento and _norm_doc(dv.num_documento) in pago_nums:
            pago_n += 1
            pago_monto += monto

    # Con plazo próximo (≈ este mes): notifs del buzón con plazo futuro ≤ 35 días.
    plazo_n = await session.scalar(
        select(func.count(Notificacion.id))
        .join(Contribuyente, Contribuyente.id == Notificacion.contribuyente_id)
        .where(Contribuyente.estudio_id == estudio_id,
               Notificacion.plazo_vencimiento.is_not(None),
               Notificacion.plazo_vencimiento >= hoy,
               Notificacion.plazo_vencimiento <= hoy + timedelta(days=35))) or 0

    return {
        "notificadas": {"n": notif_n,
                        "monto_fmt": fmt_soles(notif_monto) if notif_monto else None},
        "con_pago": {"n": pago_n,
                     "monto_fmt": fmt_soles(pago_monto) if pago_monto else None},
        "con_plazo": {"n": plazo_n},
    }


# ─────────────────────────────────────────────────────────────────────
# Motor de AGREGACIÓN por estudio (panel del contador + export)
# ─────────────────────────────────────────────────────────────────────
async def deuda_estudio(session, estudio_id) -> dict:
    """Agrega la deuda valorada de TODA la cartera de un estudio.

    Multi-tenant: SOLO los contribuyentes con Contribuyente.estudio_id ==
    estudio_id. Devuelve, por tipo de deuda: total (suma de montos parseados),
    nº de clientes, 'por_confirmar' (docs sin monto), y el DETALLE por cliente
    (ordenado por monto desc) con sus documentos. Reutilizable para el export.
    """
    rows = (await session.execute(
        select(DocumentoValorado, Contribuyente.ruc, Contribuyente.razon_social,
               Notificacion.fecha_publica_sunat, Notificacion.subtipo_coactivo,
               Contribuyente.anio_deuda_desde)
        .join(Contribuyente, Contribuyente.id == DocumentoValorado.contribuyente_id)
        .join(Notificacion, Notificacion.id == DocumentoValorado.notificacion_id,
              isouter=True)
        .where(Contribuyente.estudio_id == estudio_id,
               # PAGO no es deuda (zAlerta-69): fuera del panel de deuda.
               DocumentoValorado.tipo_valorado.not_in(
                   [TipoValorado.PAGO, TipoValorado.ESQUELA_OMISO])))).all()

    desde_default = anio_deuda_desde_default()
    # Estructura intermedia: tipo → ruc → {razon, docs[], total, por_confirmar}
    por_tipo: dict[str, dict] = {}
    for dv, ruc, razon, fecha_pub, subtipo, anio_desde in rows:
        tipo = (dv.tipo_valorado.value if hasattr(dv.tipo_valorado, "value")
                else dv.tipo_valorado)
        # Filtro de años POR BUZÓN (zAlerta-72): oculta la deuda más vieja que el
        # año elegido (se CONSERVA en BD; solo no se muestra).
        anio_doc = _anio_de_valorado(dv, fecha_pub)
        if anio_doc is not None and anio_doc < (anio_desde or desde_default):
            continue
        # Coactivas que NO son deuda (Retención/Levantamiento/Reducción/Conclusión/
        # FL) NO suman en el panel (zAlerta-70): fuera del total, sin inflar.
        if tipo == "cobranza_coactiva" and subtipo in COACTIVO_NO_SUMA:
            continue
        monto = monto_de_valorado(dv)
        fecha = None
        if dv.fecha_emision:
            fecha = dv.fecha_emision.isoformat()
        elif fecha_pub:
            fecha = fecha_pub.date().isoformat()
        doc = {
            "valorado_id": str(dv.id),
            "num_documento": dv.num_documento or "—",
            "periodo": None,    # los llena el parser (zAlerta-39); aún "—"
            "tributo": None,
            "monto_num": monto,
            "monto_fmt": fmt_soles(monto),
            "fecha": fecha,
            "gcs": bool(dv.gcs_key),
        }
        t = por_tipo.setdefault(tipo, {})
        cli = t.setdefault(ruc, {"ruc": ruc, "razon": razon or ruc,
                                 "docs": [], "total_num": 0.0, "por_confirmar": 0})
        cli["docs"].append(doc)
        if monto is not None:
            cli["total_num"] += monto
        else:
            cli["por_confirmar"] += 1

    # Salida ordenada.
    salida_tipos = {}
    tipos_presentes = [t for t in ORDEN_TIPOS if t in por_tipo]
    tipos_presentes += [t for t in por_tipo if t not in ORDEN_TIPOS]
    for tipo in tipos_presentes:
        clientes = list(por_tipo[tipo].values())
        # ordenar docs de cada cliente por monto desc; clientes por total desc
        for cli in clientes:
            cli["docs"].sort(key=lambda d: (d["monto_num"] or 0), reverse=True)
            cli["total_fmt"] = fmt_soles(cli["total_num"]) if cli["total_num"] else None
        clientes.sort(key=lambda c: c["total_num"], reverse=True)
        total_num = sum(c["total_num"] for c in clientes)
        por_confirmar = sum(c["por_confirmar"] for c in clientes)
        n_docs = sum(len(c["docs"]) for c in clientes)
        salida_tipos[tipo] = {
            "tipo": tipo,
            "label": ETIQUETA_VALORADO.get(tipo, tipo),
            "total_num": total_num,
            "total_fmt": fmt_soles(total_num) if total_num else None,
            "n_clientes": len(clientes),
            "por_confirmar": por_confirmar,
            "n_docs": n_docs,
            "clientes": clientes,
        }

    total_clientes = await session.scalar(
        select(func.count(Contribuyente.id)).where(
            Contribuyente.estudio_id == estudio_id))
    return {
        "total_clientes": total_clientes or 0,
        "por_tipo": salida_tipos,
        "tipos_orden": tipos_presentes,
    }
