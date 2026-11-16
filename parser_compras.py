# -*- coding: utf-8 -*-
"""
parser_compras.py — alerta.pe  (proyecto vistas-club)
═══════════════════════════════════════════════════════════════════════
Parsers PUROS (JSON de SUNAT → dicts; sin red ni BD) del Registro de Compras
Electrónico (RCE / SIRE), modalidad "propuesta".

Fuente real (verificada contra los volcados comp_diag_*):
  · LISTA  (una cabecera por CPE, PAGINADA, perPage≈20):
      GET api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros/rce/propuesta/
          web/propuesta/{periodo}/busqueda?codTipoOpe=&page=&perPage=
      → {"paginacion":{page,perPage,totalRegistros}, "registros":[...], "totales":{...}}
  · DETALLE (ítems de UN CPE):
      → {"comprobantes":[ {"datosEmisor":…, "datosReceptor":…,
                           "informacionItems":[ {desItem, mtoValUnitario, …} ]} ]}

NOTA de descripción: `desItem` trae basura con separador '@#@'
(p.ej. "COCA COLA VR 1500X08@#@  1-  @#@31.44@#@.00@#@.00@#@.00@#@39.30").
`limpiar_desc` se queda SOLO con la descripción (lo anterior al primer '@#@').

alerta.pe SOLO CONSULTA (REGLA DURA SOL): lee la propuesta, nunca la acepta ni
registra. Estos parsers no tocan SOL ni BD.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

PARSER_VERSION = "compras-1"

_RE_ESPACIOS = re.compile(r"\s+")


# ───────────────────────── helpers ─────────────────────────
def _dec(x) -> Decimal | None:
    """Número → Decimal (o None). Tolera str con comas de miles y vacío."""
    if x is None or x == "":
        return None
    try:
        if isinstance(x, str):
            x = x.replace(",", "").strip()
            if not x:
                return None
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fecha(s) -> date | None:
    """Acepta 'AAAA-MM-DD' (lista) o 'DD/MM/AAAA' (detalle)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        a, mes, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
        if not m:
            return None
        d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(a, mes, d)
    except ValueError:
        return None


def _bool_ind(v) -> bool | None:
    """'1'→True, '0'/''→False, None→None. (Indicadores S/N de SUNAT.)"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return False
    return s in ("1", "S", "s", "true", "True")


def limpiar_desc(desitem: str | None) -> str | None:
    """Deja solo la descripción legible del ítem (antes del primer '@#@')."""
    if not desitem:
        return None
    txt = str(desitem).split("@#@", 1)[0]
    txt = _RE_ESPACIOS.sub(" ", txt).strip()
    return txt[:300] or None


def _tipo_cambio(reg) -> Decimal | None:
    """tipoCambio viene como objeto {mtoTipoCambio: n, …} o a veces escalar."""
    tc = reg.get("tipoCambio")
    if isinstance(tc, dict):
        return _dec(tc.get("mtoTipoCambio"))
    return _dec(tc)


# ───────────────────────── 1) LISTA (cabeceras) ─────────────────────────
def parse_lista_compras(data: dict) -> dict:
    """Parsea una PÁGINA de la propuesta RCE. Devuelve:
      {"cabeceras":[dict…], "totales":dict, "paginacion":dict}
    Cada cabecera es un dict listo para upsert en comprobantes_compra (sin ids:
    contribuyente_id/estudio_id los pone el loader)."""
    regs = (data or {}).get("registros") or []
    cabeceras = []
    for r in regs:
        m = r.get("montos") or {}
        cabeceras.append({
            "periodo": r.get("perTributario"),
            "cod_car": r.get("codCar"),
            "cod_tipo_cdp": r.get("codTipoCDP"),
            "desc_tipo_cdp": r.get("desTipoCDP"),
            "serie": r.get("numSerieCDP"),
            "numero": str(r.get("numCDP")) if r.get("numCDP") is not None else None,
            "numero_final": (str(r.get("numCDPRangoFinal"))
                             if r.get("numCDPRangoFinal") else None),
            "fecha_emision": _fecha(r.get("fecEmision")),
            "fecha_vencimiento": _fecha(r.get("fecVencPag")),
            "tipo_doc_proveedor": r.get("codTipoDocIdentidadProveedor"),
            "ruc_proveedor": r.get("numDocIdentidadProveedor"),
            "razon_social_proveedor": (r.get("nomRazonSocialProveedor") or "").strip()
                                      or None,
            "moneda": r.get("codMoneda"),
            "tipo_cambio": _tipo_cambio(r),
            "cod_estado": r.get("codEstadoComprobante"),
            "estado": r.get("desEstadoComprobante"),
            "base_gravada": _dec(m.get("mtoBIGravadaDG")),
            "igv": _dec(m.get("mtoIgvIpmDG")),
            "base_exon_inaf": _dec(m.get("mtoBIGravadaDNG")),
            "valor_adq_ng": _dec(m.get("mtoValorAdqNG")),
            "isc": _dec(m.get("mtoISC")),
            "icbper": _dec(m.get("mtoIcbp")),
            "otros_tributos": _dec(m.get("mtoOtrosTrib")),
            "importe_total": _dec(m.get("mtoTotalCp")),
            "ind_detraccion": _bool_ind(r.get("indDetraccion")),
            "ind_oper_gratuita": _bool_ind(r.get("indOperGratuita")),
            "num_inconsistencias": r.get("numInconsistencias"),
        })
    return {"cabeceras": cabeceras,
            "totales": (data or {}).get("totales") or {},
            "paginacion": (data or {}).get("paginacion") or {}}


def clave_cpe(cab: dict) -> tuple:
    """Clave natural de un comprobante (para cruzar lista↔detalle e idempotencia):
    (ruc_proveedor, cod_tipo_cdp, serie, numero)."""
    return (cab.get("ruc_proveedor"), cab.get("cod_tipo_cdp"),
            cab.get("serie"), cab.get("numero"))


# ───────────────────────── 2) DETALLE (ítems) ─────────────────────────
def parse_detalle_items(data: dict) -> dict:
    """Parsea el Detalle de UN CPE. Devuelve:
      {"clave":(ruc_prov,tipo,serie,numero), "proveedor":{ruc,razon},
       "items":[dict…]}
    Cada ítem: {linea, codigo, descripcion, unidad, desc_unidad, cantidad,
    valor_unitario, descuento, icbper, importe_total}."""
    comps = (data or {}).get("comprobantes") or []
    if not comps:
        return {"clave": None, "proveedor": {}, "items": []}
    c = comps[0]
    emi = c.get("datosEmisor") or {}
    ruc_prov = emi.get("numRuc")
    tipo = c.get("codCpe")
    serie = c.get("numSerie")
    numero = str(c.get("numCpe")) if c.get("numCpe") is not None else None
    items = []
    for i, it in enumerate(c.get("informacionItems") or [], start=1):
        items.append({
            "linea": i,
            "codigo": (it.get("desCodigo") or "").strip() or None,
            "descripcion": limpiar_desc(it.get("desItem")),
            "unidad": it.get("codUnidadMedida"),
            "desc_unidad": it.get("desUnidadMedida"),
            "cantidad": _dec(it.get("cntItems")),
            "valor_unitario": _dec(it.get("mtoValUnitario")),
            "descuento": _dec(it.get("mtoDesc")),
            "icbper": _dec(it.get("mtoICBPER")),
            "importe_total": _dec(it.get("mtoImpTotal")),
        })
    return {
        "clave": (ruc_prov, tipo, serie, numero),
        "proveedor": {"ruc": ruc_prov,
                      "razon": (emi.get("desRazonSocialEmis") or "").strip() or None},
        "items": items,
    }
