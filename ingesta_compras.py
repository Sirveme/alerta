# -*- coding: utf-8 -*-
"""
ingesta_compras.py — alerta.pe  (proyecto vistas-club)
═══════════════════════════════════════════════════════════════════════
Modo `compras-carga <RUC> <periodo>`: lee el Registro de Compras Electrónico
(RCE / SIRE, modalidad "propuesta") + el Detalle de ítems por CPE (api-cpe) y
**PERSISTE** en comprobantes_compra (+ comprobante_items).

La RECOLECCIÓN la hace el módulo YA PROBADO `scraper_sunat_comprobantes` (modo
comp-diag): login SOL, interstitial de contacto, captura del Bearer SIRE/CPE,
paginado de la propuesta RCE y consulta del detalle por CPE
(api-cpe …/consultacpe/comprobantes/{ruc}-{tipo}-{serie}-{numero}-2). Aquí NO se
reescribe esa lógica: se llama a `comp_diag(...)` y se PERSISTE lo que devuelve.

  · SOLO CONSULTA (REGLA DURA SOL): el guardián de aceptación va dentro de comp_diag.
  · Upsert IDEMPOTENTE por (contribuyente, proveedor, tipo, serie, número) y por
    (comprobante, línea). Nunca borra; solo inserta/actualiza.
  · PARCIAL si falta algo (sin acceso / aceptación / detalle incompleto / corte).
  · Corre a MANO (local con `railway run`), nunca en el worker.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from scraper_sunat_playwgth import SunatConfig, log
import scraper_sunat_comprobantes as comp
from models import ComprobanteCompra, ComprobanteItem

PARSER_VERSION = "compras-1"
_OK = ("con_datos", "sin_datos")
_MALOS = ("sin_acceso", "pantalla_de_aceptacion", "error_filtro", "sin_conexion",
          "no_se_pudo_leer", "login")
# Tope de consultas de detalle por corrida (todas, por defecto; el período real es
# chico). `comp_diag` limita las consultas de ítems a este `n`.
COMPRAS_N = int(os.getenv("COMPRAS_N", "100000"))


# ───────────────────────── helpers ─────────────────────────
def _fecha(s) -> date | None:
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


def _nk(ruc, tipo, serie, numero) -> tuple:
    """Clave normalizada para cruzar propuesta↔detalle↔BD, estable entre corridas:
    (ruc, tipo.zfill2, serie.upper, numero-sin-ceros)."""
    nums = re.sub(r"\D", "", str(numero or "0")) or "0"
    return (str(ruc or ""), str(tipo or "").zfill(2),
            str(serie or "").upper(), str(int(nums)))


def _suma(regs, campo) -> float | None:
    tot = 0.0
    hay = False
    for r in regs:
        v = r.get(campo)
        if v is not None:
            try:
                tot += float(v); hay = True
            except (TypeError, ValueError):
                pass
    return round(tot, 2) if hay else None


# ───────────────────────── Recolección (vía comp_diag, en hilo) ─────────
def recolectar_compras(cfg: SunatConfig, periodo: str) -> dict:
    """Llama al recolector probado (comp_diag) y traduce su salida a lo que la capa
    de persistencia necesita. NO reescribe el scraping."""
    out = {"exito": False, "estado": "sin_conexion", "motivo": None,
           "ruc": cfg.ruc, "periodo": periodo, "registros": [], "detalles": {},
           "incidencias": [], "dump": None, "contacto_sin_validar": False,
           "n_consultables": 0}
    try:
        res = comp.comp_diag(cfg, periodo, n=COMPRAS_N)
    except Exception as e:
        out["motivo"] = f"excepcion:{type(e).__name__}"
        out["incidencias"].append(f"corte de recolección: {type(e).__name__}")
        return out

    out["dump"] = res.get("carpeta")
    out["motivo"] = res.get("motivo")
    out["contacto_sin_validar"] = bool(res.get("contacto_sin_validar"))
    if out["contacto_sin_validar"]:
        out["incidencias"].append(
            "contacto_sin_validar: SUNAT exige validar correo/celular (el contador "
            "debe hacerlo en SOL). Se cerró el aviso sin tocar datos.")

    # Guardián: alguna pantalla de aceptación → abortado sin tocar nada.
    for pa in res.get("pantallas") or []:
        if pa.get("estado") == "pantalla_de_aceptacion":
            out["estado"] = "pantalla_de_aceptacion"
            out["motivo"] = pa.get("detalle")
            out["incidencias"].append(
                f"pantalla de aceptación en {pa.get('slug')} (NO se tocó nada).")
            return out

    libro = next((l for l in res.get("libros") or []
                  if l.get("libro") == "compras"), None)
    if libro is None:
        # No se llegó a procesar compras (típicamente sin token).
        if res.get("sin_acceso"):
            out["estado"] = "sin_acceso"
            out["incidencias"].append(
                "sin acceso al RCE (el contador debe asignar el perfil SOL).")
        else:
            out["estado"] = "error_filtro"
            out["incidencias"].append(res.get("motivo") or "no se procesó el RCE.")
        return out

    lista = libro.get("lista") or {}
    estado_lista = lista.get("estado")
    if isinstance(estado_lista, int) and estado_lista >= 400:
        out["estado"] = "sin_acceso" if estado_lista in (401, 403) else "error_filtro"
        out["incidencias"].append(f"la lista RCE devolvió status {estado_lista}.")
        for e in libro.get("errores") or []:
            if e.get("msg"):
                out["incidencias"].append(f"RCE: {e['msg']}")
        return out

    registros = lista.get("registros") or []
    out["registros"] = registros
    out["n_consultables"] = lista.get("n_consultables") or 0

    detalles = {}
    for fila in libro.get("comprobantes") or []:
        cab = fila.get("cabecera")
        if not cab:
            continue
        k = _nk(cab.get("ruc_emisor"), cab.get("tipo_comprobante"),
                cab.get("serie"), cab.get("numero"))
        detalles[k] = {"cabecera": cab, "items": fila.get("items") or []}
    out["detalles"] = detalles

    if out["n_consultables"] and len(detalles) < out["n_consultables"]:
        out["incidencias"].append(
            f"ítems incompletos: {len(detalles)}/{out['n_consultables']} "
            "comprobantes consultables con detalle.")

    out["estado"] = "con_datos" if registros else "sin_datos"
    out["exito"] = True
    return out


# ───────────────────────── Persistencia (upsert idempotente) ────────────
def _fila_comprobante(contrib, reg: dict, det_cab: dict | None) -> dict:
    d = det_cab or {}
    exo = d.get("exonerado")
    ina = d.get("inafecto")
    base_exon_inaf = None
    if exo is not None or ina is not None:
        base_exon_inaf = round((exo or 0) + (ina or 0), 2)
    return {
        "id": uuid.uuid4(),
        "contribuyente_id": contrib.id,
        "estudio_id": getattr(contrib, "estudio_id", None),
        "periodo": reg.get("periodo"),
        "cod_car": None,
        "cod_tipo_cdp": reg.get("tipo"),
        "desc_tipo_cdp": reg.get("desc_tipo"),
        "serie": reg.get("serie"),
        "numero": str(reg.get("numero")) if reg.get("numero") is not None else None,
        "numero_final": None,
        "fecha_emision": _fecha(d.get("fecha_emision")) or _fecha(reg.get("fecha")),
        "fecha_vencimiento": None,
        "tipo_doc_proveedor": reg.get("tipo_doc_emisor"),
        "ruc_proveedor": reg.get("ruc_emisor"),
        "razon_social_proveedor": (reg.get("razon_emisor")
                                   or d.get("razon_emisor")),
        "moneda": d.get("moneda") or reg.get("moneda"),
        "tipo_cambio": None,
        "cod_estado": d.get("estado_cpe"),
        "estado": reg.get("estado"),
        "base_gravada": d.get("base_gravada"),
        "igv": d.get("igv") if d.get("igv") is not None else reg.get("igv"),
        "base_exon_inaf": base_exon_inaf,
        "valor_adq_ng": None,
        "isc": d.get("isc"),
        "icbper": d.get("icbper"),
        "otros_tributos": None,
        "importe_total": (d.get("importe_total")
                          if d.get("importe_total") is not None
                          else reg.get("total")),
        "ind_detraccion": None,
        "ind_oper_gratuita": None,
        "num_inconsistencias": None,
        "origen": "rce",
        "parser_version": PARSER_VERSION,
    }


_SET_COMPROBANTE = ("periodo", "desc_tipo_cdp", "fecha_emision",
                    "tipo_doc_proveedor", "razon_social_proveedor", "moneda",
                    "cod_estado", "estado", "base_gravada", "igv", "base_exon_inaf",
                    "isc", "icbper", "importe_total", "origen", "parser_version")


async def _upsert_comprobantes(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(ComprobanteCompra.__table__).values(filas)
    set_ = {c: getattr(stmt.excluded, c) for c in _SET_COMPROBANTE}
    set_["actualizado_at"] = func.now()
    stmt = stmt.on_conflict_do_update(constraint="uq_compra_cpe", set_=set_)
    await session.execute(stmt)
    return len(filas)


async def _map_ids(session, contrib, periodo: str) -> dict:
    """{clave_normalizada: id} de los comprobantes del período (para los ítems)."""
    q = select(
        ComprobanteCompra.id, ComprobanteCompra.ruc_proveedor,
        ComprobanteCompra.cod_tipo_cdp, ComprobanteCompra.serie,
        ComprobanteCompra.numero
    ).where(ComprobanteCompra.contribuyente_id == contrib.id,
            ComprobanteCompra.periodo == periodo)
    out = {}
    for r in (await session.execute(q)).all():
        out[_nk(r.ruc_proveedor, r.cod_tipo_cdp, r.serie, r.numero)] = r.id
    return out


def _fila_item(comprobante_id, it: dict) -> dict:
    return {
        "id": uuid.uuid4(),
        "comprobante_id": comprobante_id,
        "linea": it.get("linea"),
        "codigo": it.get("codigo"),
        "descripcion": it.get("descripcion"),
        "detalle_emisor": (it.get("detalle_emisor") or None),
        "unidad": it.get("unidad"),
        "desc_unidad": it.get("unidad_nombre"),
        "cantidad": it.get("cantidad"),
        "valor_unitario": it.get("valor_unitario_sin_imp"),
        "descuento": it.get("descuento"),
        "icbper": it.get("icbper"),
        "importe_total": it.get("importe_linea_emisor"),
        "parser_version": PARSER_VERSION,
    }


_SET_ITEM = ("codigo", "descripcion", "detalle_emisor", "unidad", "desc_unidad",
             "cantidad", "valor_unitario", "descuento", "icbper", "importe_total",
             "parser_version")


async def _upsert_items(session, filas: list[dict]) -> int:
    if not filas:
        return 0
    stmt = pg_insert(ComprobanteItem.__table__).values(filas)
    set_ = {c: getattr(stmt.excluded, c) for c in _SET_ITEM}
    stmt = stmt.on_conflict_do_update(constraint="uq_compra_item_linea", set_=set_)
    await session.execute(stmt)
    return len(filas)


async def cargar_compras(session, contrib, periodo: str) -> dict:
    """Orquesta la carga de Compras de un contribuyente/período: recolecta (hilo,
    Playwright, vía comp_diag) y persiste (idempotente, nunca borra). PARCIAL si
    algo faltó."""
    cred = getattr(contrib, "credencial", None)
    if not cred:
        return {"exito": False, "motivo": "sin_credencial", "ruc": contrib.ruc,
                "periodo": periodo, "parcial": True, "estado": "sin_credencial",
                "comprobantes": 0, "items": 0, "totales": {},
                "incidencias": ["sin credencial SOL"]}
    from cifrado import descifrar_clave_sol
    try:
        clave = descifrar_clave_sol(cred.clave_sol_cifrada)
    except Exception as e:
        return {"exito": False, "motivo": f"clave:{type(e).__name__}",
                "ruc": contrib.ruc, "periodo": periodo, "parcial": True,
                "estado": "sin_credencial", "comprobantes": 0, "items": 0,
                "totales": {},
                "incidencias": [f"no se pudo descifrar la clave: {type(e).__name__}"]}

    cfg = SunatConfig(ruc=contrib.ruc, usuario_sol=cred.usuario_sol,
                      clave_sol=clave, headless=True)
    rec = await asyncio.to_thread(recolectar_compras, cfg, periodo)

    resumen = {"exito": rec.get("exito", False), "motivo": rec.get("motivo"),
               "ruc": contrib.ruc, "periodo": periodo, "estado": rec.get("estado"),
               "comprobantes": 0, "items": 0, "parcial": True,
               "incidencias": list(rec.get("incidencias") or []),
               "dump": rec.get("dump"), "totales": {}}

    if not rec.get("exito") or rec.get("estado") in _MALOS:
        resumen["incidencias"].insert(
            0, f"la corrida no completó (estado={rec.get('estado')}, "
               f"motivo={rec.get('motivo')}).")
        return resumen

    registros = rec.get("registros") or []
    resumen["totales"] = {"mtoTotalCp": _suma(registros, "total"),
                          "n_registros": len(registros)}
    if not registros:
        resumen["parcial"] = False       # sin_datos legítimo (período sin compras)
        return resumen

    detalles = rec.get("detalles") or {}

    # ── Cabeceras (atómico) ──
    try:
        filas_c = []
        for reg in registros:
            if not (reg.get("ruc_emisor") and reg.get("serie")
                    and reg.get("numero") is not None):
                continue
            k = _nk(reg.get("ruc_emisor"), reg.get("tipo"), reg.get("serie"),
                    reg.get("numero"))
            det_cab = (detalles.get(k) or {}).get("cabecera")
            filas_c.append(_fila_comprobante(contrib, reg, det_cab))
        n_c = await _upsert_comprobantes(session, filas_c)
        await session.commit()
        resumen["comprobantes"] = n_c
    except Exception as e:
        await session.rollback()
        resumen["incidencias"].append(
            f"error al escribir cabeceras: {type(e).__name__}: {e}"[:180])
        return resumen

    # ── Ítems (si se recolectó detalle) ──
    if detalles:
        try:
            ids = await _map_ids(session, contrib, periodo)
            filas_i = []
            for k, det in detalles.items():
                cid = ids.get(k)
                if not cid:
                    continue
                for it in det.get("items") or []:
                    if it.get("linea") is not None:
                        filas_i.append(_fila_item(cid, it))
            n_i = await _upsert_items(session, filas_i)
            await session.commit()
            resumen["items"] = n_i
        except Exception as e:
            await session.rollback()
            resumen["incidencias"].append(
                f"error al escribir ítems: {type(e).__name__}: {e}"[:180])

    # PARCIAL si quedaron incidencias de datos (ítems incompletos, contacto, etc.).
    hay_pendiente = any(
        ("ítems incompletos" in i) or ("incompleto" in i) or ("sin acceso" in i)
        or ("aceptación" in i) or ("contacto_sin_validar" in i)
        for i in resumen["incidencias"])
    resumen["parcial"] = hay_pendiente
    return resumen
