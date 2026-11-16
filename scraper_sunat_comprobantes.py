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
# Guardián de seguridad: nunca tocar pantallas de aceptación/consentimiento.
from guardian_aceptacion import detectar_aceptacion

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
# La SPA real usa perPage de 2 dígitos (grabación: perPage=99). perPage=1000 daba
# 422 "errores de validación". Default conservador = 20 (overridable).
PERPAGE = int(os.getenv("COMP_PERPAGE", "20"))
# Endpoint de VENTAS (RVIE): no confirmado en grabaciones. Si la SPA no lo dispara,
# se intenta por simetría con RCE (overridable). Se reporta como no confirmado.
_PROPUESTA_RVIE = os.getenv(
    "COMP_PROPUESTA_RVIE",
    "/v1/contribuyente/migeigv/libros/rvie/propuesta/web/propuesta/"
    "{periodo}/busqueda")

# Tipos consultables como comprobante electrónico (resto = "no aplica").
TIPOS_CONSULTABLES = {"01": "Factura", "03": "Boleta",
                      "07": "Nota de crédito", "08": "Nota de débito"}
_SERIE_ELECTRONICA = re.compile(r"^[FBE][A-Z0-9]{3}$")

# Pantallas que DISPARAN los tokens, por CÓDIGO de menú SOL (data-id del árbol
# 00_menu.html). Se abren en PESTAÑA NUEVA (no en ifrVCE) y SOL las lanza con
# `MenuInternet.htm?action=execute&code=<code>` (NO iconExecute). Overridables por
# env. Códigos elegidos por el patrón de longitud de las grabaciones de
# asistente-sol (enmascaradas): CPE `99.99.9.9.9`, SIRE `99.9.9.9.9`.
#   11.38.1.1.1 = "Nueva Consulta de comprobantes de pago" (ruta confirmada por el
#                 contador: Comprobantes de pago > Comprobantes de Pago > Consulta de
#                 Comprobantes de Pago) → SPA "Consulta de Comprobante de Pago" (api-cpe).
#                 OJO: en algunos perfiles la opción aparece 2 veces (+ "Descarga de
#                 Resultados" 11.38.1.1.2, sin uso); se vuelca 00_menu_page.html para
#                 confirmar el code real del RUC si difiere. (NO 11.5.3.1.2 JSP vieja.)
#   60.2.2.1.1  = "Gestion de Compras" SIRE RCE  → api-sire (Bearer SIRE + propuesta)
#   60.2.1.1.1  = "Gestión de Ventas e Ingresos Electrónicos" SIRE RVIE → api-sire
#                 (NO 25.2.1.1.4: ese es el SLE-PORTAL viejo con pantalla de ACEPTACIÓN)
PANTALLAS = [
    {"slug": "cpe_consulta", "code": os.getenv("COMP_CODE_CPE", "11.38.1.1.1"),
     "label": "Nueva Consulta de comprobantes de pago", "necesita": "cpe"},
    {"slug": "sire_compras", "code": os.getenv("COMP_CODE_RCE", "60.2.2.1.1"),
     "label": "Gestion de Compras (SIRE RCE)", "necesita": "sire"},
    {"slug": "sire_ventas", "code": os.getenv("COMP_CODE_RVIE", "60.2.1.1.1"),
     "label": "Gestión de Ventas e Ingresos Electrónicos (SIRE RVIE)",
     "necesita": "sire"},
]
# Hosts de las SPA (confirmados en las grabaciones) por si hace falta navegar
# directo como respaldo.
SPA_CPE = ("https://e-factura.sunat.gob.pe/app/contribuyentems/servicio/"
           "consultacpe/consulta/nuevaconsulta/1.0.0/index.html")

# "Reintentar en 5 minutos" / no disponible = SUNAT intermitente (no es rechazo de
# nuestra sesión). Se separa de un rechazo de sesión/OAuth (401/sesión/autenticación).
_MARCA_NO_DISPONIBLE = (
    "reintente en 5 minutos", "reintentar en 5 minutos", "intente en 5 minutos",
    "no se puede acceder", "no está disponible", "no esta disponible",
    "servicio no disponible", "intente más tarde", "intente mas tarde",
    "en estos momentos no", "temporalmente no disponible",
)
_MARCA_RECHAZO_SESION = (
    "inicie sesión", "inicie sesion", "vuelva a iniciar", "sesión expiró",
    "sesion expiro", "sesión ha expirado", "su sesión", "no autenticado",
    "autenticación", "autenticacion", "token inválido", "token invalido",
    "unauthorized", "401", "oauth",
)
# Interstitial SUNAT "VALIDA TUS DATOS DE CONTACTO" (correo/celular no validados).
# Bloquea las operaciones. SOLO se permite CERRAR el aviso ("Finalizar") y seguir
# ("Continuar sin confirmar"): NINGUNO cambia datos. NUNCA "Aquí"/actualizar, ni
# enviar/validar códigos, ni escribir en campos de contacto.
_MARCA_CONTACTO = (
    "valida tus datos de contacto", "valida tus datos", "datos de contacto",
    "continuar sin confirmar", "no has validado", "valida tu correo",
    "valida tu celular", "correo y celular",
)
_BOTONES_SEGUROS_CONTACTO = ("Finalizar", "Continuar sin confirmar")

# PII a enmascarar en volcados/capturas (datos personales del contribuyente).
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_RE_CELULAR = re.compile(r"\b9\d{8}\b")


def _mask_pii(txt: str) -> str:
    """Enmascara correo y celular (9 dígitos) además del enmascarado general."""
    if not txt:
        return txt
    txt = _RE_EMAIL.sub("***@***", txt)
    txt = _RE_CELULAR.sub("9########", txt)
    return txt


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


def _limpiar_descripcion(s: str | None) -> str | None:
    """Normaliza espacios y, si la descripción es la MISMA frase repetida 2 veces
    (p.ej. 'T4 MADURO X 30 G (PERSA)  T4 MADURO X 30 G (PERSA)'), la deja una sola
    vez. No cambia el contenido, solo quita la duplicación exacta."""
    if not s:
        return None
    t = re.sub(r"\s+", " ", s).strip()
    if not t:
        return None
    m = re.fullmatch(r"(.+?)\s+\1", t)   # "X X" con X idéntico → X
    return m.group(1) if m else t


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
        # Algunos emisores EMPAQUETAN datos en la descripción con "@#@" (p.ej.
        # "COCA COLA VR 1500X08@#@ 1- @#@31.44@#@8.00@#@.00@#@2.52@#@36.15"). Se
        # separa: 1ª parte = descripción limpia; el RESTO va a 'detalle_emisor' tal
        # cual, SIN interpretar. Genérico para cualquier emisor.
        raw_desc = i.get("desItem") or ""
        if "@#@" in raw_desc:
            primero, _, resto = raw_desc.partition("@#@")
            descripcion = _limpiar_descripcion(primero)
            detalle_emisor = resto
        else:
            descripcion = _limpiar_descripcion(raw_desc)
            detalle_emisor = None
        # OJO: los montos vienen TAL CUAL de SUNAT, sin interpretar. En varios
        # emisores cantidad × valor_unitario_sin_imp ≠ importe_linea_emisor (p.ej.
        # CERVECERIA SAN JUAN 12 × 47.40 ≠ 811.20): suele ser por impuestos/descuentos
        # incluidos. NO se calcula ni se corrige nada aquí.
        items.append({
            "linea": k + 1, "codigo": i.get("desCodigo") or None,
            "descripcion": descripcion, "detalle_emisor": detalle_emisor,
            "unidad": i.get("codUnidadMedida") or None,
            "unidad_nombre": i.get("desUnidadMedida") or None,
            "cantidad": _num(i.get("cntItems")),
            "valor_unitario_sin_imp": _num(i.get("mtoValUnitario")),
            "descuento": _num(i.get("mtoDesc")),
            "importe_linea_emisor": _num(i.get("mtoImpTotal")),
            "icbper": _num(i.get("mtoICBPER")),
        })
    return {"cabecera": cabecera, "items": items}


def _mensaje_sunat(texto: str) -> str | None:
    """Mensaje de error de SUNAT (422/500) para mostrarlo al contador. Busca el
    detalle de validación en varias formas conocidas (errors/mensaje/field)."""
    try:
        j = json.loads(texto)
    except Exception:
        return None
    if isinstance(j, dict):
        errs = j.get("errors") or j.get("listaError") or j.get("validaciones")
        if isinstance(errs, list) and errs:
            e = errs[0]
            if isinstance(e, dict):
                det = (e.get("desError") or e.get("mensaje") or e.get("msg")
                       or e.get("campo") or e.get("field") or e.get("detalle"))
                if det:
                    return str(det)[:200]
            elif isinstance(e, str):
                return e[:200]
        directo = (j.get("message") or j.get("msg") or j.get("mensaje")
                   or j.get("detalle") or j.get("error"))
        if directo:
            return str(directo)[:200]
    return None


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


def parse_propuesta_rce(data) -> list[dict]:
    """Parser CONFIRMADO de la propuesta de COMPRAS (RCE), campos reales de SUNAT
    (volcado 15/11/2026, Oficinas & Depas). La lista viene en `data['registros']`.
    OJO: en COMPRAS el EMISOR del comprobante es el PROVEEDOR
    (`numDocIdentidadProveedor`), no `numRuc` (ese es el titular/comprador)."""
    regs = data.get("registros") if isinstance(data, dict) else None
    out: list[dict] = []
    for r in regs or []:
        if not isinstance(r, dict):
            continue
        m = r.get("montos") or {}
        igv = sum(_num(m.get(k)) or 0 for k in
                  ("mtoIgvIpmDG", "mtoIgvIpmDGNG", "mtoIgvIpmDNG"))
        out.append({
            "tipo": r.get("codTipoCDP"),
            "desc_tipo": r.get("desTipoCDP"),
            "serie": r.get("numSerieCDP"),
            "numero": r.get("numCDP"),
            # Emisor = proveedor (en compras es de quien RECIBIMOS el comprobante).
            "ruc_emisor": r.get("numDocIdentidadProveedor"),
            "tipo_doc_emisor": r.get("codTipoDocIdentidadProveedor"),
            "razon_emisor": r.get("nomRazonSocialProveedor"),
            "fecha": r.get("fecEmision"),
            "moneda": r.get("codMoneda"),
            "igv": igv or None,
            "total": _num(m.get("mtoTotalCp")),
            "estado": r.get("desEstadoComprobante"),
            "periodo": r.get("perTributario"),
            "_claves": {"tipo": "codTipoCDP", "serie": "numSerieCDP",
                        "numero": "numCDP", "ruc_emisor": "numDocIdentidadProveedor",
                        "razon_emisor": "nomRazonSocialProveedor",
                        "total": "montos.mtoTotalCp", "igv": "montos.mtoIgvIpm*"},
        })
    return out


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
        self.consultas_cpe: list[dict] = []  # respuestas consultacpe que hizo la SPA

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
            # Detalle con ítems que la propia SPA pidió al pulsar "Consultar".
            if "consultacpe/comprobantes/" in url.lower() \
                    and resp.request.method == "GET" and resp.status == 200:
                try:
                    self.consultas_cpe.append(
                        {"url": url, "cuerpo": resp.text()})
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
    """Escribe un JSON/texto ENMASCARADO a disco (incluye PII: correo/celular)."""
    try:
        txt = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)
        (carpeta / nombre).write_text(_mask_pii(_sanea(txt, cfg)), encoding="utf-8")
    except Exception:
        pass


# ───────────────────────── una vuelta por un libro (compras/ventas) ─────────────
def _procesar_libro(page, cap: _Captura, carpeta: Path, cfg: SunatConfig,
                    libro: str, periodo: str, indicador: int, n: int,
                    url_propuesta: str | None, cod_tipo_ope: str,
                    endpoint_confirmado: bool = True) -> dict:
    """Pide la lista del período (si hay endpoint+token) y consulta los ítems de
    los primeros `n` comprobantes consultables. `libro`='compras'|'ventas'."""
    res = {"libro": libro, "indicador": indicador, "periodo": periodo,
           "lista": {"estado": None, "n_registros": 0, "n_consultables": 0,
                     "claves_detectadas": None, "fuente": None,
                     "endpoint_confirmado": endpoint_confirmado},
           "comprobantes": [], "items_total": 0, "tiempos": [], "errores": []}

    bearer = cap.bearer_sire
    cuerpo = None
    # 1) ¿La página ya trajo la propuesta de este período? (captura pasiva)
    for pr in cap.propuestas:
        if f"/{periodo}/" in pr["url"] and libro_de_url(pr["url"]) == libro:
            cuerpo = pr["cuerpo"]
            res["lista"]["fuente"] = "captura_pasiva"
            break
    # 2) Si no, y tenemos token + endpoint, la pedimos nosotros RECORRIENDO TODAS las
    #    páginas (reporta el total real del período). `--n` limita SOLO las consultas
    #    de ítems, no el listado.
    todos_registros: list = []
    if cuerpo is None and bearer and url_propuesta:
        base = url_propuesta.format(periodo=periodo)
        sep = "&" if "?" in base else "?"
        res["lista"]["perPage"] = PERPAGE
        res["lista"]["codTipoOpe"] = cod_tipo_ope
        max_pag = int(os.getenv("COMP_MAX_PAGINAS", "500"))
        total_reg = None
        pagina = 1
        while pagina <= max_pag:
            url = f"{base}{sep}codTipoOpe={cod_tipo_ope}&page={pagina}&perPage={PERPAGE}"
            if pagina == 1:
                res["lista"]["url_patron"] = _patron_url(url, cfg)
            r = _get_api(page, url, bearer)
            if pagina == 1:
                res["lista"]["estado"] = r.get("estado") or r.get("error")
            if r.get("estado") != 200:
                if pagina == 1:
                    _volcar_json(carpeta, f"lista_{libro}_error.json",
                                 r.get("texto", ""), cfg)
                    res["lista"]["cuerpo_error"] = (r.get("texto", "") or "")[:600]
                    res["errores"].append(
                        {"paso": "lista", "estado": r.get("estado"),
                         "msg": _mensaje_sunat(r.get("texto", "")) or r.get("error")})
                break
            if pagina == 1:
                cuerpo = r.get("texto")               # muestra (1ª página) a disco
                res["lista"]["fuente"] = "consulta_directa"
            try:
                data_pg = json.loads(r["texto"])
            except Exception:
                break
            regs_pg = data_pg.get("registros") or []
            todos_registros.extend(regs_pg)
            pag = data_pg.get("paginacion") or {}
            total_reg = pag.get("totalRegistros", total_reg)
            if (total_reg is not None and len(todos_registros) >= total_reg) \
                    or len(regs_pg) < PERPAGE:
                break
            pagina += 1
            _pausa_comprobante()                      # ritmo prudente entre páginas
        res["lista"]["total_periodo"] = (total_reg if total_reg is not None
                                         else len(todos_registros))
        res["lista"]["paginas"] = pagina
    elif cuerpo:                                       # captura pasiva (1 página)
        try:
            todos_registros = (json.loads(cuerpo).get("registros") or [])
            res["lista"]["estado"] = 200
            res["lista"]["fuente"] = "captura_pasiva"
            res["lista"]["total_periodo"] = len(todos_registros)
            res["lista"]["paginas"] = 1
        except Exception:
            pass

    if todos_registros or cuerpo:
        if cuerpo:
            _volcar_json(carpeta, f"lista_{libro}_{periodo}.json", cuerpo, cfg)
        data_all = {"registros": todos_registros}
        # Compras: parser CONFIRMADO (campos reales RCE). Ventas: shape aún no
        # confirmada → extractor genérico por forma.
        if libro == "compras":
            registros = parse_propuesta_rce(data_all)
        else:
            registros = _registros_comprobante(data_all)
        res["lista"]["n_registros"] = len(registros)
        if registros:
            res["lista"]["claves_detectadas"] = registros[0].get("_claves")
        # Exponer los registros parseados (todas las cabeceras del período) para que
        # la ingesta (ingesta_compras) pueda persistirlas, no solo las consultadas.
        res["lista"]["registros"] = registros
        consultables = [r for r in registros if _consultable(r) is None]
        res["lista"]["n_consultables"] = len(consultables)
        objetivo = consultables[:n]                    # --n limita SOLO las consultas
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
        # Captura a nivel de CONTEXTO: las pantallas SIRE/CPE abren PESTAÑA NUEVA,
        # así escuchamos el Bearer y las respuestas de TODAS las páginas/tabs.
        ctx.on("request", cap.on_request)
        ctx.on("response", cap.on_response)
        page = ctx.new_page()
        try:
            try:
                ok_login = login_sol(page, cfg)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                # Caída de red / DNS (p.ej. ERR_NAME_NOT_RESOLVED, getaddrinfo) NO es
                # un rechazo de credenciales: se reporta como sin conexión.
                if re.search(r"ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|"
                             r"getaddrinfo|ENOTFOUND| ERR_CONNECTION|net::ERR|"
                             r"ERR_TIMED_OUT|Timeout.*exceeded.*goto", msg, re.I):
                    res["motivo"] = "sin_conexion"
                    log(f"  comp-diag: SIN CONEXIÓN / DNS ({_sanea(msg, cfg)}). "
                        f"No es rechazo de login.", "ERROR")
                else:
                    res["motivo"] = "login_error"
                    log(f"  comp-diag: error en login ({_sanea(msg, cfg)}).", "ERROR")
                return res
            if not ok_login:
                res["motivo"] = "login"
                log("  comp-diag: login SOL rechazado (credenciales).", "ERROR")
                return res
            log("  comp-diag: login OK. Reviso interstitial de contacto y navego "
                "por CÓDIGO a las pantallas que disparan los tokens...", "OK")

            # Interstitial 'VALIDA TUS DATOS DE CONTACTO' (bloquea las operaciones).
            try:
                page.wait_for_timeout(1500)
                if _manejar_contacto_sin_validar(ctx):
                    res["contacto_sin_validar"] = True
                    log("  comp-diag: contacto_sin_validar — SUNAT exige validar "
                        "correo y celular. Cerré el aviso con 'Finalizar'/'Continuar "
                        "sin confirmar' (sin tocar datos).", "WARN")
            except Exception:
                pass

            # Catálogo de menú + volcado de la página completa del menú (árbol con
            # los data-id; sirve para verificar/actualizar los códigos y para la
            # guía de perfil mínimo).
            try:
                res["menu_catalogo"] = _menu_catalogo(page, cfg)
                _volcar_json(carpeta, "00_menu.json", res["menu_catalogo"], cfg)
            except Exception:
                pass
            try:
                (carpeta / "00_menu_page.html").write_text(
                    _mask_pii(_sanea(page.content(), cfg)), encoding="utf-8")
            except Exception:
                pass

            # Abrir cada pantalla por su código y ESPERAR (hasta 30 s) a que su SPA
            # porte el Bearer, igual que esperábamos el iframe ifrVCE. Se vuelca una
            # captura de cada pantalla abierta para ver qué pasó.
            res["pantallas"] = []
            for tg in PANTALLAS:
                log(f"  comp-diag: abro {tg['slug']} (code {tg['code']})...", "INFO")
                abierto = _abrir_menu(page, tg["code"], tg["label"])
                # Dar un respiro a que abra la tab y cargue algo de contenido.
                try:
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                # El interstitial de contacto puede reaparecer en la tab de la SPA:
                # cerrarlo de forma segura (Finalizar / Continuar sin confirmar).
                try:
                    if _manejar_contacto_sin_validar(ctx):
                        res["contacto_sin_validar"] = True
                except Exception:
                    pass
                # GUARDIÁN: si CUALQUIER tab es una pantalla de aceptación/
                # consentimiento, abortarla de inmediato SIN tocar nada.
                aceptacion = None
                try:
                    for pg in ctx.pages:
                        es_ace, det = detectar_aceptacion(pg)
                        if es_ace:
                            aceptacion = det
                            break
                except Exception:
                    pass
                if aceptacion:
                    log(f"    · {tg['slug']}: pantalla_de_aceptacion ({aceptacion}) "
                        f"— NO se tocó nada.", "WARN")
                    _volcar_pantallas(ctx, carpeta, cfg, tg["slug"])
                    res["pantallas"].append(
                        {"slug": tg["slug"], "code": tg["code"], "abierto": abierto,
                         "bearer": _tiene_bearer(cap, tg["necesita"]),
                         "estado": "pantalla_de_aceptacion", "detalle": aceptacion,
                         "sin_acceso": False})
                    _cerrar_tabs_extra(ctx, page)
                    continue

                got = _esperar_bearer(page, cap, tg["necesita"], 30) if abierto else False
                # Fallback CPE: navegar directo a la SPA si el menú no disparó token.
                if tg["slug"] == "cpe_consulta" and not got:
                    try:
                        pg2 = ctx.new_page()
                        pg2.goto(SPA_CPE, wait_until="domcontentloaded",
                                 timeout=TIMEOUT_MS)
                        pg2.wait_for_timeout(1500)
                        es_ace, det = detectar_aceptacion(pg2)
                        if not es_ace:
                            got = _esperar_bearer(pg2, cap, "cpe", 20)
                    except Exception:
                        pass
                sin_acc = _hay_marcador(_texto_scopes(page), _MARCA_SIN_ACCESO)
                if sin_acc:
                    res["sin_acceso"] = True
                # Distinguir 'reintente en 5 minutos' (SUNAT) de rechazo de sesión.
                no_disp = None if got else _clasificar_no_disponible(ctx)
                estado = ("ok" if got else ("sin_acceso" if sin_acc
                          else no_disp or "sin_token"))
                res["pantallas"].append(
                    {"slug": tg["slug"], "code": tg["code"], "abierto": abierto,
                     "bearer": bool(got), "estado": estado, "sin_acceso": sin_acc})
                _volcar_pantallas(ctx, carpeta, cfg, tg["slug"])
                _cerrar_tabs_extra(ctx, page)

            # Si el Bearer de api-cpe no apareció al navegar (a veces solo se emite al
            # pulsar "Consultar"), lo obtenemos con UNA consulta real del 1er
            # comprobante (solo lectura, permitido por el guardián).
            if cap.bearer_sire and not cap.bearer_cpe:
                log("  comp-diag: no llegó token CPE al navegar; intento UNA consulta "
                    "real (Individual/Recibido) del 1er comprobante...", "INFO")
                try:
                    if _mint_cpe_token(ctx, page, cap, cfg, periodo,
                                       cod_tipo_ope, carpeta):
                        log("  comp-diag: token CPE obtenido tras la consulta real.",
                            "OK")
                except Exception:
                    pass
                _cerrar_tabs_extra(ctx, page)

            # Mapa de red capturado (patrón+estado), sin valores sensibles.
            _volcar_json(carpeta, "red_api.json", cap.red, cfg)
            res["bearer_capturado"] = {"sire": bool(cap.bearer_sire),
                                       "cpe": bool(cap.bearer_cpe)}
            # Guardar los detalles que la propia SPA pidió (si hubo consulta real).
            for i, cq in enumerate(cap.consultas_cpe[:N_DEFAULT]):
                _volcar_json(carpeta, f"detalle_spa_{i}.json", cq.get("cuerpo", ""), cfg)

            # Si NO se capturó ningún token, NO se puede consultar nada: CON ERRORES.
            if not cap.bearer_sire and not cap.bearer_cpe:
                res["con_errores"] = True
                falla = [f"{pa['slug']}={pa.get('estado', 'sin_token')}"
                         for pa in res["pantallas"]]
                res["motivo"] = ("no se obtuvo token SIRE/CPE ("
                                 + "; ".join(falla) + ")")
                log(f"  comp-diag: CON ERRORES — {res['motivo']}.", "ERROR")
                res["exito"] = False
                _volcar_json(carpeta, "resumen.json",
                             {k: v for k, v in res.items()
                              if k != "menu_catalogo"}, cfg)
                return res

            # Procesar compras (ind=2) y ventas (ind=1) solo con token disponible.
            res["libros"].append(_procesar_libro(
                page, cap, carpeta, cfg, "compras", periodo, 2, n,
                API_SIRE + _PROPUESTA_RCE, cod_tipo_ope))
            url_rvie, rvie_ok = _endpoint_ventas(cap)
            res["libros"].append(_procesar_libro(
                page, cap, carpeta, cfg, "ventas", periodo, 1, n,
                url_rvie, cod_tipo_ope, endpoint_confirmado=rvie_ok))

            # Si una LISTA de endpoint CONFIRMADO falla (4xx/5xx), el resumen NO puede
            # decir OK: CON ERRORES. Un endpoint NO confirmado (ventas RVIE en pausa,
            # prioridad baja) NO marca error: se reporta como 'endpoint_no_confirmado'.
            fallas_lista = []
            for lb in res["libros"]:
                li = lb.get("lista", {})
                est = li.get("estado")
                if not li.get("endpoint_confirmado"):
                    li["nota"] = "endpoint_no_confirmado (pausa, prioridad baja)"
                    continue
                if isinstance(est, int) and est >= 400:
                    fallas_lista.append(f"{lb['libro']} lista {est}")
                elif lb.get("errores"):
                    fallas_lista.append(f"{lb['libro']} {lb['errores'][0].get('msg')}")
            if fallas_lista:
                res["con_errores"] = True
                res["motivo"] = "CON ERRORES (" + "; ".join(fallas_lista) + ")"
                res["exito"] = False
                log(f"  comp-diag: {res['motivo']}.", "ERROR")
            else:
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


def _endpoint_ventas(cap: _Captura) -> tuple[str, bool]:
    """Endpoint de la propuesta de VENTAS (RVIE) y si está CONFIRMADO. Preferimos el
    REAL capturado del tráfico de la SPA (confirmado=True); si la SPA no lo disparó,
    caemos al candidato por simetría con RCE (confirmado=False → pausa, prioridad
    baja; su fallo NO marca CON ERRORES todo el run)."""
    for pr in cap.propuestas:
        if libro_de_url(pr["url"]) == "ventas":
            base = pr["url"].split("?")[0]
            return (re.sub(r"/\d{6}/", "/{periodo}/", base), True)
    return (API_SIRE + _PROPUESTA_RVIE, False)


def _abrir_menu(page, code: str, label: str) -> bool:
    """Lanza una opción de SOL por su CÓDIGO con el MISMO mecanismo que la web para
    las SPA nuevas (SIRE/CPE): `ejecuta('MenuInternet.htm?action=execute&code=…')`
    (NO iconExecute, que da 404 en esas pantallas). Fallback a _ir_por_codigo."""
    url = f"MenuInternet.htm?action=execute&code={code}"
    try:
        page.evaluate("(a) => ejecuta(a.u, false, '', '', a.c)",
                      {"u": url, "c": code})
        return True
    except Exception:
        try:
            return _ir_por_codigo(page, code, label)
        except Exception:
            return False


def _manejar_contacto_sin_validar(ctx) -> bool:
    """Detecta el interstitial 'VALIDA TUS DATOS DE CONTACTO'. Si está, lo CIERRA de
    forma segura: SOLO pulsa 'Finalizar' (cerrar aviso) y 'Continuar sin confirmar'
    (no cambia datos). NUNCA toca 'Aquí'/actualizar, ni envía/valida códigos, ni
    escribe en campos de contacto. Devuelve True si lo detectó (estado útil:
    'contacto_sin_validar' → el contador debe validar correo y celular en SOL)."""
    detectado = False
    try:
        paginas = list(ctx.pages)
    except Exception:
        paginas = []
    for pg in paginas:
        try:
            txt = pg.inner_text("body").lower()
        except Exception:
            continue
        if not _hay_marcador(txt, _MARCA_CONTACTO):
            continue
        detectado = True
        # Precaución: si la MISMA pantalla fuera de aceptación/consentimiento, manda
        # el guardián (NO tocar nada), no el cierre de contacto.
        try:
            es_ace, _ = detectar_aceptacion(pg)
            if es_ace:
                continue
        except Exception:
            pass
        # Cerrar SOLO con los botones seguros, por texto EXACTO. Nada más.
        for etiqueta in _BOTONES_SEGUROS_CONTACTO:
            try:
                b = pg.get_by_role("button", name=re.compile(
                    rf"^\s*{re.escape(etiqueta)}\s*$", re.I))
                if b and b.count() > 0:
                    b.first.click(timeout=2500)
                    try:
                        pg.wait_for_timeout(800)
                    except Exception:
                        pass
                    continue
            except Exception:
                pass
            try:
                b = pg.get_by_text(re.compile(
                    rf"^\s*{re.escape(etiqueta)}\s*$", re.I))
                if b and b.count() > 0:
                    b.first.click(timeout=2500)
                    try:
                        pg.wait_for_timeout(800)
                    except Exception:
                        pass
            except Exception:
                pass
    return detectado


def _clasificar_no_disponible(ctx) -> str | None:
    """Mira el texto de las tabs abiertas y distingue 'sunat_no_disponible'
    (intermitente, 'reintente en 5 minutos') de 'rechazo_sesion' (la SPA pide su
    propio login/OAuth). Devuelve el estado o None."""
    texto = ""
    try:
        for pg in ctx.pages:
            try:
                texto += " " + pg.inner_text("body")
            except Exception:
                pass
    except Exception:
        return None
    t = re.sub(r"\s+", " ", texto.lower())
    if _hay_marcador(t, _MARCA_RECHAZO_SESION):
        return "rechazo_sesion"
    if _hay_marcador(t, _MARCA_NO_DISPONIBLE):
        return "sunat_no_disponible"
    return None


def _spa_cpe_page(ctx):
    """Devuelve la tab cuya URL es la SPA de consulta de comprobantes (e-factura /
    consultacpe), o None."""
    try:
        for pg in ctx.pages:
            u = (pg.url or "").lower()
            if "consultacpe" in u or "e-factura.sunat" in u:
                return pg
    except Exception:
        pass
    return None


def _mint_cpe_token(ctx, page, cap: _Captura, cfg: SunatConfig,
                    periodo: str, cod_tipo_ope: str, carpeta: Path) -> bool:
    """Si el Bearer de api-cpe NO apareció solo, lo obtiene haciendo UNA consulta
    REAL del 1er comprobante en la SPA Individual (Recibido). Consultar es SOLO
    LECTURA → permitido por el guardián. Best-effort: si no encuentra los campos,
    desiste sin tocar nada raro (solo rellena inputs y pulsa el botón 'Consultar')."""
    # 1) Un candidato: 1er comprobante consultable de la propuesta de compras.
    reg = None
    if cap.bearer_sire:
        url = (API_SIRE + _PROPUESTA_RCE).format(periodo=periodo)
        url += f"?codTipoOpe={cod_tipo_ope}&page=1&perPage={PERPAGE}"
        r = _get_api(page, url, cap.bearer_sire)
        if r.get("estado") == 200:
            try:
                regs = _registros_comprobante(json.loads(r["texto"]))
                reg = next((x for x in regs if _consultable(x) is None), None)
            except Exception:
                reg = None
    if not reg:
        return False
    # 2) La SPA de CPE (ya abierta por el menú, o la abrimos directa).
    spa = _spa_cpe_page(ctx)
    if spa is None:
        try:
            spa = ctx.new_page()
            spa.goto(SPA_CPE, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            spa.wait_for_timeout(2000)
        except Exception:
            return False
    # GUARDIÁN antes de tocar nada.
    es_ace, _ = detectar_aceptacion(spa)
    if es_ace:
        return False
    # 3) Rellenar Individual (Recibido) y pulsar SOLO el botón 'Consultar'.
    tipo = str(reg.get("tipo") or "").zfill(2)
    serie = str(reg.get("serie") or "").upper()
    ruc = str(reg.get("ruc_emisor") or "")
    numero = str(int(re.sub(r"\D", "", str(reg.get("numero") or "0")) or "0"))
    try:
        _rellenar_campo(spa, ["ruc", "emisor"], ruc)
        _rellenar_campo(spa, ["serie"], serie)
        _rellenar_campo(spa, ["numero", "número", "num"], numero)
        # Filtro "Recibido" (radio/botón) — best-effort por texto.
        for txt in ("Recibido", "RECIBIDO"):
            try:
                el = spa.get_by_text(txt, exact=False)
                if el and el.count() > 0:
                    el.first.click(timeout=2000)
                    break
            except Exception:
                pass
        # SOLO 'Consultar' (nunca Aceptar/Continuar/Registrar/Presentar).
        try:
            spa.get_by_role("button", name=re.compile(r"consultar", re.I)).first.click(
                timeout=3000)
        except Exception:
            try:
                spa.get_by_text(re.compile(r"^\s*consultar\s*$", re.I)).first.click(
                    timeout=3000)
            except Exception:
                return False
    except Exception:
        return False
    got = _esperar_bearer(spa, cap, "cpe", 20)
    _volcar_pantallas(ctx, carpeta, cfg, "cpe_mint")
    return got


def _rellenar_campo(page, pistas, valor):
    """Rellena el 1er input cuyo name/id/placeholder/aria-label contenga alguna
    pista. Best-effort, no rompe."""
    if not valor:
        return
    for p in pistas:
        for sel in (f"input[name*='{p}' i]", f"input[id*='{p}' i]",
                    f"input[placeholder*='{p}' i]", f"input[aria-label*='{p}' i]"):
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.fill(str(valor), timeout=2000)
                    return
            except Exception:
                pass


def _tiene_bearer(cap: _Captura, cual: str) -> bool:
    if cual == "cpe":
        return bool(cap.bearer_cpe)
    if cual == "sire":
        return bool(cap.bearer_sire)
    return bool(cap.bearer_cpe or cap.bearer_sire)


def _esperar_bearer(page, cap: _Captura, cual: str, timeout_s: float = 30.0) -> bool:
    """Espera (bombeando el event-loop con wait_for_timeout, para que los handlers
    de request/response corran) hasta `timeout_s` a que aparezca el Bearer pedido.
    Igual que esperábamos a que el iframe ifrVCE navegara en decl-diag."""
    fin = time.monotonic() + timeout_s
    while time.monotonic() < fin:
        if _tiene_bearer(cap, cual):
            return True
        try:
            page.wait_for_timeout(500)   # cede al loop; dispara on_request/on_response
        except Exception:
            try:
                time.sleep(0.5)
            except Exception:
                return _tiene_bearer(cap, cual)
    return _tiene_bearer(cap, cual)


def _cerrar_tabs_extra(ctx, page):
    """Cierra las pestañas extra (deja la del menú) para que la detección de la
    siguiente pantalla no se contamine. NO hace clic en contenido; solo cierra."""
    try:
        for pg in list(ctx.pages):
            if pg is not page:
                try:
                    pg.close()
                except Exception:
                    pass
    except Exception:
        pass


def _volcar_pantallas(ctx, carpeta: Path, cfg: SunatConfig, slug: str):
    """Captura de cada página/tab abierta (HTML enmascarado + screenshot) para ver
    qué pasó. Si la pantalla trae datos de contacto (correo/celular), se OMITE el
    PNG (no se puede enmascarar un pixel) y se deja solo el HTML enmascarado."""
    for i, pg in enumerate(ctx.pages):
        tiene_contacto = False
        try:
            html = pg.content()
            tiene_contacto = _hay_marcador(html.lower(), _MARCA_CONTACTO)
            (carpeta / f"{slug}_tab{i}.html").write_text(
                _mask_pii(_sanea(html, cfg)), encoding="utf-8")
        except Exception:
            pass
        if tiene_contacto:
            # No se toma PNG: mostraría correo/celular en claro (PII).
            try:
                (carpeta / f"{slug}_tab{i}.png.OMITIDO.txt").write_text(
                    "PNG omitido: la pantalla trae datos de contacto (PII). "
                    "Ver el HTML enmascarado.", encoding="utf-8")
            except Exception:
                pass
            continue
        try:
            pg.screenshot(path=str(carpeta / f"{slug}_tab{i}.png"), full_page=True)
        except Exception:
            pass


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
    # Columnas de montos nombradas para dejar claro que vienen TAL CUAL de SUNAT
    # (sin interpretar): valor_unitario_sin_imp e importe_linea_emisor pueden NO
    # cumplir cantidad × valor = importe (impuestos/descuentos incluidos por el emisor).
    ws.append(["libro", "fecha_emision", "serie-numero", "ruc_emisor",
               "razon_emisor", "linea", "descripcion", "detalle_emisor",
               "cantidad", "unidad", "valor_unitario_sin_imp", "importe_linea_emisor",
               "IGV_comprobante", "total_comprobante", "moneda"])
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
                    it.get("linea"), it.get("descripcion"),
                    it.get("detalle_emisor"), it.get("cantidad"),
                    it.get("unidad"), it.get("valor_unitario_sin_imp"),
                    it.get("importe_linea_emisor"),
                    cab.get("igv"), cab.get("importe_total"), cab.get("moneda"),
                ])
    try:
        wb.save(str(carpeta / "muestra_items.xlsx"))
    except Exception:
        pass
