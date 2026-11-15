# -*- coding: utf-8 -*-
"""
Parser de "Declaraciones y Pagos" de SOL (proyecto decl-diag) — HTML-first.

Funciones PURAS sobre el HTML ya descargado (sin red, sin BD): fáciles de probar
contra los volcados de `decl-diag2` en `descargas/decl_diag2_*/`.

Fuentes:
  · 01 Consulta general  → `parse_consulta_general(html)`  (rejilla por PERÍODO,
    con banco, código-tributo e IMPORTE PAGADO en HTML). Es la fuente de la fila
    verde.
  · 02 Consulta de DDJJ y pagos → `parse_djj_pagos(html)`  (lista de
    presentaciones por FECHA DE PRESENTACIÓN + referencia a la constancia PDF).

NO escribe en la BD ni descarga PDFs. El desglose fino por tributo (constancia
0621, PDF) es FASE POSTERIOR y va a `DeclaracionTributo`.

Enmascarado: la salida no contiene token, usuario SOL ni clave. El RUC y la razón
social son datos propios de la app (no se enmascaran); el DNI no aparece aquí.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

PARSER_VERSION = "decl-1"

# Formularios que son PAGOS/BOLETAS (no declaraciones juradas).
# 1662 = "BOLETA DE PAGO - VIRTUAL". Se amplía si aparecen otros en volcados.
PAGO_FORMULARIOS = {"1662", "1663", "1073", "1075", "1662 "}

# Formularios que son DECLARACIONES juradas conocidas (informativo; cualquier
# formulario que NO esté en PAGO_FORMULARIOS se trata como declaración).
DECL_FORMULARIOS = {"0621", "0601", "0616", "0617", "0600", "0610", "0710", "0709"}


# ───────────────────────── helpers de limpieza ─────────────────────────
def _limpia(txt: str | None) -> str | None:
    """Trim; convierte '', '-', '&nbsp;' en None."""
    if txt is None:
        return None
    t = txt.replace("\xa0", " ").strip()
    if t in ("", "-", "--"):
        return None
    return t


def _num_pe(txt: str | None) -> Decimal | None:
    """Número en formato peruano: '1.234,56' → 1234.56 ; '17,00' → 17 ; '0,00' → 0.
    Devuelve None si no hay dígitos. OJO: 0,00 devuelve Decimal('0') (no None),
    para distinguir 'pagó 0' de 'sin dato'."""
    if txt is None:
        return None
    t = txt.replace("\xa0", " ").strip()
    if t in ("", "-", "--"):
        return None
    neg = t.startswith("-") and any(c.isdigit() for c in t)
    t = re.sub(r"[^0-9,.]", "", t)
    if not re.search(r"\d", t):
        return None
    # Peruano: '.' = miles, ',' = decimal.
    t = t.replace(".", "").replace(",", ".")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


def _fecha(txt: str | None) -> date | None:
    """DD/MM/YYYY → date (o None)."""
    if not txt:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt)
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _periodo_ok(p: str | None) -> str | None:
    """Normaliza período a AAAAMM si tiene esa forma."""
    if not p:
        return None
    m = re.search(r"\b(\d{6})\b", p)
    return m.group(1) if m else _limpia(p)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


# ───────────────────────── 01 Consulta general ─────────────────────────
# Columnas reales de la tabla #tblDetalleDeclPagos (volcado 14/11/2026):
#   0 checkbox | 1 Periodo | 2 N°Form | 3 N°Orden | 4 Descripción(form)
#   5 Fecha de Pres. | 6 Banco Receptor | 7 Codigo-Tributo
#   8 Descripción(tributo)/enlace Detalle | 9 Importe pagado
_RE_DET_TRIB = re.compile(
    r"verDetalleTributos\(\s*'[^']*'\s*,\s*'(\d+)'\s*,\s*'(\d+)'\s*,"
    r"\s*'(\d+)'\s*,\s*'(\d+)'", re.S)


def parse_consulta_general(html: str) -> dict:
    """Parsea la pantalla 01. Devuelve:
        {
          'cabecera': {ruc, fecha, razon_social, desde, hasta},
          'filas': [ {raw...} ],          # tal cual, por si se audita
          'declaraciones': [ {...} ],     # formularios DJ (0621…)
          'pagos': [ {...} ],             # boletas/pagos (1662…)
        }
    Las claves de declaraciones/pagos calzan con los modelos
    DeclaracionJurada / PagoTributario.
    """
    s = _soup(html)
    cab = _cabecera_01(s)
    ruc = cab.get("ruc")

    filas: list[dict] = []
    declaraciones: list[dict] = []
    pagos: list[dict] = []

    tabla = s.find("table", id="tblDetalleDeclPagos")
    cuerpo = tabla.find("tbody") if tabla else None
    if cuerpo:
        for tr in cuerpo.find_all("tr"):
            celdas = tr.find_all("td")
            if len(celdas) < 10:
                continue
            periodo = _periodo_ok(celdas[1].get_text())
            cod_form = _limpia(celdas[2].get_text())
            num_orden = _limpia(celdas[3].get_text())
            desc_form = _limpia(celdas[4].get_text())
            fecha_pres = _fecha(celdas[5].get_text())
            banco = _limpia(celdas[6].get_text())
            cod_trib = _limpia(celdas[7].get_text())
            # Enlace a Detalle de Tributos Pagados (HTML, fase posterior).
            det = None
            m = _RE_DET_TRIB.search(str(celdas[8]))
            if m:
                det = {"ruc": m.group(1), "cod_form": m.group(2),
                       "tipnro": m.group(3), "num_orden": m.group(4)}
            importe = _num_pe(celdas[9].get_text())

            if not (periodo and cod_form and num_orden):
                continue

            fila = {
                "periodo": periodo, "cod_formulario": cod_form,
                "num_orden": num_orden, "desc_formulario": desc_form,
                "fecha_presentacion": fecha_pres, "banco_receptor": banco,
                "cod_tributo": cod_trib, "importe": importe,
                "detalle_tributos": det,
            }
            filas.append(fila)

            if _es_pago(cod_form):
                pagos.append({
                    "ruc": ruc, "periodo": periodo,
                    "cod_formulario": cod_form, "desc_formulario": desc_form,
                    "num_orden": num_orden, "fecha_pago": fecha_pres,
                    "banco_receptor": banco, "cod_tributo": cod_trib,
                    "importe": importe, "origen": "01",
                    "detalle_tributos": det,
                })
            else:
                declaraciones.append({
                    "ruc": ruc, "periodo": periodo,
                    "cod_formulario": cod_form, "desc_formulario": desc_form,
                    "num_orden": num_orden,
                    "fecha_presentacion": fecha_pres,
                    "importe_pagado": importe, "origen": "01",
                })

    marcar_vigentes(declaraciones)
    return {"cabecera": cab, "filas": filas,
            "declaraciones": declaraciones, "pagos": pagos}


def _es_pago(cod_form: str | None) -> bool:
    if not cod_form:
        return False
    return cod_form.strip() in PAGO_FORMULARIOS


def _cabecera_01(s: BeautifulSoup) -> dict:
    """RUC / FECHA / razón social / DESDE / HASTA del panel superior de 01."""
    out = {"ruc": None, "fecha": None, "razon_social": None,
           "desde": None, "hasta": None}
    texto = s.get_text(" ", strip=True)
    m = re.search(r"RUC\s*:?\s*(\d{11})", texto)
    if m:
        out["ruc"] = m.group(1)
    m = re.search(r"DESDE\s*:?\s*(\d{6})", texto)
    if m:
        out["desde"] = m.group(1)
    m = re.search(r"HASTA\s*:?\s*(\d{6})", texto)
    if m:
        out["hasta"] = m.group(1)
    m = re.search(r"RAZON SOCIAL\s*:?\s*([^:]+?)\s{2,}", texto)
    if m:
        out["razon_social"] = m.group(1).strip()
    return out


def marcar_vigentes(declaraciones: list[dict]) -> None:
    """Rectificatorias: por (periodo, cod_formulario), la de mayor fecha de
    presentación (desempate por num_orden) queda es_vigente=True; las demás
    es_vigente=False y es_rectificatoria=True. Se CONSERVAN todas. Muta la lista."""
    grupos: dict[tuple, list[dict]] = {}
    for d in declaraciones:
        grupos.setdefault((d["periodo"], d["cod_formulario"]), []).append(d)
    for lista in grupos.values():
        def clave(d):
            return (d.get("fecha_presentacion") or date.min,
                    _orden_int(d.get("num_orden")))
        lista_ord = sorted(lista, key=clave)
        for i, d in enumerate(lista_ord):
            ultimo = (i == len(lista_ord) - 1)
            d["es_vigente"] = ultimo
            d["es_rectificatoria"] = (len(lista_ord) > 1 and not ultimo) or \
                (len(lista_ord) > 1 and ultimo and i > 0)
        # La vigente de un grupo con >1 es también rectificatoria (la última).
        if len(lista_ord) > 1:
            lista_ord[-1]["es_rectificatoria"] = True


def _orden_int(num_orden: str | None) -> int:
    if not num_orden:
        return 0
    d = re.sub(r"\D", "", num_orden)
    return int(d) if d else 0


# ───────────────────────── 02 Consulta de DDJJ y pagos ─────────────────────────
# Cada presentación = una fila. Columnas: # | Fec.Pres. | Periodo | Cod.Form. |
# Desc.Form. | N° Orden | (enlace Comprobante → constanciaNP(num_ord,'cod_for'))
_RE_CONSTANCIA = re.compile(r"constanciaNP\(\s*(\d+)\s*,\s*'(\w+)'\s*\)")
_RE_PAGINADO = re.compile(r"(\d+)\s*a\s*(\d+)\s*de\s*(\d+)")


def parse_djj_pagos(html: str) -> dict:
    """Parsea la pantalla 02. Devuelve:
        {
          'total': int|None,            # 'X a Y de Z' → Z
          'presentaciones': [ {fec_pres, periodo, cod_formulario, desc_formulario,
                               num_orden, constancia:{num_ord,cod_for}|None} ],
        }
    La constancia es un PDF (`obtener_constanciaNP`); aquí solo se referencia, no
    se descarga (fase posterior).
    """
    s = _soup(html)
    total = None
    m = _RE_PAGINADO.search(s.get_text(" ", strip=True))
    if m:
        total = int(m.group(3))

    pres: list[dict] = []
    vistos: set[str] = set()
    # Escaneo por FORMA (robusto ante tablas anidadas): una fila de datos de 02
    # tiene >=7 celdas, col2 = período AAAAMM, col5 = nº de orden (dígitos).
    for tr in s.find_all("tr"):
        celdas = tr.find_all("td", recursive=False)
        if len(celdas) < 7:
            continue
        periodo = _periodo_ok(celdas[2].get_text())
        num_orden = _limpia(celdas[5].get_text())
        if not (periodo and re.fullmatch(r"\d{6}", periodo or "")):
            continue
        if not (num_orden and re.search(r"\d", num_orden)):
            continue
        cod_form = _limpia(celdas[3].get_text())
        if not cod_form:
            continue
        fec = _fecha(celdas[1].get_text())
        desc_form = _limpia(celdas[4].get_text())
        cons = None
        mc = _RE_CONSTANCIA.search(str(celdas[6]))
        if mc:
            cons = {"num_ord": mc.group(1), "cod_for": mc.group(2)}
        if num_orden in vistos:
            continue
        vistos.add(num_orden)
        pres.append({
            "fecha_presentacion": fec, "periodo": periodo,
            "cod_formulario": cod_form, "desc_formulario": desc_form,
            "num_orden": num_orden, "constancia": cons,
            "origen": "02",
        })
    return {"total": total, "presentaciones": pres}


# ───────────────────────── rejilla período × tributo ─────────────────────────
def rejilla_periodos(cg: dict) -> list[dict]:
    """Construye filas de rejilla POR PERÍODO a partir de parse_consulta_general.
    SOLO datos de la fuente: declaró (sí/no + fecha) y pagó (suma de importes del
    período). NO calcula saldos ni deuda. El buzón (semáforo) se añade fuera, en
    la vista, al costado.
    """
    por_periodo: dict[str, dict] = {}
    for d in cg.get("declaraciones", []):
        p = por_periodo.setdefault(d["periodo"], _fila_vacia(d["periodo"]))
        if not d.get("es_vigente", True):
            continue
        p["declaro"] = True
        p["cod_formulario"] = d.get("cod_formulario")
        p["desc_formulario"] = d.get("desc_formulario")
        p["fecha_presentacion"] = d.get("fecha_presentacion")
        if d.get("importe_pagado"):
            p["pagado"] = (p["pagado"] or Decimal("0")) + d["importe_pagado"]
    for pg in cg.get("pagos", []):
        p = por_periodo.setdefault(pg["periodo"], _fila_vacia(pg["periodo"]))
        if pg.get("importe") is not None:
            p["pagado"] = (p["pagado"] or Decimal("0")) + pg["importe"]
        if pg.get("cod_tributo"):
            p["cod_tributos"].add(pg["cod_tributo"])
        p["tiene_pago"] = True
    filas = list(por_periodo.values())
    for f in filas:
        f["cod_tributos"] = sorted(f["cod_tributos"])
    return sorted(filas, key=lambda f: f["periodo"], reverse=True)


def _fila_vacia(periodo: str) -> dict:
    return {"periodo": periodo, "declaro": False, "cod_formulario": None,
            "desc_formulario": None, "fecha_presentacion": None,
            "tiene_pago": False, "pagado": None, "cod_tributos": set()}


# ───────────────────────── CLI de validación (sin BD) ─────────────────────────
def _cli(carpeta: str) -> None:
    """Lee un volcado decl_diag2_* y resume lo parseado. Uso:
        python parser_declaraciones.py descargas/decl_diag2_XXXXXXXXXXX_YYYYMMDD_HHMMSS
    No toca la BD ni la red."""
    import glob
    import os

    def _leer(patron):
        hits = glob.glob(os.path.join(carpeta, patron))
        return open(hits[0], encoding="utf-8", errors="replace").read() if hits else ""

    cg = parse_consulta_general(_leer("01_consulta_general_resultados_frame4.html")
                                or _leer("01_consulta_general*frame4.html"))
    dp = parse_djj_pagos(_leer("02_djj_pagos_resultados_frame4.html")
                         or _leer("02_djj_pagos*frame4.html"))

    print("== CABECERA 01 ==", cg["cabecera"])
    print(f"== 01: {len(cg['declaraciones'])} declaraciones, "
          f"{len(cg['pagos'])} pagos ==")
    for d in cg["declaraciones"]:
        print(f"  DJ  {d['periodo']} F{d['cod_formulario']} "
              f"orden {d['num_orden']} pres {d['fecha_presentacion']} "
              f"vig={d.get('es_vigente')} pagado={d.get('importe_pagado')}")
    for pg in cg["pagos"]:
        print(f"  PAGO {pg['periodo']} F{pg['cod_formulario']} "
              f"trib {pg.get('cod_tributo')} banco {pg.get('banco_receptor')} "
              f"importe {pg.get('importe')}")
    print(f"== 02: total={dp['total']}, "
          f"{len(dp['presentaciones'])} presentaciones (pág. actual) ==")
    print("== REJILLA período × tributo (solo datos de fuente) ==")
    for f in rejilla_periodos(cg):
        print(f"  {f['periodo']}  declaró={'sí' if f['declaro'] else 'no'} "
              f"({f['fecha_presentacion']})  pagado={f['pagado']}  "
              f"tributos={f['cod_tributos']}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("uso: python parser_declaraciones.py <carpeta_volcado>")
        raise SystemExit(2)
    _cli(sys.argv[1])
