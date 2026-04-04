"""
comprobantes.py — Modelo Comprobante: comprobantes electrónicos SUNAT.

Decisiones técnicas:
- PK: Integer autoincremental. Volumen muy alto (miles por empresa/mes),
  se usa en JOINs con pagos y acumulados constantemente.
- Soft delete: SÍ. Los comprobantes son documentos tributarios que jamás se eliminan.
- Detección de duplicados: UNIQUE constraint en (ruc_emisor, serie, correlativo, ruc_receptor).
  Si se intenta insertar un duplicado, se guarda con estado 'duplicado' en vez de rechazar.
  Esto permite auditar intentos de duplicación sin perder datos.
- Notas de crédito/débito: comprobante_referencia_id (FK a sí misma, nullable).
  Permite rastrear a qué comprobante original se le aplicó la nota.
- Guías de remisión: monto = 0, factura_asociada_id nullable.
  No todas las guías se asocian inmediatamente a una factura.
- Tipos: factura, boleta, nota_credito, nota_debito, guia_remision, liquidacion.
- Estados: pendiente, validado, observado, rechazado_sunat, anulado, duplicado.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class ClasificadoPor(str, enum.Enum):
    """Quién clasificó el ítem como deducible/no deducible."""
    IA = "ia"
    USUARIO = "usuario"
    REGLA = "regla"


class TipoComprobante(str, enum.Enum):
    """Tipos de comprobante electrónico SUNAT."""
    FACTURA = "factura"
    BOLETA = "boleta"
    NOTA_CREDITO = "nota_credito"
    NOTA_DEBITO = "nota_debito"
    GUIA_REMISION = "guia_remision"
    LIQUIDACION = "liquidacion"


class EstadoComprobante(str, enum.Enum):
    """Estado del comprobante en el sistema."""
    PENDIENTE = "pendiente"
    VALIDADO = "validado"
    OBSERVADO = "observado"
    RECHAZADO_SUNAT = "rechazado_sunat"
    ANULADO = "anulado"
    DUPLICADO = "duplicado"


class Comprobante(Base, TimestampMixin, SoftDeleteMixin):
    """
    Comprobante electrónico SUNAT (factura, boleta, nota, guía, liquidación).
    Se obtiene del portal SUNAT/SIRE o se registra manualmente.
    """

    __tablename__ = "comprobantes"
    __table_args__ = (
        # Detección de duplicados: si ya existe esta combinación, insertar como 'duplicado'
        UniqueConstraint(
            "ruc_emisor", "serie", "correlativo", "ruc_receptor",
            name="uq_comprobante_duplicado",
        ),
        # Consultas frecuentes: comprobantes de una empresa por periodo
        Index("ix_comprobantes_empresa_fecha", "empresa_id", "fecha_emision"),
        # Consultas por estado dentro de una empresa
        Index("ix_comprobantes_empresa_estado", "empresa_id", "estado"),
        # Consultas por RUC emisor (buscar comprobantes de un proveedor)
        Index("ix_comprobantes_ruc_emisor", "ruc_emisor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    empresa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("empresas_cliente.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Datos del comprobante SUNAT
    tipo: Mapped[TipoComprobante] = mapped_column(
        SAEnum(TipoComprobante, name="tipo_comprobante_enum", create_constraint=True),
        nullable=False,
    )
    serie: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="Serie del comprobante, ej: F001, B001, FC01.",
    )
    correlativo: Mapped[str] = mapped_column(
        String(15), nullable=False,
        comment="Número correlativo del comprobante.",
    )

    # Emisor y receptor
    ruc_emisor: Mapped[str] = mapped_column(String(11), nullable=False)
    razon_social_emisor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ruc_receptor: Mapped[str] = mapped_column(String(11), nullable=False)
    razon_social_receptor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Montos
    moneda: Mapped[str] = mapped_column(String(3), nullable=False, default="PEN")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    igv: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    # Fechas
    fecha_emision: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_vencimiento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    estado: Mapped[EstadoComprobante] = mapped_column(
        SAEnum(EstadoComprobante, name="estado_comprobante_enum", create_constraint=True),
        nullable=False,
        default=EstadoComprobante.PENDIENTE,
    )

    # --- Notas de crédito/débito: referencia al comprobante original ---
    comprobante_referencia_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("comprobantes.id", ondelete="SET NULL"),
        nullable=True,
        comment="Para NC/ND: comprobante original al que se aplica la nota.",
    )

    # --- Guías de remisión: factura asociada (puede no existir aún) ---
    factura_asociada_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("comprobantes.id", ondelete="SET NULL"),
        nullable=True,
        comment="Para guías de remisión: factura a la que se asocia (nullable).",
    )

    # Detalle de ítems (JSONB) — el detalle completo de productos/servicios
    # Estructura: [{"descripcion": "...", "cantidad": 1, "precio_unitario": 100.00,
    #   "igv": 18.00, "total": 118.00, "es_deducible": true}]
    detalle_items: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Array de ítems del comprobante con precios y deducibilidad.",
    )

    # Hash del XML/CDR para verificación de integridad
    hash_cpe: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Hash del comprobante electrónico para verificación SUNAT.",
    )

    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Relaciones ---
    empresa: Mapped["EmpresaCliente"] = relationship(
        "EmpresaCliente", back_populates="comprobantes",
    )
    pagos: Mapped[list["Pago"]] = relationship(
        "Pago", back_populates="comprobante", foreign_keys="[Pago.comprobante_id]",
    )
    comprobante_referencia: Mapped[Optional["Comprobante"]] = relationship(
        "Comprobante",
        remote_side="Comprobante.id",
        foreign_keys=[comprobante_referencia_id],
        uselist=False,
    )
    factura_asociada: Mapped[Optional["Comprobante"]] = relationship(
        "Comprobante",
        remote_side="Comprobante.id",
        foreign_keys=[factura_asociada_id],
        uselist=False,
    )
    detalles: Mapped[list["DetalleComprobante"]] = relationship(
        "DetalleComprobante", back_populates="comprobante",
        cascade="all, delete-orphan", lazy="selectin",
    )


class DetalleComprobante(Base, TimestampMixin):
    """
    Línea de detalle de un comprobante electrónico SUNAT.
    Tabla CRÍTICA para consultas de voz por producto ("¿cuánto aceite compramos?").

    Decisiones técnicas:
    - PK: Integer. Alto volumen (N líneas por comprobante × miles de comprobantes).
    - Soft delete: NO. Se elimina en cascada con el comprobante padre.
    - Impuestos SEPARADOS: IGV, ISC, ICBPER, IVAP como columnas independientes.
      Nunca se suman entre sí porque tienen bases imponibles diferentes.
    - GIN index con pg_trgm en descripcion: permite búsqueda fuzzy tipo
      "aceite" → "aceite vegetal", "aceite de oliva", etc. Esencial para voz.
    - categoria_ia: clasificación automática por IA para reportes y deducibilidad.
    - es_deducible: NULL = sin clasificar, True/False = clasificado.
    """

    __tablename__ = "comprobante_detalle"
    __table_args__ = (
        Index("ix_detalle_comprobante_desc", "comprobante_id", "descripcion"),
        # GIN index para búsqueda de texto con pg_trgm — se crea en migración
        # porque SQLAlchemy no soporta nativamente CREATE INDEX ... USING gin ... gin_trgm_ops
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    comprobante_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("comprobantes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Identificación de línea ---
    numero_linea: Mapped[int] = mapped_column(
        SmallInteger, nullable=False,
        comment="Número secuencial de la línea dentro del comprobante.",
    )
    codigo_producto: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True,
        comment="Código interno del producto/servicio del emisor.",
    )
    codigo_sunat: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="Código del catálogo de productos/servicios SUNAT.",
    )
    descripcion: Mapped[str] = mapped_column(
        String(500), nullable=False,
        comment="Descripción del producto/servicio. Indexado con GIN+trgm para voz.",
    )
    unidad_medida: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        comment="Código SUNAT de unidad: NIU (unidad), ZZ (servicio), KGM, LTR, etc.",
    )

    # --- Cantidades y precios ---
    cantidad: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=1,
        comment="Cantidad con hasta 4 decimales (ej: 10.5000 galones).",
    )
    precio_unitario: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="Precio unitario SIN impuestos.",
    )
    precio_unitario_inc: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True,
        comment="Precio unitario CON impuestos (referencial).",
    )
    valor_venta: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="cantidad × precio_unitario (base imponible de la línea).",
    )

    # --- IGV (Impuesto General a las Ventas — 18%) ---
    igv_base: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="Base imponible para IGV.",
    )
    igv_monto: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="Monto de IGV calculado.",
    )
    igv_tipo: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        comment="Código SUNAT: 1000=IGV, 9997=Exonerado, 9998=Inafecto, 9999=Exportación.",
    )
    igv_afectacion: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True,
        comment="Tipo afectación: Gravado, Exonerado, Inafecto.",
    )

    # --- ISC (Impuesto Selectivo al Consumo — combustibles, alcohol, cigarrillos) ---
    isc_base: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
    )
    isc_monto: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
    )
    isc_tipo: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        comment="Código del sistema ISC SUNAT.",
    )

    # --- ICBPER (Impuesto al Consumo de Bolsas de Plástico — S/0.40/bolsa) ---
    icbper_cantidad: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Número de bolsas plásticas.",
    )
    icbper_monto: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="icbper_cantidad × tarifa vigente (S/0.40 en 2025).",
    )

    # --- IVAP (Impuesto a la Venta de Arroz Pilado — Loreto/Amazonía) ---
    ivap_base: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
    )
    ivap_monto: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
    )

    # --- Otros tributos no contemplados ---
    otros_tributos: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Tributos adicionales no estándar: [{codigo, nombre, base, monto}].",
    )

    # --- Total y clasificación IA ---
    total_linea: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0,
        comment="Total de la línea: valor_venta + todos los impuestos.",
    )
    categoria_ia: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Categoría asignada por IA: 'combustible', 'útiles de oficina', etc.",
    )
    es_deducible: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True,
        comment="NULL=sin clasificar, True=deducible, False=no deducible.",
    )
    clasificado_por: Mapped[Optional[ClasificadoPor]] = mapped_column(
        SAEnum(ClasificadoPor, name="clasificado_por_enum", create_constraint=True),
        nullable=True,
        comment="Quién clasificó: ia, usuario, regla.",
    )

    # --- Relaciones ---
    comprobante: Mapped["Comprobante"] = relationship(
        "Comprobante", back_populates="detalles",
    )
