# -*- coding: utf-8 -*-
"""
cronograma_loader.py — alerta.pe  (proyecto decl-diag · "pagó fuera de plazo")

Parsers + cargadores de las fuentes OFICIALES (diag-first, sobre la estructura
real volcada con diag_publico.py):

  1) CRONOGRAMA anual  (sunat.gob.pe/orientacion/cronogramas/AAAA/cObligacionMensualAAAA.html)
     Tabla: PERÍODO × [0 | 1 | 2y3 | 4y5 | 6y7 | 8y9 | BUENOS CONTRIBUYENTES y UESP].
     → filas cronograma_vencimientos (tipo='base', grupo general/BC).
  2) PADRÓN de Buenos Contribuyentes  (descarga directa BueCont_TXT.zip → TXT
     'RUC\tNombre\tA partir del\tNro.Resolución'). → padron_buenos_contribuyentes.
  3) PRÓRROGAS  (orientacion.sunat.gob.pe/Prorroga-…): tablas 'Último dígito → fecha';
     el PERÍODO y el ÁMBITO (a quién aplica) están en el encabezado de prosa y
     varían por resolución → se extraen BLOQUES (contexto + filas) y el HUMANO los
     mapea/confirma en /interno (no se auto-asigna el ámbito: evita la alerta falsa).

Los `parse_*` son PUROS (HTML/texto → dicts, sin red ni BD). Los `guardar_*`
hacen upsert (se usan al CONFIRMAR en /interno; requieren la migración aplicada).
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

PARSER_VERSION = "crono-1"

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
    "ago": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _limpia(txt):
    return re.sub(r"\s+", " ", (txt or "").replace("\xa0", " ")).strip()


def _periodo_label_a_aaaamm(label: str):
    """'Ene-2026' / 'Ene 2026' → '202601'. None si no calza."""
    t = _limpia(label).lower()
    m = re.search(r"([a-z]{3})[\s\-]*([12]\d{3})", t)
    if not m:
        return None
    mes = _MESES.get(m.group(1)[:3])
    if not mes:
        return None
    return f"{int(m.group(2)):04d}{mes:02d}"


def _fecha_dmabbr(celda: str, anio_periodo: int, mes_periodo: int):
    """'16 Feb' / '18 Ene 2027' → date. Si no trae año, lo infiere del período:
    el vencimiento cae el mes siguiente; si el mes de venc < mes del período →
    año+1 (caso Dic → Ene del año siguiente)."""
    t = _limpia(celda).lower()
    m = re.search(r"(\d{1,2})\s*([a-z]{3})\.?\s*([12]\d{3})?", t)
    if not m:
        return None
    dia = int(m.group(1))
    mes = _MESES.get(m.group(2)[:3])
    if not mes:
        return None
    anio = int(m.group(3)) if m.group(3) else anio_periodo
    if not m.group(3) and mes < mes_periodo:
        anio = anio_periodo + 1
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def _digitos_de_encabezado(txt: str):
    """'0'→[0]; '2 y 3'→[2,3]; 'BUENOS CONTRIBUYENTES y UESP'→'BC'."""
    t = _limpia(txt).lower()
    if "buen" in t or "uesp" in t:
        return "BC"
    ds = [int(d) for d in re.findall(r"\d", t)]
    return ds or None


# ───────────────────────── 1) CRONOGRAMA ─────────────────────────
def parse_cronograma(html: str, anio: int, num_resolucion: str | None = None,
                     fuente: str | None = None) -> list[dict]:
    """Devuelve filas listas para cronograma_vencimientos (tipo='base'):
    {anio, periodo, digito_ruc, grupo, fecha_vencimiento, tipo, ambito, vigente,
     num_resolucion, fuente}. Una por (período, dígito, grupo)."""
    s = BeautifulSoup(html or "", "html.parser")
    tabla = _tabla_cronograma(s)
    if tabla is None:
        return []
    filas_html = tabla.find_all("tr")
    # Localiza la fila de encabezados de dígitos ('0','1','2 y 3',...).
    cols = None
    idx_header = None
    for i, tr in enumerate(filas_html):
        celdas = [_limpia(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        mapeo = [_digitos_de_encabezado(c) for c in celdas]
        if sum(1 for mcol in mapeo if mcol) >= 4 and any(
                m == "BC" for m in mapeo):
            cols = mapeo
            idx_header = i
            break
    if cols is None:
        return []

    out: list[dict] = []
    for tr in filas_html[idx_header + 1:]:
        celdas = [_limpia(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if len(celdas) < 2:
            continue
        periodo = _periodo_label_a_aaaamm(celdas[0])
        if not periodo:
            continue
        anio_p, mes_p = int(periodo[:4]), int(periodo[4:])
        # La fila de encabezados son SOLO los 7 grupos de dígito (sin columna de
        # período); las de datos traen período + 7 fechas. Alinean 1:1 tras quitar
        # la 1ª celda (período).
        datos = celdas[1:]
        for col_idx, mapeo in enumerate(cols):
            if not mapeo or col_idx >= len(datos):
                continue
            fecha = _fecha_dmabbr(datos[col_idx], anio_p, mes_p)
            if not fecha:
                continue
            if mapeo == "BC":
                # BC/UESP: misma fecha para todos los dígitos.
                for d in range(10):
                    out.append(_fila_crono(anio, periodo, d,
                               "buenos_contribuyentes", fecha, num_resolucion, fuente))
            else:
                for d in mapeo:
                    out.append(_fila_crono(anio, periodo, d, "general", fecha,
                               num_resolucion, fuente))
    return out


def _fila_crono(anio, periodo, digito, grupo, fecha, num_resolucion, fuente):
    return {"anio": anio, "periodo": periodo, "digito_ruc": digito,
            "grupo": grupo, "fecha_vencimiento": fecha, "tipo": "base",
            "ambito": "general", "vigente": True,
            "num_resolucion": num_resolucion, "fuente": fuente}


def _tabla_cronograma(s: BeautifulSoup):
    """La tabla cuyo encabezado menciona PERÍODO + ÚLTIMO DÍGITO."""
    for t in s.find_all("table"):
        cab = _limpia(t.get_text(" "))[:200].lower()
        if "per" in cab and ("dígito" in cab or "digito" in cab):
            return t
    tabs = s.find_all("table")
    return tabs[0] if tabs else None


# ───────────────────────── 2) PADRÓN BC (TXT) ─────────────────────────
_RE_BC = re.compile(
    r"^\s*(\d{11})\t+(.+?)\t+(\d{2}/\d{2}/\d{4})\t+(\S+)\s*$")


def parse_padron_bc_txt(texto: str, fuente: str | None = None) -> list[dict]:
    """Parsea el TXT del padrón BC (BueCont_TXT): una línea por RUC,
    'RUC\\tNombre\\tA partir del\\tNro.Resolución'. Tolera comillas en el nombre."""
    out: list[dict] = []
    for linea in (texto or "").splitlines():
        m = _RE_BC.match(linea)
        if not m:
            # Fallback: separa por tabs y valida RUC + fecha.
            partes = [p.strip() for p in linea.split("\t") if p.strip()]
            if len(partes) >= 4 and re.fullmatch(r"\d{11}", partes[0]) and \
               re.fullmatch(r"\d{2}/\d{2}/\d{4}", partes[2]):
                m = None
                ruc, nombre, fdesde, resol = partes[0], partes[1], partes[2], partes[3]
            else:
                continue
        else:
            ruc, nombre, fdesde, resol = m.group(1), m.group(2), m.group(3), m.group(4)
        out.append({"ruc": ruc, "grupo": "buenos_contribuyentes",
                    "vigente": True, "vigencia_desde": _fecha_dmy(fdesde),
                    "nombre": nombre.strip().strip('"').strip(),
                    "num_resolucion": resol, "fuente": fuente})
    return out


def _fecha_dmy(txt):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


# ───────────────────────── 3) PRÓRROGAS (bloques) ─────────────────────────
def parse_prorrogas(html: str) -> list[dict]:
    """Extrae BLOQUES de prórroga = {contexto, filas:[{etiqueta, digito, fecha}]}.
    El PERÍODO y el ÁMBITO (a quién aplica) están en `contexto` (prosa) y varían
    por resolución → NO se auto-asignan: el humano los mapea/confirma en /interno.
    """
    s = BeautifulSoup(html or "", "html.parser")
    bloques: list[dict] = []
    for t in s.find_all("table"):
        ths = " ".join(_limpia(th.get_text(" ")) for th in t.find_all("th")).lower()
        txt = _limpia(t.get_text(" ")).lower()
        if "dígito" not in (ths + txt) and "digito" not in (ths + txt):
            continue
        if "vencimiento" not in (ths + txt):
            continue
        filas = []
        for tr in t.find_all("tr"):
            celdas = [_limpia(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if len(celdas) < 2:
                continue
            etiqueta = celdas[0]
            if "dígito" in etiqueta.lower() or "digito" in etiqueta.lower():
                continue  # fila de encabezado
            fecha = _fecha_dmy(celdas[1])
            dig = etiqueta if re.fullmatch(r"\d", etiqueta) else None
            if fecha is None and dig is None:
                continue
            filas.append({"etiqueta": etiqueta,
                          "digito": int(dig) if dig is not None else None,
                          "fecha": fecha})
        if not filas:
            continue
        bloques.append({"contexto": _contexto_previo(t), "filas": filas})
    return bloques


def _contexto_previo(t) -> str:
    """Heading/párrafo más cercano antes de la tabla (para que el humano sepa a
    quién aplica la prórroga)."""
    prev = t.find_previous(["h1", "h2", "h3", "h4", "h5", "strong", "b",
                            "caption", "p"])
    for _ in range(6):
        if prev is None:
            break
        txt = _limpia(prev.get_text(" "))
        if len(txt) >= 12 and not re.fullmatch(r"[\d/ ]+", txt):
            return txt[:200]
        prev = prev.find_previous(["h1", "h2", "h3", "h4", "h5", "strong",
                                   "b", "caption", "p"])
    return ""


# ───────────────────────── Fetch (páginas públicas, HTML estático) ─────────
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def fetch_html(url: str, timeout: int = 40) -> str:
    """GET de una página pública (cronograma/prórrogas). urllib + UA de navegador."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec - URL oficial
        data = r.read()
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def descargar_padron_bc_txt(url_zip: str, timeout: int = 120) -> str:
    """Baja BueCont_TXT.zip y devuelve el TXT interno (el padrón completo)."""
    import io
    import urllib.request
    import zipfile
    req = urllib.request.Request(url_zip, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec - URL oficial
        blob = r.read()
    zf = zipfile.ZipFile(io.BytesIO(blob))
    nombre = next((n for n in zf.namelist() if n.lower().endswith(".txt")),
                  zf.namelist()[0] if zf.namelist() else None)
    if not nombre:
        return ""
    for enc in ("utf-8", "latin-1"):
        try:
            return zf.read(nombre).decode(enc)
        except UnicodeDecodeError:
            continue
    return zf.read(nombre).decode("utf-8", "replace")


# ───────────────────────── Guardar (upsert; al CONFIRMAR en /interno) ───────
async def guardar_cronograma(filas: list[dict]) -> int:
    """Upsert de filas de cronograma_vencimientos. Idempotente por
    (periodo, digito_ruc, grupo, ambito, num_resolucion). Requiere la migración.
    NO recomputa vigencia de prórrogas aquí (ver recomputar_vigencia_cronograma)."""
    if not filas:
        return 0
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from db import get_session
    from models import CronogramaVencimiento, nuevo_uuid
    ahora = datetime.now(timezone.utc)
    n = 0
    async with get_session() as session:
        for f in filas:
            vals = {k: f.get(k) for k in (
                "anio", "periodo", "digito_ruc", "grupo", "fecha_vencimiento",
                "tipo", "ambito", "ambito_detalle", "resolucion_fecha", "vigente",
                "num_resolucion", "fuente")}
            vals = {k: v for k, v in vals.items() if v is not None}
            vals.setdefault("id", nuevo_uuid())
            vals["cargado_at"] = ahora
            stmt = pg_insert(CronogramaVencimiento).values(**vals)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_cronograma_celda_resolucion",
                set_={"fecha_vencimiento": stmt.excluded.fecha_vencimiento,
                      "vigente": stmt.excluded.vigente,
                      "fuente": stmt.excluded.fuente,
                      "cargado_at": ahora})
            await session.execute(stmt)
            n += 1
        await session.commit()
    return n


async def guardar_padron_bc(filas: list[dict]) -> int:
    """Upsert del padrón BC. Idempotente por `ruc`. Requiere la migración."""
    if not filas:
        return 0
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from db import get_session
    from models import PadronBuenContribuyente, nuevo_uuid
    ahora = datetime.now(timezone.utc)
    n = 0
    async with get_session() as session:
        for f in filas:
            vals = {"id": nuevo_uuid(), "ruc": f["ruc"],
                    "grupo": f.get("grupo", "buenos_contribuyentes"),
                    "vigente": f.get("vigente", True),
                    "vigencia_desde": f.get("vigencia_desde"),
                    "num_resolucion": f.get("num_resolucion"),
                    "fuente": f.get("fuente"), "cargado_at": ahora}
            vals = {k: v for k, v in vals.items() if v is not None}
            stmt = pg_insert(PadronBuenContribuyente).values(**vals)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_padron_bc_ruc",
                set_={"vigente": stmt.excluded.vigente,
                      "vigencia_desde": stmt.excluded.vigencia_desde,
                      "num_resolucion": stmt.excluded.num_resolucion,
                      "cargado_at": ahora})
            await session.execute(stmt)
            n += 1
        await session.commit()
    return n


# ───────────────────────── CLI de preview (sin BD) ─────────────────────────
def _cli(carpeta: str) -> None:
    import glob
    import json
    import os

    def _leer(patron):
        hits = glob.glob(os.path.join(carpeta, patron))
        return open(hits[0], encoding="utf-8", errors="replace").read() if hits else ""

    base = os.path.basename(carpeta.rstrip("/\\")).lower()
    if base.startswith("cronograma"):
        anio = int(re.search(r"(20\d{2})", base).group(1)) if re.search(r"20\d{2}", base) else 0
        filas = parse_cronograma(_leer("cronograma_*.html") or _leer("*.html"),
                                 anio, num_resolucion="281-2022/SUNAT")
        print(f"CRONOGRAMA {anio}: {len(filas)} filas")
        gen = [f for f in filas if f["grupo"] == "general"]
        bc = [f for f in filas if f["grupo"] == "buenos_contribuyentes"]
        print(f"  general={len(gen)}  buenos_contribuyentes={len(bc)}")
        for f in sorted(gen, key=lambda x: (x["periodo"], x["digito_ruc"]))[:12]:
            print(f"   {f['periodo']} díg {f['digito_ruc']} {f['grupo']}"
                  f" -> {f['fecha_vencimiento']}")
    elif base.startswith("prorrogas"):
        bloques = parse_prorrogas(_leer("prorrogas_*.html") or _leer("*.html"))
        print(f"PRÓRROGAS: {len(bloques)} bloque(s)")
        for b in bloques:
            print(f"  · contexto: {b['contexto'][:90]}")
            print(f"    filas: " + ", ".join(
                f"{x['etiqueta']}={x['fecha']}" for x in b["filas"][:6])
                + (" …" if len(b["filas"]) > 6 else ""))
    elif base.startswith("bc_padron"):
        print("PADRÓN BC: descarga directa /descarga/BueCont/BueCont_TXT.zip")
        print("  (parse_padron_bc_txt espera el TXT del zip; demo con muestra)")
    else:
        print("carpeta no reconocida (usa cronograma_*/prorrogas_*/bc_padron_*)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("uso: python cronograma_loader.py <carpeta_volcado>")
        raise SystemExit(2)
    _cli(sys.argv[1])
