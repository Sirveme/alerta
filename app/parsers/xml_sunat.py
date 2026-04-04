"""
parsers/xml_sunat.py — Parser completo del XML UBL 2.1 de SUNAT Perú.

SUNAT usa el estándar UBL 2.1 con extensiones propias.
Namespaces principales:
  cbc: urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2
  cac: urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2
  ext: urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2

Tipos de documento por UBL root element:
  Invoice        → Factura (01) o Boleta (03)
  CreditNote     → Nota de Crédito (07)
  DebitNote      → Nota de Débito (08)
  DespatchAdvice → Guía de Remisión (09)

Decisiones técnicas:
- Se usa lxml para parseo robusto con soporte de namespaces.
- Parseo flexible: si un namespace no coincide exactamente, intenta con wildcard.
- Campos obligatorios faltantes generan warnings, no errores fatales.
  El comprobante se guarda con los datos que tenga + lista de warnings.
- Cada impuesto (IGV, ISC, ICBPER, IVAP) se extrae por separado basado en
  el código de tributo SUNAT dentro de TaxScheme/ID.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from lxml import etree

logger = logging.getLogger(__name__)

# Namespaces UBL 2.1 SUNAT
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "sac": "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1",
}

# Códigos de tributo SUNAT
CODIGO_IGV = "1000"
CODIGO_ISC = "2000"
CODIGO_ICBPER = "7152"
CODIGO_IVAP = "1016"
CODIGO_EXONERADO = "9997"
CODIGO_INAFECTO = "9998"
CODIGO_EXPORTACION = "9999"

# Mapeo UBL root → tipo comprobante
ROOT_TIPO_MAP = {
    "Invoice": None,  # Depende del InvoiceTypeCode (01=factura, 03=boleta)
    "CreditNote": "nota_credito",
    "DebitNote": "nota_debito",
    "DespatchAdvice": "guia_remision",
}

INVOICE_TYPE_MAP = {
    "01": "factura",
    "03": "boleta",
}


class ParseError(Exception):
    """Error fatal de parseo XML."""
    pass


@dataclass
class LineaDetalle:
    """Línea de detalle parseada de un comprobante."""
    numero_linea: int = 0
    codigo_producto: Optional[str] = None
    codigo_sunat: Optional[str] = None
    descripcion: str = ""
    unidad_medida: Optional[str] = None
    cantidad: Decimal = Decimal("1")
    precio_unitario: Decimal = Decimal("0")
    precio_unitario_inc: Optional[Decimal] = None
    valor_venta: Decimal = Decimal("0")
    # IGV
    igv_base: Decimal = Decimal("0")
    igv_monto: Decimal = Decimal("0")
    igv_tipo: Optional[str] = None
    igv_afectacion: Optional[str] = None
    # ISC
    isc_base: Decimal = Decimal("0")
    isc_monto: Decimal = Decimal("0")
    isc_tipo: Optional[str] = None
    # ICBPER
    icbper_cantidad: int = 0
    icbper_monto: Decimal = Decimal("0")
    # IVAP
    ivap_base: Decimal = Decimal("0")
    ivap_monto: Decimal = Decimal("0")
    # Otros
    otros_tributos: Optional[list] = None
    total_linea: Decimal = Decimal("0")


@dataclass
class ComprobanteParseado:
    """Resultado completo del parseo de un XML SUNAT."""
    # Tipo
    tipo_comprobante: str = ""
    # Serie + correlativo
    serie: str = ""
    correlativo: str = ""
    # Fechas
    fecha_emision: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    # Moneda
    moneda: str = "PEN"
    # Emisor
    ruc_emisor: str = ""
    nombre_emisor: str = ""
    direccion_emisor: Optional[str] = None
    # Receptor
    ruc_receptor: str = ""
    nombre_receptor: str = ""
    direccion_receptor: Optional[str] = None
    # Referencia (NC/ND)
    comprobante_referencia_serie: Optional[str] = None
    comprobante_referencia_correlativo: Optional[str] = None
    motivo_nota: Optional[str] = None
    # Totales
    subtotal: Decimal = Decimal("0")
    total_igv: Decimal = Decimal("0")
    total_isc: Decimal = Decimal("0")
    total_icbper: Decimal = Decimal("0")
    total_ivap: Decimal = Decimal("0")
    total_otros_tributos: Decimal = Decimal("0")
    total_descuentos: Decimal = Decimal("0")
    total_cargos: Decimal = Decimal("0")
    total_comprobante: Decimal = Decimal("0")
    # Detalle
    lineas: list[LineaDetalle] = field(default_factory=list)
    # Warnings de parseo (campos faltantes, etc.)
    warnings: list[str] = field(default_factory=list)
    # Hash CPE
    hash_cpe: Optional[str] = None


def parsear_xml_sunat(xml_bytes: bytes) -> ComprobanteParseado:
    """
    Parsea un XML UBL 2.1 de SUNAT y devuelve un ComprobanteParseado.
    Lanza ParseError solo en caso de XML completamente ilegible.
    Para campos faltantes, agrega warnings y continúa.
    """
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as e:
        raise ParseError(f"XML malformado: {e}")

    resultado = ComprobanteParseado()

    # Detectar namespaces del documento (pueden variar ligeramente)
    nsmap = _detectar_namespaces(root)

    # Tipo de documento
    root_tag = etree.QName(root.tag).localname
    if root_tag in ROOT_TIPO_MAP:
        tipo = ROOT_TIPO_MAP[root_tag]
        if tipo is None and root_tag == "Invoice":
            code = _texto(root, "cbc:InvoiceTypeCode", nsmap)
            tipo = INVOICE_TYPE_MAP.get(code, "factura")
        resultado.tipo_comprobante = tipo or "factura"
    else:
        resultado.warnings.append(f"Root element desconocido: {root_tag}")
        resultado.tipo_comprobante = "factura"

    # Serie + Correlativo (ej: "F001-1847")
    id_text = _texto(root, "cbc:ID", nsmap) or ""
    if "-" in id_text:
        partes = id_text.split("-", 1)
        resultado.serie = partes[0]
        resultado.correlativo = partes[1]
    else:
        resultado.serie = id_text
        resultado.warnings.append("No se pudo separar serie-correlativo")

    # Fechas
    resultado.fecha_emision = _texto(root, "cbc:IssueDate", nsmap)
    resultado.fecha_vencimiento = _texto(root, "cbc:DueDate", nsmap)
    if not resultado.fecha_emision:
        resultado.warnings.append("Fecha de emisión faltante")

    # Moneda
    moneda_el = root.find(f".//{{{nsmap.get('cbc', '')}}}DocumentCurrencyCode")
    if moneda_el is not None:
        resultado.moneda = moneda_el.text or "PEN"

    # Emisor
    emisor = root.find(f".//{{{nsmap.get('cac', '')}}}AccountingSupplierParty")
    if emisor is not None:
        party = emisor.find(f".//{{{nsmap.get('cac', '')}}}Party")
        if party is not None:
            resultado.ruc_emisor = _texto(party, ".//cbc:ID", nsmap) or ""
            resultado.nombre_emisor = (
                _texto(party, ".//cbc:RegistrationName", nsmap) or
                _texto(party, ".//cbc:Name", nsmap) or ""
            )
            resultado.direccion_emisor = _texto(party, ".//cbc:Line", nsmap)

    # Receptor
    receptor = root.find(f".//{{{nsmap.get('cac', '')}}}AccountingCustomerParty")
    if receptor is not None:
        party = receptor.find(f".//{{{nsmap.get('cac', '')}}}Party")
        if party is not None:
            resultado.ruc_receptor = _texto(party, ".//cbc:ID", nsmap) or ""
            resultado.nombre_receptor = (
                _texto(party, ".//cbc:RegistrationName", nsmap) or
                _texto(party, ".//cbc:Name", nsmap) or ""
            )
            resultado.direccion_receptor = _texto(party, ".//cbc:Line", nsmap)

    # Referencia para NC/ND
    ref_elements = ["BillingReference", "DespatchDocumentReference"]
    for ref_name in ref_elements:
        ref = root.find(f".//{{{nsmap.get('cac', '')}}}{ref_name}")
        if ref is not None:
            ref_id = _texto(ref, ".//cbc:ID", nsmap)
            if ref_id and "-" in ref_id:
                partes = ref_id.split("-", 1)
                resultado.comprobante_referencia_serie = partes[0]
                resultado.comprobante_referencia_correlativo = partes[1]
            break

    # Motivo de NC/ND
    response_code = _texto(root, ".//cbc:ResponseCode", nsmap)
    response_desc = _texto(root, ".//cbc:Description", nsmap)
    resultado.motivo_nota = response_desc or response_code

    # Totales
    resultado.subtotal = _decimal(root, ".//cbc:LineExtensionAmount", nsmap)
    resultado.total_comprobante = _decimal(root, ".//cbc:PayableAmount", nsmap)
    resultado.total_descuentos = _decimal(root, ".//cbc:AllowanceTotalAmount", nsmap)
    resultado.total_cargos = _decimal(root, ".//cbc:ChargeTotalAmount", nsmap)

    # Impuestos totales por código
    for tax_total in root.findall(f".//{{{nsmap.get('cac', '')}}}TaxTotal"):
        for subtotal in tax_total.findall(f".//{{{nsmap.get('cac', '')}}}TaxSubtotal"):
            codigo = _texto(subtotal, ".//cac:TaxScheme/cbc:ID", nsmap)
            monto = _decimal_el(subtotal.find(f".//{{{nsmap.get('cbc', '')}}}TaxAmount"))
            if codigo == CODIGO_IGV or codigo == CODIGO_EXONERADO:
                resultado.total_igv += monto
            elif codigo == CODIGO_ISC:
                resultado.total_isc += monto
            elif codigo == CODIGO_ICBPER:
                resultado.total_icbper += monto
            elif codigo == CODIGO_IVAP:
                resultado.total_ivap += monto
            else:
                resultado.total_otros_tributos += monto

    # Hash CPE (firma digital)
    hash_el = root.find(f".//{{{nsmap.get('ds', '')}}}DigestValue")
    if hash_el is not None and hash_el.text:
        resultado.hash_cpe = hash_el.text

    # Líneas de detalle
    line_tag_map = {
        "Invoice": "InvoiceLine",
        "CreditNote": "CreditNoteLine",
        "DebitNote": "DebitNoteLine",
        "DespatchAdvice": "DespatchLine",
    }
    line_tag = line_tag_map.get(root_tag, "InvoiceLine")

    for i, line_el in enumerate(root.findall(
        f".//{{{nsmap.get('cac', '')}}}{line_tag}"
    ), 1):
        linea = _parsear_linea(line_el, nsmap, i)
        resultado.lineas.append(linea)

    return resultado


def _parsear_linea(line_el, nsmap: dict, default_num: int) -> LineaDetalle:
    """Parsea una línea de detalle (InvoiceLine, CreditNoteLine, etc.)."""
    linea = LineaDetalle()
    linea.numero_linea = int(_texto(line_el, "cbc:ID", nsmap) or str(default_num))

    # Producto
    item = line_el.find(f".//{{{nsmap.get('cac', '')}}}Item")
    if item is not None:
        linea.descripcion = _texto(item, "cbc:Description", nsmap) or ""
        linea.codigo_producto = _texto(item, ".//cbc:ID", nsmap)
        # Código SUNAT (CommodityClassification)
        commodity = _texto(item, ".//cac:CommodityClassification/cbc:ItemClassificationCode", nsmap)
        if commodity:
            linea.codigo_sunat = commodity

    # Unidad de medida
    linea.unidad_medida = _attr(line_el, "cbc:InvoicedQuantity", "unitCode", nsmap)
    if not linea.unidad_medida:
        linea.unidad_medida = _attr(line_el, "cbc:CreditedQuantity", "unitCode", nsmap)

    # Cantidad
    qty_text = (
        _texto(line_el, "cbc:InvoicedQuantity", nsmap) or
        _texto(line_el, "cbc:CreditedQuantity", nsmap) or
        _texto(line_el, "cbc:DebitedQuantity", nsmap) or "1"
    )
    linea.cantidad = _safe_decimal(qty_text)

    # Precio unitario (sin impuestos)
    pricing = line_el.find(f".//{{{nsmap.get('cac', '')}}}Price")
    if pricing is not None:
        linea.precio_unitario = _decimal_el(
            pricing.find(f".//{{{nsmap.get('cbc', '')}}}PriceAmount")
        )

    # Precio unitario con impuestos (AlternativeConditionPrice)
    alt_price = line_el.find(
        f".//{{{nsmap.get('cac', '')}}}AlternativeConditionPrice"
    )
    if alt_price is not None:
        linea.precio_unitario_inc = _decimal_el(
            alt_price.find(f".//{{{nsmap.get('cbc', '')}}}PriceAmount")
        )

    # Valor venta (LineExtensionAmount)
    linea.valor_venta = _decimal(line_el, "cbc:LineExtensionAmount", nsmap)

    # Impuestos por línea
    for tax_total in line_el.findall(f".//{{{nsmap.get('cac', '')}}}TaxTotal"):
        for subtotal in tax_total.findall(f".//{{{nsmap.get('cac', '')}}}TaxSubtotal"):
            codigo = _texto(subtotal, ".//cac:TaxScheme/cbc:ID", nsmap)
            monto = _decimal_el(subtotal.find(f".//{{{nsmap.get('cbc', '')}}}TaxAmount"))
            base = _decimal_el(subtotal.find(f".//{{{nsmap.get('cbc', '')}}}TaxableAmount"))
            tipo_afect = _texto(subtotal, ".//cbc:TaxExemptionReasonCode", nsmap)

            if codigo == CODIGO_IGV or codigo in (CODIGO_EXONERADO, CODIGO_INAFECTO, CODIGO_EXPORTACION):
                linea.igv_base = base
                linea.igv_monto = monto
                linea.igv_tipo = codigo
                linea.igv_afectacion = _map_afectacion(codigo, tipo_afect)
            elif codigo == CODIGO_ISC:
                linea.isc_base = base
                linea.isc_monto = monto
                linea.isc_tipo = _texto(subtotal, ".//cbc:TierRange", nsmap)
            elif codigo == CODIGO_ICBPER:
                linea.icbper_monto = monto
                # Cantidad de bolsas = monto / tarifa (S/0.50 en 2026)
                try:
                    linea.icbper_cantidad = int(monto / Decimal("0.50"))
                except Exception:
                    linea.icbper_cantidad = 0
            elif codigo == CODIGO_IVAP:
                linea.ivap_base = base
                linea.ivap_monto = monto
            else:
                if linea.otros_tributos is None:
                    linea.otros_tributos = []
                linea.otros_tributos.append({
                    "codigo": codigo,
                    "base": str(base),
                    "monto": str(monto),
                })

    # Total línea
    linea.total_linea = (
        linea.valor_venta + linea.igv_monto + linea.isc_monto +
        linea.icbper_monto + linea.ivap_monto
    )

    return linea


# ── Utilidades de parseo ─────────────────────────────────────

def _detectar_namespaces(root) -> dict:
    """Detecta los namespaces usados en el documento."""
    nsmap = dict(NS)  # Empezar con los estándar
    # Sobrescribir con los del documento si existen
    for prefix, uri in root.nsmap.items():
        if prefix and prefix in nsmap:
            nsmap[prefix] = uri
    return nsmap


def _texto(element, xpath: str, nsmap: dict) -> Optional[str]:
    """Extrae texto de un elemento XPath. Maneja namespaces."""
    try:
        # Intentar con namespaces completos
        el = element.find(xpath, nsmap)
        if el is not None and el.text:
            return el.text.strip()
    except Exception:
        pass

    # Fallback: buscar sin namespace (wildcard)
    try:
        local_name = xpath.split(":")[-1] if ":" in xpath else xpath
        for child in element.iter():
            tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
            if tag == local_name and child.text:
                return child.text.strip()
    except Exception:
        pass

    return None


def _attr(element, xpath: str, attr: str, nsmap: dict) -> Optional[str]:
    """Extrae atributo de un elemento XPath."""
    try:
        el = element.find(xpath, nsmap)
        if el is not None:
            return el.get(attr)
    except Exception:
        pass
    return None


def _decimal(element, xpath: str, nsmap: dict) -> Decimal:
    """Extrae Decimal de un elemento XPath."""
    text = _texto(element, xpath, nsmap)
    return _safe_decimal(text)


def _decimal_el(element) -> Decimal:
    """Extrae Decimal del texto de un elemento lxml."""
    if element is not None and element.text:
        return _safe_decimal(element.text)
    return Decimal("0")


def _safe_decimal(text: Optional[str]) -> Decimal:
    """Convierte texto a Decimal de forma segura."""
    if not text:
        return Decimal("0")
    try:
        return Decimal(text.strip().replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def _map_afectacion(codigo: str, tipo_afect: Optional[str]) -> str:
    """Mapea código de tributo a tipo de afectación legible."""
    if codigo == CODIGO_IGV:
        return "Gravado"
    if codigo == CODIGO_EXONERADO:
        return "Exonerado"
    if codigo == CODIGO_INAFECTO:
        return "Inafecto"
    if codigo == CODIGO_EXPORTACION:
        return "Exportación"
    return tipo_afect or "Desconocido"
