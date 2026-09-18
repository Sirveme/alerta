"""
webapp/excel_export.py — alerta.pe · Exportaciones a Excel (.xlsx)
═══════════════════════════════════════════════════════════════════════
Constructores de libros openpyxl PROFESIONALES (no CSV crudo) para:

  A. Buzón de un cliente  → una fila por notificación   (libro_buzon_cliente)
  B. Cartera del estudio  → una fila por cliente + hoja resumen (libro_cartera)

Este módulo NO decide el alcance (scope): recibe SOLO las filas que el router
ya filtró según lo que la persona puede ver. Toda la lógica de scope vive en
los routers (reusa la de las vistas). Aquí solo se da forma y formato.

Diseño: fuente Arial, encabezados en banda azul, filas cebra, tipos correctos
(fechas como fecha, montos como número S/, conteos como entero), anchos
adecuados, panel congelado y autofiltro. Los totales son FÓRMULAS (Excel las
recalcula al abrir).
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .core import TZ_LIMA

# ── Paleta / tipografía ────────────────────────────────────────────────
FUENTE = "Arial"
AZUL = "1F3A5F"          # banda de encabezado + títulos
AZUL_FG = "FFFFFF"
CEBRA = "F2F5F9"         # fila alterna
BORDE = "D0D7E2"
GRIS = "6B7280"          # metadatos

_F_TITULO = Font(name=FUENTE, size=15, bold=True, color=AZUL)
_F_META = Font(name=FUENTE, size=10, color=GRIS)
_F_HEAD = Font(name=FUENTE, size=10, bold=True, color=AZUL_FG)
_F_CELDA = Font(name=FUENTE, size=10)
_F_TOTAL = Font(name=FUENTE, size=10, bold=True)
_FILL_HEAD = PatternFill("solid", fgColor=AZUL)
_FILL_CEBRA = PatternFill("solid", fgColor=CEBRA)
_FILL_TOTAL = PatternFill("solid", fgColor="E6EBF2")
_LADO = Side(style="thin", color=BORDE)
_BORDE_CELDA = Border(left=_LADO, right=_LADO, top=_LADO, bottom=_LADO)
_CENTRO = Alignment(horizontal="center", vertical="center")
_IZQ = Alignment(horizontal="left", vertical="center", wrap_text=False)

FMT_MONEDA = '"S/" #,##0.00;[Red]-"S/" #,##0.00;"S/" 0.00'
FMT_ENTERO = "#,##0"
FMT_FECHA = "DD/MM/YYYY"


def _slug(texto: str, tope: int = 42) -> str:
    """'Estudio Contable Ñañez S.A.C.' → 'Estudio_Contable_Nanez_SAC' (nombre de archivo ASCII)."""
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_")
    return (base[:tope] or "export").strip("_")


def hoy_lima():
    return datetime.now(TZ_LIMA)


def fecha_excel(dt: datetime | None):
    """datetime (tz-aware o no) → datetime naive en hora Lima, para celda de fecha."""
    if not dt:
        return None
    v = dt.astimezone(TZ_LIMA) if dt.tzinfo else dt.replace(tzinfo=TZ_LIMA)
    return v.replace(tzinfo=None)


def nombre_archivo(prefijo: str, etiqueta: str) -> str:
    return f"{prefijo}_{_slug(etiqueta)}_{hoy_lima():%Y-%m-%d}.xlsx"


# ── Escritura genérica de tabla ─────────────────────────────────────────
# columnas: lista de dicts {clave, titulo, ancho, tipo, total?}
#   tipo ∈ {"texto","entero","moneda","fecha"}; total=True → footer con SUMA.
def _escribir_tabla(ws, columnas, filas, fila_head, total_footer=False):
    ncols = len(columnas)
    # Encabezado
    for j, col in enumerate(columnas, start=1):
        c = ws.cell(row=fila_head, column=j, value=col["titulo"])
        c.font = _F_HEAD
        c.fill = _FILL_HEAD
        c.alignment = _CENTRO
        c.border = _BORDE_CELDA
        ws.column_dimensions[get_column_letter(j)].width = col["ancho"]
    # Filas de datos
    r = fila_head
    for i, fila in enumerate(filas):
        r = fila_head + 1 + i
        cebra = (i % 2 == 1)
        for j, col in enumerate(columnas, start=1):
            val = fila.get(col["clave"])
            c = ws.cell(row=r, column=j)
            tipo = col["tipo"]
            if tipo == "moneda":
                c.value = val
                c.number_format = FMT_MONEDA
                c.alignment = Alignment(horizontal="right", vertical="center")
            elif tipo == "entero":
                c.value = val
                c.number_format = FMT_ENTERO
                c.alignment = _CENTRO
            elif tipo == "fecha":
                c.value = val
                c.number_format = FMT_FECHA
                c.alignment = _CENTRO
            else:
                c.value = val
                c.alignment = _IZQ
            c.font = _F_CELDA
            c.border = _BORDE_CELDA
            if cebra:
                c.fill = _FILL_CEBRA
    fila_fin = fila_head + len(filas)   # última fila de datos (== head si no hay filas)
    # Footer de totales (fórmulas SUMA; Excel recalcula al abrir)
    if total_footer and filas:
        rt = fila_fin + 1
        for j, col in enumerate(columnas, start=1):
            c = ws.cell(row=rt, column=j)
            c.font = _F_TOTAL
            c.fill = _FILL_TOTAL
            c.border = _BORDE_CELDA
            if j == 1:
                c.value = "TOTALES"
                c.alignment = _IZQ
            elif col["tipo"] in ("entero", "moneda"):
                L = get_column_letter(j)
                c.value = f"=SUM({L}{fila_head+1}:{L}{fila_fin})"
                c.number_format = FMT_MONEDA if col["tipo"] == "moneda" else FMT_ENTERO
                c.alignment = Alignment(horizontal="right" if col["tipo"] == "moneda" else "center",
                                        vertical="center")
        fila_fin = rt
    # Panel congelado (bajo el encabezado) + autofiltro sobre la tabla
    ws.freeze_panes = ws.cell(row=fila_head + 1, column=1)
    ws.auto_filter.ref = f"A{fila_head}:{get_column_letter(ncols)}{fila_head + len(filas)}"
    return fila_fin


def _bloque_titulo(ws, titulo, metas, ncols):
    """Filas 1..n: título grande + líneas de metadatos (fusionadas a lo ancho)."""
    ultima = get_column_letter(ncols)
    ws.merge_cells(f"A1:{ultima}1")
    t = ws["A1"]; t.value = titulo; t.font = _F_TITULO
    t.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 22
    fila = 2
    for m in metas:
        ws.merge_cells(f"A{fila}:{ultima}{fila}")
        c = ws.cell(row=fila, column=1, value=m); c.font = _F_META
        c.alignment = Alignment(horizontal="left", vertical="center")
        fila += 1
    return fila + 1   # deja una fila en blanco antes de la tabla


# ═════════════════════════ A. BUZÓN DE UN CLIENTE ═════════════════════════
COLS_BUZON = [
    {"clave": "fecha",    "titulo": "Fecha",            "ancho": 12, "tipo": "fecha"},
    {"clave": "tipo",     "titulo": "Tipo / categoría", "ancho": 26, "tipo": "texto"},
    {"clave": "tributo",  "titulo": "Tributo",          "ancho": 11, "tipo": "texto"},
    {"clave": "periodo",  "titulo": "Período",          "ancho": 10, "tipo": "texto"},
    {"clave": "asunto",   "titulo": "Asunto",           "ancho": 60, "tipo": "texto"},
    {"clave": "monto",    "titulo": "Monto (S/)",       "ancho": 14, "tipo": "moneda"},
    {"clave": "estado",   "titulo": "Estado",           "ancho": 12, "tipo": "texto"},
    {"clave": "urgencia", "titulo": "Urgencia",         "ancho": 14, "tipo": "texto"},
    {"clave": "adjuntos", "titulo": "Adjuntos",         "ancho": 10, "tipo": "entero"},
]


def libro_buzon_cliente(razon_social: str, ruc: str, filas: list[dict],
                        generado_por: str = "", alcance: str = "") -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Buzón"
    ws.sheet_view.showGridLines = False
    metas = [
        f"RUC {ruc}",
        f"{len(filas)} notificación(es)  ·  Generado {hoy_lima():%d/%m/%Y %H:%M} (hora Lima)",
    ]
    if generado_por:
        metas.append(f"Exportado por: {generado_por}")
    if alcance:
        metas.append(f"Alcance: {alcance}")
    fila_head = _bloque_titulo(ws, f"Buzón — {razon_social}", metas, len(COLS_BUZON))
    _escribir_tabla(ws, COLS_BUZON, filas, fila_head, total_footer=False)
    return wb


# ═════════════════════════ B. CARTERA DEL ESTUDIO ═════════════════════════
COLS_CARTERA = [
    {"clave": "razon",     "titulo": "Razón social",       "ancho": 40, "tipo": "texto"},
    {"clave": "ruc",       "titulo": "RUC",                "ancho": 14, "tipo": "texto"},
    {"clave": "asistente", "titulo": "Asistente asignado", "ancho": 24, "tipo": "texto"},
    {"clave": "coactiva",  "titulo": "Cbza. coactiva",     "ancho": 13, "tipo": "entero"},
    {"clave": "orden_pago", "titulo": "Órdenes de pago",   "ancho": 14, "tipo": "entero"},
    {"clave": "multa",     "titulo": "Multas",             "ancho": 9,  "tipo": "entero"},
    {"clave": "pago",      "titulo": "Pagos",              "ancho": 9,  "tipo": "entero"},
    {"clave": "otros",     "titulo": "Otros",              "ancho": 9,  "tipo": "entero"},
    {"clave": "total",     "titulo": "Total docs.",        "ancho": 11, "tipo": "entero"},
    {"clave": "urgentes",  "titulo": "Urgentes",           "ancho": 10, "tipo": "entero"},
    {"clave": "estado",    "titulo": "Estado",             "ancho": 16, "tipo": "texto"},
]
# Índices de columna (1-based) para las fórmulas de Total y del resumen.
_C_COACTIVA, _C_OTROS = 4, 8          # rango D:H que suma "Total docs."
_C_TOTAL, _C_URGENTES = 9, 10


def libro_cartera(estudio_nombre: str, clientes: list[dict],
                  generado_por: str = "", alcance: str = "") -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Cartera"
    ws.sheet_view.showGridLines = False

    # Total docs. por fila = SUMA de los 5 grupos (D:H). Urgentes NO se suma
    # (es una clasificación transversal → duplicaría el conteo).
    fila_head = 5
    filas_out = []
    for c in clientes:
        filas_out.append({
            "razon": c["razon_social"], "ruc": c["ruc"],
            "asistente": c.get("responsable") or "Sin asignar",
            "coactiva": c["coactiva"], "orden_pago": c["orden_pago"],
            "multa": c["multa"], "pago": c["pago"], "otros": c["otros"],
            "total": None,   # se sobreescribe con fórmula abajo
            "urgentes": c["urgentes"], "estado": c["estado_label"],
        })
    metas = [
        f"{len(clientes)} cliente(s) en la cartera  ·  Generado {hoy_lima():%d/%m/%Y %H:%M} (hora Lima)",
    ]
    if generado_por:
        metas.append(f"Exportado por: {generado_por}")
    if alcance:
        metas.append(f"Alcance: {alcance}")
    _bloque_titulo(ws, f"Cartera — {estudio_nombre}", metas, len(COLS_CARTERA))
    fila_fin_datos = fila_head + len(filas_out)
    _escribir_tabla(ws, COLS_CARTERA, filas_out, fila_head, total_footer=True)

    # Reemplaza la celda "Total docs." de cada fila por una FÓRMULA =SUM(D:H).
    Lc = get_column_letter(_C_COACTIVA); Lo = get_column_letter(_C_OTROS)
    for i in range(len(filas_out)):
        r = fila_head + 1 + i
        cell = ws.cell(row=r, column=_C_TOTAL)
        cell.value = f"=SUM({Lc}{r}:{Lo}{r})"
        cell.number_format = FMT_ENTERO

    # ── Hoja RESUMEN (primera pestaña) con totales por fórmula sobre 'Cartera'. ──
    res = wb.create_sheet("Resumen", 0)
    res.sheet_view.showGridLines = False
    res.column_dimensions["A"].width = 30
    res.column_dimensions["B"].width = 18
    res["A1"] = f"Resumen de cartera — {estudio_nombre}"
    res["A1"].font = _F_TITULO
    res.merge_cells("A1:B1")
    meta_lines = [f"Generado {hoy_lima():%d/%m/%Y %H:%M} (hora Lima)"]
    if generado_por:
        meta_lines.append(f"Exportado por: {generado_por}")
    if alcance:
        meta_lines.append(f"Alcance: {alcance}")
    rr = 2
    for m in meta_lines:
        res.cell(row=rr, column=1, value=m).font = _F_META
        res.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=2)
        rr += 1
    rr += 1

    def _kv(label, valor, fmt=FMT_ENTERO, formula=False):
        nonlocal rr
        a = res.cell(row=rr, column=1, value=label); a.font = _F_CELDA; a.border = _BORDE_CELDA
        a.alignment = _IZQ
        b = res.cell(row=rr, column=2)
        b.value = valor; b.font = _F_TOTAL; b.border = _BORDE_CELDA
        b.number_format = fmt; b.alignment = _CENTRO
        rr += 1

    d0, d1 = fila_head + 1, fila_fin_datos    # rango de datos en 'Cartera'
    tiene = len(filas_out) > 0
    def _suma(col_idx):
        if not tiene:
            return 0
        L = get_column_letter(col_idx)
        return f"=SUM(Cartera!{L}{d0}:{L}{d1})"
    _kv("Clientes en la cartera", len(filas_out))
    _kv("Cobranza coactiva", _suma(_C_COACTIVA))
    _kv("Órdenes de pago", _suma(5))
    _kv("Multas", _suma(6))
    _kv("Documentos (total)", _suma(_C_TOTAL))
    _kv("Notificaciones urgentes", _suma(_C_URGENTES))

    return wb


def libro_a_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
