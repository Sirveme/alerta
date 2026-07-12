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

import re

from sqlalchemy import select, func

from models import DocumentoValorado, Contribuyente, Notificacion, TipoValorado
from clasificacion import COACTIVO_NO_SUMA


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
    r"(?:importe\s*(?:total|pagado)?|total\s*(?:pagado|a\s*pagar)?)\s*:?\s*S?\s*/?\s*([\d.,]+)", re.I)
# Frontera: corta el capturado al llegar a la siguiente etiqueta del formulario
# (o a 2+ espacios / salto de línea). Robusto aunque el pdf_texto venga en 1 línea.
_PG_STOP = (r"(?=\s{2,}|\n|$|\bimporte\b|\bbanco\b|\bfecha\b|\bnumero\b|\bn[°ºo]\b|"
            r"\bc[oó]digo\b|\bruc\b|\bper[ií]odo\b|\bdocumento\b|\bvalor\b|\boperaci)")
_RE_PG_TRIBUTO = re.compile(
    r"(?:tributo|concepto|c[oó]digo\s*de\s*tributo)\s*:?\s*(?:\d{3,5}\s*[-–]\s*)?"
    r"([A-Za-zÁÉÍÓÚÑáéíóúñ][A-Za-zÁÉÍÓÚÑáéíóúñ .]{3,55}?)" + _PG_STOP, re.I)
_RE_PG_ORDEN = re.compile(
    r"(?:n[°ºo]?\s*(?:de\s*)?orden|n[uú]mero\s*de\s*orden)\s*:?\s*(\d{6,})", re.I)
_RE_PG_OPERACION = re.compile(
    r"(?:n[°ºo]?\s*(?:de\s*)?operaci[oó]n|n[uú]mero\s*de\s*operaci[oó]n)\s*:?\s*(\d{4,})", re.I)
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
               Notificacion.fecha_publica_sunat, Notificacion.subtipo_coactivo)
        .join(Contribuyente, Contribuyente.id == DocumentoValorado.contribuyente_id)
        .join(Notificacion, Notificacion.id == DocumentoValorado.notificacion_id,
              isouter=True)
        .where(Contribuyente.estudio_id == estudio_id,
               # PAGO no es deuda (zAlerta-69): fuera del panel de deuda.
               DocumentoValorado.tipo_valorado != TipoValorado.PAGO))).all()

    # Estructura intermedia: tipo → ruc → {razon, docs[], total, por_confirmar}
    por_tipo: dict[str, dict] = {}
    for dv, ruc, razon, fecha_pub, subtipo in rows:
        tipo = (dv.tipo_valorado.value if hasattr(dv.tipo_valorado, "value")
                else dv.tipo_valorado)
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
