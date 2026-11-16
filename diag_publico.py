# -*- coding: utf-8 -*-
"""
diag_publico.py — alerta.pe  (proyecto decl-diag · cronograma + padrón BC)
═══════════════════════════════════════════════════════════════════════
Volcador DIAG-FIRST de páginas PÚBLICAS de SUNAT (sin login SOL, sin BD). Baja una
URL con Playwright y vuelca su ESTRUCTURA para escribir el parser sobre lo REAL
(no fabricado): HTML, tablas (encabezados + n_filas + id/class), formularios
(selects/opciones), frames, enlaces útiles y red (método + patrón de URL + status +
content-type). NO escribe en la BD. NO descarga binarios: si la URL no es HTML,
reporta el content-type y guarda una muestra de texto.

Masking: todo pasa por `enmascarar` (igual que el motor). Estas páginas son
públicas (no hay credenciales), pero mantenemos el mismo sink por disciplina.

Uso (local; las páginas son públicas, no necesita railway ni SOL):
    python diag_publico.py cronograma_2026 "<URL oficial del cronograma del año>"
    python diag_publico.py bc_padron       "<URL del padrón de Buenos Contribuyentes>"

Pásame la carpeta descargas/<slug>_<ts>/ y escribo el parser del cargador.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Lima")
except Exception:
    TZ = None

try:
    from seguridad_logs import enmascarar as _mask
except Exception:
    def _mask(x):
        return str(x) if x is not None else ""

DEST = Path("./descargas")
PAUSA_SEG = 3


def _ts() -> str:
    now = datetime.now(TZ) if TZ else datetime.now()
    return now.strftime("%Y%m%d_%H%M%S")


def log(msg: str, nivel: str = "INFO") -> None:
    print(f"[{nivel}] {_mask(msg)}", flush=True)


def _patron_url(u: str) -> str:
    """Ruta + claves de query SIN valores (nunca filtra un token)."""
    if not u:
        return ""
    base, _, query = u.partition("?")
    claves = [kv.split("=", 1)[0] for kv in query.split("&") if kv]
    return base + ("?" + "=&".join(claves) + "=" if claves else "")


def _scopes(page):
    yield page
    for fr in page.frames:
        if fr is not page.main_frame:
            yield fr


def _tablas(scope) -> list:
    out = []
    for t in scope.query_selector_all("table"):
        heads = [_mask(th.inner_text()) for th in t.query_selector_all("thead th")]
        if not heads:
            first = t.query_selector("tr")
            if first:
                heads = [_mask(c.inner_text())
                         for c in first.query_selector_all("th,td")]
        filas = t.query_selector_all("tbody tr") or t.query_selector_all("tr")
        # Muestra de la 1ª fila de datos (para ver la forma real, enmascarada).
        muestra = []
        if filas:
            cuerpo = [f for f in filas if f.query_selector_all("td")]
            if cuerpo:
                muestra = [_mask(c.inner_text())
                           for c in cuerpo[0].query_selector_all("td")][:15]
        out.append({"id": t.get_attribute("id"), "class": t.get_attribute("class"),
                    "encabezados": [h for h in heads if h][:25],
                    "n_filas": len(filas), "muestra_fila": muestra})
    return out


def _formularios(scope) -> list:
    out = []
    for f in scope.query_selector_all("form"):
        campos = []
        for el in f.query_selector_all("input, select, textarea"):
            try:
                tag = (el.evaluate("e => e.tagName") or "").lower()
            except Exception:
                tag = ""
            opciones = None
            if tag == "select":
                opciones = [_mask(o.inner_text())
                            for o in el.query_selector_all("option")][:15]
            campos.append({"tag": tag, "name": el.get_attribute("name"),
                           "type": el.get_attribute("type"),
                           "id": el.get_attribute("id"), "opciones": opciones})
        out.append({"action": _patron_url(f.get_attribute("action") or ""),
                    "method": (f.get_attribute("method") or "get").lower(),
                    "campos": campos[:40]})
    return out


def _enlaces(scope) -> list:
    """Enlaces cuyo texto/href huele a cronograma / resolución / padrón."""
    pistas = ("cronograma", "resoluc", "padron", "padrón", "buenos",
              "vencim", "descarg", ".xls", ".csv", ".txt", ".zip", ".pdf")
    out = []
    for a in scope.query_selector_all("a"):
        try:
            href = a.get_attribute("href") or ""
            txt = _mask(a.inner_text())[:80]
        except Exception:
            continue
        blob = (href + " " + txt).lower()
        if any(p in blob for p in pistas):
            out.append({"texto": txt, "href": _patron_url(href),
                        "href_tiene_valor": "?" in href})
    return out[:40]


def diag_publico(slug: str, url: str) -> dict:
    carpeta = DEST / f"{slug}_{_ts()}"
    carpeta.mkdir(parents=True, exist_ok=True)
    res = {"slug": slug, "url_patron": _patron_url(url), "carpeta": str(carpeta),
           "principal": {}, "scopes": [], "enlaces": [], "red": []}
    red: list = []

    def _on_response(resp):
        try:
            red.append({"metodo": resp.request.method,
                        "url": _patron_url(resp.url), "status": resp.status,
                        "ctype": ((resp.headers or {}).get("content-type", ""))[:50]})
        except Exception:
            pass

    with sync_playwright() as p:
        nav = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = nav.new_context(
            locale="es-PE",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"))
        ctx.on("response", _on_response)
        page = ctx.new_page()
        try:
            log(f"Abriendo {slug}: {_patron_url(url)}")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            ctype = ""
            if resp is not None:
                res["principal"] = {"status": resp.status,
                                    "url_patron": _patron_url(resp.url)}
                ctype = ((resp.headers or {}).get("content-type", ""))
                res["principal"]["ctype"] = ctype[:80]
            # Si no es HTML (padrón suele ser archivo), guarda muestra de texto.
            if "html" not in ctype.lower():
                try:
                    cuerpo = page.content()
                except Exception:
                    cuerpo = ""
                muestra = _mask(cuerpo)[:4000]
                (carpeta / f"{slug}_muestra.txt").write_text(
                    muestra, encoding="utf-8")
                res["principal"]["no_html"] = True
                res["principal"]["muestra_bytes"] = len(cuerpo)
                log(f"  {slug}: la URL no es HTML (ctype={ctype[:40]}). "
                    f"Muestra guardada.", "WARN")
            # HTML por scope (página + frames).
            for i, sc in enumerate(_scopes(page)):
                try:
                    nombre = f"{slug}.html" if i == 0 else f"{slug}_frame{i}.html"
                    (carpeta / nombre).write_text(_mask(sc.content()),
                                                  encoding="utf-8")
                    res["scopes"].append({
                        "idx": i, "archivo": nombre,
                        "url_patron": _patron_url(getattr(sc, "url", "") or ""),
                        "tablas": _tablas(sc), "formularios": _formularios(sc)})
                    res["enlaces"] += _enlaces(sc)
                except Exception as e:
                    res["scopes"].append({"idx": i,
                                          "error": _mask(f"{type(e).__name__}: {e}")})
        except Exception as e:
            res["error"] = _mask(f"{type(e).__name__}: {e}")
            log(f"  {slug}: error {res['error']}", "ERROR")
        finally:
            try:
                ctx.close(); nav.close()
            except Exception:
                pass

    res["red"] = red[:120]
    (carpeta / "_resumen.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    # Resumen a consola (sin datos sensibles).
    log(f"{slug}: {len(res['scopes'])} scope(s), "
        f"{sum(len(s.get('tablas', [])) for s in res['scopes'])} tabla(s), "
        f"{len(res['enlaces'])} enlace(s) útiles, {len(red)} petición(es).")
    log(f"Volcado en {carpeta}")
    return res


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("uso: python diag_publico.py <slug> <url>")
        print('  ej: python diag_publico.py cronograma_2026 "<URL cronograma>"')
        print('      python diag_publico.py bc_padron "<URL padrón BC>"')
        raise SystemExit(2)
    diag_publico(sys.argv[1], sys.argv[2])
