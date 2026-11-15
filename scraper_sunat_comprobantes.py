# -*- coding: utf-8 -*-
"""
scraper_sunat_comprobantes.py — alerta.pe  (proyecto comp-diag, DIAG-FIRST)
═══════════════════════════════════════════════════════════════════════════
Lee, EN SOLO LECTURA y del lado del servidor, los comprobantes de COMPRAS (RCE)
y VENTAS (RVIE) de un período, CON LA DESCRIPCIÓN DE LOS ÍTEMS. Reusa el login
SOL de decl-diag (mismo enmascarado, `sin_acceso`, pausas). NO escribe en la BD,
NO descarga PDFs/ZIP, NO hace merge ni migraciones.

Método (validado antes en `asistente-sol`, ramas diagnostico-01/02 y lote-03):
  1. Tras el login SOL, la SPA de SIRE/Comprobantes llama a servicios REST
     `api-sire.sunat.gob.pe` y `api-cpe.sunat.gob.pe` con un **Bearer** que la
     propia página porta (JWT por sesión). Lo CAPTURAMOS de sus requests (igual
     que el "gancho": nunca se guarda, solo se reusa en memoria para pedir las
     mismas rutas que pediría la pantalla).
  2. **Lista del período** (cabeceras) desde SIRE:
       compras: /v1/contribuyente/migeigv/libros/rce/propuesta/web/propuesta/{AAAAMM}/busqueda
       ventas : el endpoint análogo de RVIE (se CAPTURA al abrir la pantalla; no
                se adivina).
  3. Para los primeros N comprobantes consultables (electrónicos, con RUC del
     emisor) pedimos a api-cpe el DETALLE CON ÍTEMS:
       /v1/contribuyente/consultacpe/comprobantes/{ruc}-{tipo}-{serie}-{numero}-{ind}
       indicador final: **2 = Recibido (compras), 1 = Emitido (ventas)**.
  4. Se vuelca todo (ENMASCARADO) a `./descargas/comp_diag_<RUC>_<ts>/` y se
     arma un Excel de muestra (una fila por ítem).

Enmascarado: el token Bearer NUNCA se escribe ni se loguea. Las URLs se guardan
como patrón (claves de query sin valores). El usuario SOL/clave se redactan.

⚠️  DIAG: primera corrida = CAPTURA. Las formas de respuesta de la propuesta SIRE
se descubren aquí; el parseo fino se endurece después con el volcado real.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from scraper_sunat_playwgth import SunatConfig, login_sol, log
# Reusa helpers ya validados del diag de declaraciones (mismo enmascarado/menú).
from scraper_sunat_declaraciones import (
    _sanea, _patron_url, _norm, _scopes, _menu_catalogo, _ir_por_codigo,
    _texto_scopes, _MARCA_SIN_ACCESO, _hay_marcador,
)

TZ_LIMA = ZoneInfo("America/Lima")
DEST = Path("./descargas")

API_CPE = "https://api-cpe.sunat.gob.pe"
API_SIRE = "https://api-sire.sunat.gob.pe"
_RUTA_CPE = "/v1/contribuyente/consultacpe/comprobantes"
# Compras (RCE). El `codTipoOpe` real se captura del request de la página si se ve;
# por defecto 1 (se reporta el usado). perPage alto para traer el período de una.
_PROPUESTA_RCE = ("/v1/contribuyente/migeigv/libros/rce/propuesta/web/propuesta/"
                  "{periodo}/busqueda")

# Ritmo prudente (de `asistente-sol/motor.js`, validado): nada en paralelo.
PAUSA = (float(os.getenv("COMP_PAUSA_MIN", "3")),
         float(os.getenv("COMP_PAUSA_MAX", "5")))   # entre comprobantes
TIMEOUT_MS = int(os.getenv("COMP_TIMEOUT_MS", "30000"))
N_DEFAULT = int(os.getenv("COMP_N", "5"))
PERPAGE = int(os.getenv("COMP_PERPAGE", "1000"))

# Tipos consultables como comprobante electrónico (resto = "no aplica").
TIPOS_CONSULTABLES = {"01": "Factura", "03": "Boleta",
                      "07": "Nota de crédito", "08": "Nota de débito"}
_SERIE_ELECTRONICA = re.compile(r"^[FBE][A-Z0-9]{3}$")


# ───────────────────────── utilidades de montos/fechas ─────────────────────────
def _num(v):
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _iso(f):
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", str(f or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (f or None)


# ───────────────────────── parseo del DETALLE (api-cpe) ─────────────────────────
# Port de `asistente-sol/src/lote/comprobante.js::normalizarConsulta` (validado).
def _totales_de(c: dict) -> dict:
    for v in c.values():
        if isinstance(v, dict) and "mtoImporteTotal" in v:
            return v
    return {}


def normalizar_consulta(obj: dict) -> dict | None:
    """Respuesta de GET …/consultacpe/comprobantes/{clave} → {cabecera, items}.
    Devuelve None si no hay comprobante. Los nombres de campo son los REALES de
    SUNAT (confirmados en asistente-sol)."""
    comps = obj.get("comprobantes") if isinstance(obj, dict) else None
    c = comps[0] if isinstance(comps, list) and comps else None
    if not c:
        return None
    t = _totales_de(c)
    e = c.get("datosEmisor") or {}
    r = c.get("datosReceptor") or {}
    cabecera = {
        "tipo_comprobante": c.get("codCpe"), "serie": c.get("numSerie"),
        "numero": str(c.get("numCpe")) if c.get("numCpe") is not None else None,
        "fecha_emision": _iso(c.get("fecEmision")), "moneda": c.get("codMoneda"),
        "ruc_emisor": e.get("numRuc"), "razon_emisor": e.get("desRazonSocialEmis"),
        "direccion_emisor": e.get("desDirEmis"), "ubigeo_emisor": e.get("ubigeoEmis") or None,
        "tipo_doc_receptor": r.get("codDocIdeRecep"),
        "num_doc_receptor": r.get("numDocIdeRecep"),
        "razon_receptor": r.get("desRazonSocialRecep"),
        "base_gravada": _num(t.get("mtoTotalValVentaGrabado", t.get("mtoTotalValVentaGravado"))),
        "exonerado": _num(t.get("mtoTotalValVentaExonerado")),
        "inafecto": _num(t.get("mtoTotalValVentaInafecto")),
        "exportacion": _num(t.get("mtoTotalValVentaExportacion")),
        "gratuito": _num(t.get("mtoTotalValVentaGratuito")),
        "igv": _num(t.get("mtoSumIGV")), "isc": _num(t.get("mtoSumISC")),
        "icbper": _num(t.get("mtoSumICBPER")),
        "importe_total": _num(t.get("mtoImporteTotal")),
        "estado_cpe": c.get("indEstadoCpe"),
    }
    items_raw = c.get("informacionItems")
    items = []
    for k, i in enumerate(items_raw if isinstance(items_raw, list) else []):
        items.append({
            "linea": k + 1, "codigo": i.get("desCodigo") or None,
            "descripcion": i.get("desItem") or None,
            "unidad": i.get("codUnidadMedida") or None,
            "unidad_nombre": i.get("desUnidadMedida") or None,
            "cantidad": _num(i.get("cntItems")),
            "valor_unitario": _num(i.get("mtoValUnitario")),
            "descuento": _num(i.get("mtoDesc")),
            "importe": _num(i.get("mtoImpTotal")),
            "icbper": _num(i.get("mtoICBPER")),
        })
    return {"cabecera": cabecera, "items": items}


def _mensaje_sunat(texto: str) -> str | None:
    """Mensaje de error de SUNAT (422/500) para mostrarlo al contador."""
    try:
        j = json.loads(texto)
    except Exception:
        return None
    errs = j.get("errors")
    e = errs[0] if isinstance(errs, list) and errs else None
    msg = (e or {}).get("desError") if isinstance(e, dict) else None
    return (str(msg or j.get("message") or j.get("msg") or "")[:200]) or None


# ───────────────────────── extracción de la lista SIRE (por forma) ──────────────
# La forma JSON de la propuesta SIRE NO está confirmada (en las grabaciones el
# cuerpo venía enmascarado). Se extrae POR FORMA + pistas de nombre, y se reporta
# la clave cruda del 1er registro para endurecer el parser luego.
_PISTAS = {
    "serie": ("serie", "numserie", "seriecdp", "numseriecdp"),
    "numero": ("numero", "numcpe", "nrocp", "numcp", "nrocomprobante", "numcomprobante"),
    "tipo": ("codtipocp", "tipocp", "codcpe", "tipocomprobante", "codtipocomprobante", "tipodocumento"),
    "ruc_emisor": ("numrucemisor", "rucemisor", "numdocidentidad", "numruc", "ruc"),
    "razon_emisor": ("desrazonsocial", "razonsocial", "nombrerazonsocial", "desrazon"),
    "fecha": ("fecemision", "fechaemision", "fecha", "fechacp"),
    "moneda": ("codmoneda", "moneda", "tipmoneda"),
    "igv": ("mtoigv", "igv", "mtoigvipm", "igvipm"),
    "total": ("mtoimportetotal", "importetotal", "mtototalcp", "totalcp", "total"),
}


def _match_pista(clave: str, nombres) -> bool:
    k = re.sub(r"[^a-z0-9]", "", clave.lower())
    return any(k == n or k.endswith(n) for n in nombres)


def _registros_comprobante(obj) -> list[dict]:
    """Recorre el JSON y junta los registros que parecen un comprobante: tienen
    una SERIE electrónica (FBE+3) o, en su defecto, campos tipo/serie/numero/ruc."""
    out: list[dict] = []

    def visita(o):
        if isinstance(o, dict):
            # ¿este dict es un registro de comprobante?
            plano = {k: v for k, v in o.items()
                     if isinstance(v, (str, int, float)) and v is not None}
            encontrado = {}
            for campo, nombres in _PISTAS.items():
                for k, v in plano.items():
                    if _match_pista(k, nombres):
                        encontrado.setdefault(campo, (k, v))
            serie_val = str(encontrado.get("serie", ("", ""))[1] or "").upper()
            tiene_serie = bool(_SERIE_ELECTRONICA.match(serie_val))
            if (tiene_serie or ("serie" in encontrado and "numero" in encontrado
                                and ("ruc_emisor" in encontrado or "tipo" in encontrado))):
                reg = {c: val for c, (kk, val) in encontrado.items()}
                reg["_claves"] = {c: kk for c, (kk, val) in encontrado.items()}
                out.append(reg)
            for v in o.values():
                visita(v)
        elif isinstance(o, list):
            for it in o:
                visita(it)

    visita(obj)
    return out


def _consultable(reg: dict) -> str | None:
    """Motivo por el que NO se consulta (o None si es consultable). Port de
    asistente-sol/propuesta.js::motivoNoAplica."""
    tipo = str(reg.get("tipo") or "").zfill(2)
    serie = str(reg.get("serie") or "").upper()
    ruc = str(reg.get("ruc_emisor") or "")
    numero = re.sub(r"\D", "", str(reg.get("numero") or ""))
    if tipo not in TIPOS_CONSULTABLES:
        return f"Tipo {tipo or '?'}: no se consulta como comprobante electrónico"
    if not re.fullmatch(r"\d{11}", ruc):
        return "El emisor no tiene RUC"
    if not _SERIE_ELECTRONICA.match(serie):
        return "Serie física (no electrónica)"
    if not re.fullmatch(r"\d{1,10}", numero):
        return "Número no válido"
    return None


def _clave_cpe(reg: dict, indicador: int) -> str:
    """Construye {ruc}-{tipo}-{serie}-{numero}-{ind} (número sin ceros)."""
    tipo = str(reg.get("tipo") or "").zfill(2)
    serie = str(reg.get("serie") or "").upper()
    ruc = str(reg.get("ruc_emisor") or "")
    numero = str(int(re.sub(r"\D", "", str(reg.get("numero") or "0")) or "0"))
    return f"{ruc}-{tipo}-{serie}-{numero}-{indicador}"


# ───────────────────────── captura de red (Bearer + cuerpos) ────────────────────
class _Captura:
    """Escucha requests/responses de api-cpe/api-sire. Guarda el Bearer SOLO en
    memoria (nunca a disco) y registra el mapa de red (patrón+estado)."""
    def __init__(self, cfg: SunatConfig):
        self.cfg = cfg
        self.bearer_sire = None
        self.bearer_cpe = None
        self.red: list[dict] = []
        self.propuestas: list[dict] = []   # {url, patron, cuerpo} de busquedas vistas

    def on_request(self, req):
        try:
            url = req.url or ""
            if API_SIRE not in url and API_CPE not in url:
                return
            auth = (req.headers or {}).get("authorization", "")
            if auth[:7].lower() == "bearer ":
                if API_SIRE in url and not self.bearer_sire:
                    self.bearer_sire = auth
                if API_CPE in url and not self.bearer_cpe:
                    self.bearer_cpe = auth
        except Exception:
            pass

    def on_response(self, resp):
        try:
            url = resp.url or ""
            if API_SIRE not in url and API_CPE not in url:
                return
            self.red.append({"metodo": resp.request.method,
                             "patron": _patron_url(url, self.cfg),
                             "estado": resp.status})
            # Guardar cuerpo de las búsquedas de propuesta (la lista del período).
            if "propuesta" in url.lower() and "busqueda" in url.lower() \
                    and resp.request.method == "GET" and resp.status == 200:
                try:
                    self.propuestas.append(
                        {"url": url, "patron": _patron_url(url, self.cfg),
                         "cuerpo": resp.text()})
                except Exception:
                    pass
        except Exception:
            pass


def _pausa_comprobante():
    try:
        time.sleep(random.uniform(*PAUSA))
    except Exception:
        pass


def _get_api(page, url: str, bearer: str) -> dict:
    """GET autenticado a un servicio api-* reusando la sesión del contexto (igual
    que el botón de la pantalla). Devuelve {estado, texto} o {error}."""
    try:
        r = page.request.get(
            url, headers={"Authorization": bearer,
                          "Accept": "application/json, text/plain, */*"},
            timeout=TIMEOUT_MS)
        try:
            txt = r.text()
        except Exception:
            txt = ""
        return {"estado": r.status, "texto": txt}
    except Exception as e:
        return {"error": type(e).__name__}


def _volcar_json(carpeta: Path, nombre: str, obj, cfg: SunatConfig):
    """Escribe un JSON/texto ENMASCARADO a disco."""
    try:
        txt = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)
        (carpeta / nombre).write_text(_sanea(txt, cfg), encoding="utf-8")
    except Exception:
        pass


# ───────────────────────── una vuelta por un libro (compras/ventas) ─────────────
def _procesar_libro(page, cap: _Captura, carpeta: Path, cfg: SunatConfig,
                    libro: str, periodo: str, indicador: int, n: int,
                    url_propuesta: str | None, cod_tipo_ope: str) -> dict:
    """Pide la lista del período (si hay endpoint+token) y consulta los ítems de
    los primeros `n` comprobantes consultables. `libro`='compras'|'ventas'."""
    res = {"libro": libro, "indicador": indicador, "periodo": periodo,
           "lista": {"estado": None, "n_registros": 0, "n_consultables": 0,
                     "claves_detectadas": None, "fuente": None},
           "comprobantes": [], "items_total": 0, "tiempos": [], "errores": []}

    bearer = cap.bearer_sire
    cuerpo = None
    # 1) ¿La página ya trajo la propuesta de este período? (captura pasiva)
    for pr in cap.propuestas:
        if f"/{periodo}/" in pr["url"] and libro_de_url(pr["url"]) == libro:
            cuerpo = pr["cuerpo"]
            res["lista"]["fuente"] = "captura_pasiva"
            break
    # 2) Si no, y tenemos token + endpoint, la pedimos nosotros (compras: conocido).
    if cuerpo is None and bearer and url_propuesta:
        url = url_propuesta.format(periodo=periodo)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}codTipoOpe={cod_tipo_ope}&page=1&perPage={PERPAGE}"
        r = _get_api(page, url, bearer)
        res["lista"]["estado"] = r.get("estado") or r.get("error")
        if r.get("estado") == 200:
            cuerpo = r.get("texto")
            res["lista"]["fuente"] = "consulta_directa"
        else:
            res["errores"].append(
                {"paso": "lista", "estado": r.get("estado"),
                 "msg": _mensaje_sunat(r.get("texto", "")) or r.get("error")})

    if cuerpo:
        _volcar_json(carpeta, f"lista_{libro}_{periodo}.json", cuerpo, cfg)
        try:
            data = json.loads(cuerpo)
        except Exception:
            data = {}
        registros = _registros_comprobante(data)
        res["lista"]["n_registros"] = len(registros)
        if registros:
            res["lista"]["claves_detectadas"] = registros[0].get("_claves")
        consultables = [r for r in registros if _consultable(r) is None]
        res["lista"]["n_consultables"] = len(consultables)
        objetivo = consultables[:n]
        for reg in objetivo:
            clave = _clave_cpe(reg, indicador)
            bcpe = cap.bearer_cpe or cap.bearer_sire
            if not bcpe:
                res["errores"].append({"paso": "consulta", "msg": "sin_token_cpe"})
                break
            t0 = time.monotonic()
            r = _get_api(page, f"{API_CPE}{_RUTA_CPE}/{clave}", bcpe)
            dt = round(time.monotonic() - t0, 2)
            res["tiempos"].append(dt)
            estado = r.get("estado") or r.get("error")
            fila = {"clave_forma": _clave_forma(clave), "estado": estado,
                    "segundos": dt}
            if r.get("estado") == 200:
                try:
                    norm = normalizar_consulta(json.loads(r["texto"]))
                except Exception:
                    norm = None
                if norm:
                    fila["cabecera"] = norm["cabecera"]
                    fila["items"] = norm["items"]
                    fila["n_items"] = len(norm["items"])
                    res["items_total"] += len(norm["items"])
                    _volcar_json(carpeta,
                                 f"detalle_{libro}_{_archivo_seguro(clave)}.json",
                                 r["texto"], cfg)
                else:
                    fila["nota"] = "respuesta 200 sin comprobante"
            elif r.get("estado") in (401, 403):
                fila["nota"] = "sesión/token vencido"
                res["errores"].append({"paso": "consulta", "estado": r.get("estado"),
                                       "msg": "token vencido"})
                res["comprobantes"].append(fila)
                break
            else:
                fila["nota"] = _mensaje_sunat(r.get("texto", "")) or "no encontrado/limite"
            res["comprobantes"].append(fila)
            _pausa_comprobante()
    return res


def libro_de_url(url: str) -> str:
    u = url.lower()
    if "/rce/" in u:
        return "compras"
    if "/rvie/" in u:
        return "ventas"
    return "?"


def _clave_forma(clave: str) -> str:
    """Enmascara la clave para loguear: conserva tipo e indicador, el resto forma."""
    p = clave.split("-")
    if len(p) == 5:
        return f"{'x'*len(p[0])}-{p[1]}-{'x'*len(p[2])}-{'x'*len(p[3])}-{p[4]}"
    return "x"


def _archivo_seguro(clave: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "_", clave)


# ───────────────────────── entrada principal (sync) ─────────────────────────────
def comp_diag(cfg: SunatConfig, periodo: str, n: int = N_DEFAULT) -> dict:
    """Diag de comprobantes compras+ventas con ítems. NO BD, NO PDFs, SOLO LECTURA.
    `periodo` = AAAAMM. Captura el Bearer de la página, pide la lista del período y
    consulta los ítems de los primeros `n` comprobantes de cada libro."""
    res = {"exito": False, "periodo": periodo, "carpeta": None,
           "libros": [], "menu_catalogo": None, "sin_acceso": False}
    ts = datetime.now(TZ_LIMA).strftime("%Y%m%d_%H%M%S")
    carpeta = DEST / f"comp_diag_{cfg.ruc}_{ts}"
    carpeta.mkdir(parents=True, exist_ok=True)
    res["carpeta"] = str(carpeta)
    cod_tipo_ope = os.getenv("COMP_CODTIPOOPE", "1")

    with sync_playwright() as p:
        nav = p.chromium.launch(
            headless=cfg.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = nav.new_context(
            locale="es-PE", timezone_id="America/Lima",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"))
        cap = _Captura(cfg)
        page = ctx.new_page()
        page.on("request", cap.on_request)
        page.on("response", cap.on_response)
        try:
            if not login_sol(page, cfg):
                res["motivo"] = "login"
                log("  comp-diag: login SOL rechazado.", "ERROR")
                return res
            log("  comp-diag: login OK. Abro menú y capturo endpoints SIRE/CPE...", "OK")

            # Catálogo de menú (para la guía de perfil mínimo: qué opciones necesita
            # el usuario secundario). Enmascarado.
            try:
                cat = _menu_catalogo(page, cfg)
                res["menu_catalogo"] = cat
                _volcar_json(carpeta, "00_menu.json", cat, cfg)
                res["opciones_sire"] = [
                    c for c in cat
                    if any(t in _norm(c.get("texto", ""))
                           for t in ("sire", "registro de compras", "registro de ventas",
                                     "comprobante de pago", "rce", "rvie"))]
            except Exception:
                pass

            # Abrir las opciones SIRE/Comprobantes (best-effort por texto) para que
            # la SPA dispare sus llamadas (así capturamos Bearer + endpoints reales,
            # incluido el de VENTAS que no teníamos confirmado).
            for c in (res.get("opciones_sire") or [])[:6]:
                code = (c.get("onclick") or "")
                m = re.search(r"code=([0-9.]+)", code) or re.search(r"'([0-9.]{5,})'", code)
                if m:
                    _ir_por_codigo(page, m.group(1), c.get("texto", "")[:50])
                    _esperar(2.5)
                # Si no hay código, el href/onclick quedó enmascarado: se omite.

            _esperar(3)   # dar tiempo a que la SPA cargue y porte el Bearer

            # Marcador de falta de perfil (usuario secundario sin SIRE).
            if _hay_marcador(_texto_scopes(page), _MARCA_SIN_ACCESO):
                res["sin_acceso"] = True

            # Mapa de red capturado (patrón+estado), sin valores sensibles.
            _volcar_json(carpeta, "red_api.json", cap.red, cfg)
            res["bearer_capturado"] = {"sire": bool(cap.bearer_sire),
                                       "cpe": bool(cap.bearer_cpe)}

            # Procesar compras (ind=2) y ventas (ind=1).
            res["libros"].append(_procesar_libro(
                page, cap, carpeta, cfg, "compras", periodo, 2, n,
                API_SIRE + _PROPUESTA_RCE, cod_tipo_ope))
            res["libros"].append(_procesar_libro(
                page, cap, carpeta, cfg, "ventas", periodo, 1, n,
                _endpoint_ventas(cap), cod_tipo_ope))

            res["exito"] = True
            _volcar_json(carpeta, "resumen.json",
                         {k: v for k, v in res.items() if k != "menu_catalogo"}, cfg)
            _escribir_excel(carpeta, cfg, res)
        finally:
            try:
                ctx.close(); nav.close()
            except Exception:
                pass
    return res


def _endpoint_ventas(cap: _Captura) -> str | None:
    """Endpoint de la propuesta de VENTAS (RVIE). No está confirmado en las
    grabaciones → se toma del tráfico capturado si la página lo llamó; si no, None
    (y el diag lo reporta como pendiente, sin adivinar)."""
    for pr in cap.propuestas:
        if libro_de_url(pr["url"]) == "ventas":
            base = pr["url"].split("?")[0]
            # reemplaza el período concreto por el placeholder {periodo}
            return re.sub(r"/\d{6}/", "/{periodo}/", base)
    return None


def _esperar(seg: float):
    try:
        time.sleep(seg)
    except Exception:
        pass


# ───────────────────────── Excel de muestra (una fila por ítem) ─────────────────
def _escribir_excel(carpeta: Path, cfg: SunatConfig, res: dict):
    """Una fila por ÍTEM: fecha, serie-número, RUC y razón del emisor, descripción,
    cantidad, valor, IGV y total. (IGV/total son del COMPROBANTE, no del ítem: la
    consulta api-cpe no trae IGV por línea; se marca en el encabezado.)"""
    try:
        from openpyxl import Workbook
    except Exception:
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "items"
    ws.append(["libro", "fecha_emision", "serie-numero", "ruc_emisor",
               "razon_emisor", "linea", "descripcion", "cantidad", "unidad",
               "valor_unitario", "importe_item", "IGV_comprobante",
               "total_comprobante", "moneda"])
    for libro in res.get("libros", []):
        nombre = libro.get("libro")
        for comp in libro.get("comprobantes", []):
            cab = comp.get("cabecera") or {}
            sn = f"{cab.get('serie') or ''}-{cab.get('numero') or ''}"
            for it in comp.get("items", []):
                ws.append([
                    nombre, cab.get("fecha_emision"), sn,
                    _sanea(str(cab.get("ruc_emisor") or ""), cfg),
                    _sanea(str(cab.get("razon_emisor") or ""), cfg),
                    it.get("linea"), it.get("descripcion"), it.get("cantidad"),
                    it.get("unidad"), it.get("valor_unitario"), it.get("importe"),
                    cab.get("igv"), cab.get("importe_total"), cab.get("moneda"),
                ])
    try:
        wb.save(str(carpeta / "muestra_items.xlsx"))
    except Exception:
        pass
