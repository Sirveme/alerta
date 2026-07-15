"""
models.py — alerta.pe (Buzón SUNAT para estudios contables)
═══════════════════════════════════════════════════════════════════════
Modelo de datos SQLAlchemy. Multi-tenant real con UUIDs.

Jerarquía:
    EstudioContable  (tu cliente que paga — el tenant)
      └── Contribuyente   (cada RUC vigilado — la Ficha)
            ├── CredencialSol   (usuario+clave SOL, cifrada Fernet)
            └── Notificacion    (cada mensaje de SUNAT)
                  └── Adjunto   (PDF — key de GCS, no los bytes)

Reglas de diseño (decididas en sesión):
  - UUIDs en todas las tablas (evita enumeración en multi-tenant).
  - Multi-tenant: TODA fila cuelga de estudio_id; es el filtro obligatorio.
  - Dedup: nunca guardar dos veces el mismo mensaje SUNAT
    (unique por contribuyente_id + cod_mensaje_sunat + tipo_msj).
  - Todo en hora Lima (America/Lima). Timestamps con tz.
  - Clave SOL cifrada con Fernet (FERNET_KEY_SOL). Nunca texto plano.
  - Trazabilidad legal: quien_cargo / cargado_at en credenciales.
  - Campos de Capa 4 (clasificación/IA) presentes desde el día 1,
    aunque la lógica se construya después.

Stack: FastAPI async + SQLAlchemy 2.0 + PostgreSQL (Railway).
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, Index, CheckConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TZ_LIMA = ZoneInfo("America/Lima")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def nuevo_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────
# Mixins reutilizables
# ─────────────────────────────────────────────────────────────────────
class TimestampMixin:
    """created_at / updated_at en hora Lima, automáticos."""
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)
    actualizado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, onupdate=ahora_lima,
        nullable=False)


# ─────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────
class RolUsuario(str, enum.Enum):
    """RBAC dentro de un estudio contable."""
    ADMIN = "admin"          # dueño del estudio: todo
    CONTADOR = "contador"    # opera, ve notificaciones, gestiona RUCs
    ASISTENTE = "asistente"  # solo lectura / carga


class EstadoContribuyente(str, enum.Enum):
    ACTIVO = "activo"
    INACTIVO = "inactivo"        # el estudio lo pausó
    ERROR_CREDENCIAL = "error_credencial"  # la clave SOL falló al scrapear


class TipoBandeja(int, enum.Enum):
    """tipoMsj real del visor SUNAT (descubierto en el scraper)."""
    MENSAJES = 1
    NOTIFICACIONES = 2


class Urgencia(str, enum.Enum):
    """Clasificación de urgencia (Capa 4). Default SIN_CLASIFICAR."""
    SIN_CLASIFICAR = "sin_clasificar"
    INFORMATIVA = "informativa"
    IMPORTANTE = "importante"
    URGENTE = "urgente"          # plazo legal corriendo
    CRITICA = "critica"          # cobranza coactiva / inminente


class TipoDocumento(str, enum.Enum):
    """Clasificación del documento SUNAT para filtros fiables (zAlerta-01 A.4).

    Convive con notificaciones.tipo_documento (String libre): este enum
    permite filtrar por chips/dropdown de forma confiable.
    """
    ORDEN_PAGO = "orden_pago"
    MULTA = "multa"
    RESOLUCION_DETERMINACION = "resolucion_determinacion"
    FRACCIONAMIENTO = "fraccionamiento"
    ESQUELA = "esquela"
    COBRANZA_COACTIVA = "cobranza_coactiva"
    PAGO = "pago"              # constancia de pago (Formulario 1662, zAlerta-69)
    AVISO = "aviso"
    OTRO = "otro"


# Etiqueta legible (es) para cada tipo de documento — para chips/filtros.
ETIQUETA_TIPO_DOCUMENTO: dict[str, str] = {
    "orden_pago": "Órdenes de Pago",
    "multa": "Multas",
    "resolucion_determinacion": "Resoluciones de Determinación",
    "fraccionamiento": "Fraccionamientos",
    "esquela": "Esquelas",
    "cobranza_coactiva": "Cobranza Coactiva",
    "pago": "Pagos confirmados",
    "aviso": "Avisos",
    "otro": "Otros",
}


class TipoValorado(str, enum.Enum):
    """Tipo de documento de DEUDA valorada (zAlerta-34). Solo los que comportan
    deuda: se les baja el 2º PDF (documento real) y se valoran."""
    ORDEN_PAGO = "orden_pago"
    RESOLUCION_MULTA = "resolucion_multa"
    RESOLUCION_DETERMINACION = "resolucion_determinacion"
    COBRANZA_COACTIVA = "cobranza_coactiva"
    FRACCIONAMIENTO = "fraccionamiento"
    PAGO = "pago"              # constancia de pago (no es deuda; zAlerta-69)
    ESQUELA_OMISO = "esquela_omiso"   # esquela de omiso (aviso; zAlerta-81)


# Mapa TipoDocumento (clasificación del buzón) → TipoValorado. Los que llevan
# 2º PDF permanente en GCS (velocidad rápida): deuda + PAGO (constancia de pago,
# zAlerta-69). Un tipo ausente NO se valora (no se baja 2º PDF).
TIPODOC_A_VALORADO: dict = {
    TipoDocumento.ORDEN_PAGO: TipoValorado.ORDEN_PAGO,
    TipoDocumento.MULTA: TipoValorado.RESOLUCION_MULTA,
    TipoDocumento.RESOLUCION_DETERMINACION: TipoValorado.RESOLUCION_DETERMINACION,
    TipoDocumento.COBRANZA_COACTIVA: TipoValorado.COBRANZA_COACTIVA,
    TipoDocumento.FRACCIONAMIENTO: TipoValorado.FRACCIONAMIENTO,
    TipoDocumento.PAGO: TipoValorado.PAGO,
    # Esquela de Omiso (zAlerta-81): baja su documento (2º PDF) con período+tributo.
    TipoDocumento.ESQUELA: TipoValorado.ESQUELA_OMISO,
}


class TipoReaccion(str, enum.Enum):
    """Feedback del contador sobre una notificación (señal de producto)."""
    UTIL = "util"
    NO_UTIL = "no_util"
    DESTACADA = "destacada"


# ─────────────────────────────────────────────────────────────────────
# Cuentas: tipo de cuenta, planes comerciales y suscripción (zAlerta-06)
# ─────────────────────────────────────────────────────────────────────
class TipoCuenta(str, enum.Enum):
    """Una organización (estudios_contables) puede ser de dos tipos."""
    EMPRESARIO = "empresario"   # dueño que vigila su propio RUC
    ESTUDIO = "estudio"         # contador que vigila RUCs de clientes


class PlanComercial(str, enum.Enum):
    """Planes comerciales (zAlerta-06 A.2). El precio/límites viven en
    LIMITES_PLAN (código, no BD), para validar y mostrar sin tocar el esquema."""
    EMPRESARIO = "empresario"                 # S/5 · 1 RUC · 2 usuarios
    INICIA = "inicia"                         # S/15 · 4 RUCs · 2 usuarios
    PEQUENO = "pequeno"                        # S/25 · 10 RUCs · 3 usuarios
    MEDIANO = "mediano"                        # S/45 · 25 RUCs · 5 usuarios
    GRANDE = "grande"                          # S/85 · 50 RUCs · 10 usuarios
    CORPORATIVO = "corporativo"                # a medida · 51+
    CLIENTE_DE_ESTUDIO = "cliente_de_estudio"  # GRATIS · cuenta del empresario


class EstadoSuscripcion(str, enum.Enum):
    """Estado de la suscripción. En modo testers todos quedan en PRUEBA."""
    PRUEBA = "prueba"
    ACTIVA = "activa"
    VENCIDA = "vencida"


# Mapa plan → límites y precio (S/). Vive en código, NO en BD (zAlerta-06 A.2).
# precio_soles None = "a medida"; 0 = gratis. nombre = etiqueta para la UI.
LIMITES_PLAN: dict[str, dict] = {
    "empresario":         {"nombre": "Empresario",        "max_contribuyentes": 1,    "max_usuarios": 2,   "precio_soles": 5},
    "inicia":             {"nombre": "Inicia",            "max_contribuyentes": 4,    "max_usuarios": 2,   "precio_soles": 15},
    "pequeno":            {"nombre": "Pequeño",           "max_contribuyentes": 10,   "max_usuarios": 3,   "precio_soles": 25},
    "mediano":            {"nombre": "Mediano",           "max_contribuyentes": 25,   "max_usuarios": 5,   "precio_soles": 45},
    "grande":             {"nombre": "Grande",            "max_contribuyentes": 50,   "max_usuarios": 10,  "precio_soles": 85},
    "corporativo":        {"nombre": "Corporativo",       "max_contribuyentes": 1000, "max_usuarios": 100, "precio_soles": None},
    "cliente_de_estudio": {"nombre": "Cliente de estudio", "max_contribuyentes": 1,  "max_usuarios": 2,   "precio_soles": 0},
}

# Planes ofrecidos en /registro según el tipo elegido (zAlerta-06 B.1).
# cliente_de_estudio NO se ofrece (lo crea el contador, no se auto-registra).
PLANES_POR_TIPO: dict[str, list[str]] = {
    "empresario": ["empresario"],
    "estudio": ["inicia", "pequeno", "mediano", "grande", "corporativo"],
}


def limites_de(plan: str | None) -> dict:
    """Límites del plan; fallback permisivo para planes legados (ej. 'basico')."""
    return LIMITES_PLAN.get(
        plan or "", {"nombre": plan or "—", "max_contribuyentes": 50,
                     "max_usuarios": 10, "precio_soles": None})


# ═════════════════════════════════════════════════════════════════════
# 1. EstudioContable — el tenant
# ═════════════════════════════════════════════════════════════════════
class EstudioContable(Base, TimestampMixin):
    __tablename__ = "estudios_contables"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    razon_social: Mapped[str] = mapped_column(String(255), nullable=False)
    ruc: Mapped[str | None] = mapped_column(String(11), unique=True)  # RUC del propio estudio
    correo_contacto: Mapped[str | None] = mapped_column(String(255))
    telefono: Mapped[str | None] = mapped_column(String(30))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Tipo de cuenta (zAlerta-06 A.1): "estudio" (contador) | "empresario".
    # Guardamos el .value de TipoCuenta como String (sin enum Postgres, para
    # que la migración sea un simple ADD COLUMN idempotente).
    tipo_cuenta: Mapped[str] = mapped_column(
        String(20), default=TipoCuenta.ESTUDIO.value, nullable=False)

    # Plan comercial (S/) — palanca de monetización. Guarda un PlanComercial.value.
    plan: Mapped[str] = mapped_column(String(50), default="basico", nullable=False)
    max_contribuyentes: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    max_usuarios: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Suscripción (zAlerta-06 B.1) — modo testers: todos en "prueba", sin pago.
    estado_suscripcion: Mapped[str] = mapped_column(
        String(20), default=EstadoSuscripcion.PRUEBA.value, nullable=False)
    suscripcion_vence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Fecha del último pago que activó/renovó la suscripción (zAlerta-14).
    fecha_ultimo_pago: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Marca de inicio de una "sesión de pago" (zAlerta-15): cuando el usuario dice
    # "voy a pagar ahora", para acotar la búsqueda a una ventana corta. UTC.
    inicio_sesion_pago: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # CANDADO de precio (zAlerta-24): el precio que aseguró al capturarse viaja del
    # lead a la cuenta y es el que se cobra siempre. Se fija UNA vez, no cambia.
    precio_congelado: Mapped[int | None] = mapped_column(Integer)
    precio_congelado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Insignia/código Fundador: se genera al PRIMER pago (no al capturar).
    es_fundador: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    codigo_fundador: Mapped[str | None] = mapped_column(String(20), unique=True)
    fundador_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # WhatsApp de contacto (con código país). Clave para el onboarding viral
    # del empresario (zAlerta-06 A.4 / C).
    whatsapp: Mapped[str | None] = mapped_column(String(20))

    # Si es una cuenta-empresario creada por un estudio: qué estudio la creó
    # (su contador/proveedor). NULL si se auto-registró (zAlerta-06 A.3).
    creado_por_estudio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estudios_contables.id", ondelete="SET NULL"), nullable=True)

    usuarios: Mapped[list["Usuario"]] = relationship(
        back_populates="estudio", cascade="all, delete-orphan")
    # foreign_keys explícito: Contribuyente tiene 2 FKs a estudios_contables
    # (estudio_id = quién vigila; cuenta_empresario_id = dueño). Esta relación
    # es por estudio_id.
    contribuyentes: Mapped[list["Contribuyente"]] = relationship(
        back_populates="estudio", cascade="all, delete-orphan",
        foreign_keys="Contribuyente.estudio_id")
    grupos: Mapped[list["Grupo"]] = relationship(
        back_populates="estudio", cascade="all, delete-orphan")


# ═════════════════════════════════════════════════════════════════════
# 2. Usuario — quienes acceden al sistema, dentro de un estudio (RBAC)
# ═════════════════════════════════════════════════════════════════════
class Usuario(Base, TimestampMixin):
    __tablename__ = "usuarios"
    __table_args__ = (
        # Login por DNI: único dentro del estudio
        UniqueConstraint("estudio_id", "dni", name="uq_usuario_dni_estudio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    # LOGIN POR DNI (no por correo). nullable: el empresario (zAlerta-06) se
    # identifica por WhatsApp, no por DNI; el login acepta DNI o WhatsApp.
    dni: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    # Identificador de login alternativo del empresario (con código país).
    whatsapp: Mapped[str | None] = mapped_column(String(20), index=True)
    correo: Mapped[str | None] = mapped_column(String(255))  # contacto/notificaciones
    # Hash de la clave de acceso al sistema (Argon2, como en CCPL — NO bcrypt)
    access_code: Mapped[str] = mapped_column(String(255), nullable=False)
    rol: Mapped[RolUsuario] = mapped_column(
        Enum(RolUsuario), default=RolUsuario.CONTADOR, nullable=False)
    # Cargo declarado del empresario en su negocio (zAlerta-11a B.5): dueño /
    # administrador / gerente / encargado. Opcional, solo informativo.
    cargo: Mapped[str | None] = mapped_column(String(30))
    # Declaración de responsabilidad del acceso (zAlerta-12 P3): evidencia de que
    # aceptó estar autorizado a acceder al RUC. Timestamp + RUC declarado.
    responsabilidad_aceptada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responsabilidad_ruc: Mapped[str | None] = mapped_column(String(11))
    # Métrica de lectura del push (botón GRACIAS, zAlerta-12 P1.d).
    ultima_alerta_vista_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    debe_cambiar_clave: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Empresario cuya clave aún la entrega Soporte manualmente (zAlerta-06 C.4).
    clave_pendiente: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ultimo_acceso_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Última visita al resumen/bienvenida (zAlerta-07): base para "nuevas desde
    # tu última visita". Se actualiza DESPUÉS de calcular el resumen.
    ultima_visita_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    estudio: Mapped["EstudioContable"] = relationship(back_populates="usuarios")


# ═════════════════════════════════════════════════════════════════════
# 3. Contribuyente — cada RUC vigilado (la Ficha)
# ═════════════════════════════════════════════════════════════════════
class Contribuyente(Base, TimestampMixin):
    __tablename__ = "contribuyentes"
    __table_args__ = (
        # Un RUC no se repite dentro del mismo estudio
        UniqueConstraint("estudio_id", "ruc", name="uq_contribuyente_ruc_estudio"),
        Index("ix_contribuyente_estudio_estado", "estudio_id", "estado"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    ruc: Mapped[str] = mapped_column(String(11), nullable=False)
    razon_social: Mapped[str | None] = mapped_column(String(255))

    # Ficha (autocompletable vía API RUC de Facturalo)
    ciiu: Mapped[str | None] = mapped_column(String(10))
    estado_sunat: Mapped[str | None] = mapped_column(String(50))   # activo/baja/etc
    condicion_sunat: Mapped[str | None] = mapped_column(String(50))  # habido/no habido
    ubigeo: Mapped[str | None] = mapped_column(String(6))
    domicilio_fiscal: Mapped[str | None] = mapped_column(Text)
    establecimientos_anexos: Mapped[dict | None] = mapped_column(JSONB)  # se llena después

    estado: Mapped[EstadoContribuyente] = mapped_column(
        Enum(EstadoContribuyente), default=EstadoContribuyente.ACTIVO, nullable=False)

    # Cuenta-empresario dueña de este RUC (zAlerta-06 A.3). El estudio que lo
    # VIGILA sigue en estudio_id; este campo es la VISTA del dueño (solo lectura).
    # NULL si el estudio aún no creó la cuenta del empresario.
    cuenta_empresario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estudios_contables.id", ondelete="SET NULL"),
        nullable=True, index=True)

    # Control de scraping
    ultimo_scrapeo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_scrapeo_ok: Mapped[bool | None] = mapped_column(Boolean)
    # Último barrido COMPLETO exitoso (zAlerta-46). El incremental solo se activa
    # si esto existe (hubo un full previo = base contra la que comparar). El primer
    # scan y el barrido nocturno lo setean.
    ultimo_barrido_full_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Filtro de años de deuda POR BUZÓN (zAlerta-72). `desde` = año desde el que el
    # usuario quiere VER su deuda (filtro de lectura, sube/baja libre). `cubierto`
    # = año más antiguo REALMENTE descargado a BD (el scraper baja desde aquí; solo
    # decrece al ampliar). NULL en ambos → default año_actual − 2.
    anio_deuda_desde: Mapped[int | None] = mapped_column(Integer)
    anio_deuda_cubierto_desde: Mapped[int | None] = mapped_column(Integer)
    # Censo del buzón (zAlerta-83): mapa {año: nº documentos} SIN descargar (solo
    # índice). Dice el tamaño del trabajo antes de hacerlo. + fecha del censo.
    censo_json: Mapped[dict | None] = mapped_column(JSONB)
    censo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Aviso (push) "tu credencial dejó de servir" enviado UNA vez al entrar en
    # ERROR_CREDENCIAL (zAlerta-13 P2). Se limpia al reconectar.
    credencial_error_avisada: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False)

    # Actualización bajo demanda (zAlerta-04): el botón "Actualizar ahora" de la
    # WebApp NO scrapea en el proceso web (liviano, sin Playwright). Marca este
    # flag y el worker separado (worker.py) lo scrapea con prioridad en su
    # próximo ciclo corto, luego lo limpia.
    actualizar_solicitado: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False)
    actualizar_solicitado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    estudio: Mapped["EstudioContable"] = relationship(
        back_populates="contribuyentes", foreign_keys=[estudio_id])
    credencial: Mapped["CredencialSol"] = relationship(
        back_populates="contribuyente", uselist=False, cascade="all, delete-orphan")
    notificaciones: Mapped[list["Notificacion"]] = relationship(
        back_populates="contribuyente", cascade="all, delete-orphan")
    # N:N con grupos (etiquetas). Un contribuyente puede estar en VARIOS grupos.
    grupos_links: Mapped[list["ContribuyenteGrupo"]] = relationship(
        back_populates="contribuyente", cascade="all, delete-orphan")
    grupos: Mapped[list["Grupo"]] = relationship(
        secondary="contribuyente_grupo", viewonly=True,
        back_populates="contribuyentes")


# ═════════════════════════════════════════════════════════════════════
# 4. CredencialSol — usuario + clave SOL cifrada (Fernet)
# ═════════════════════════════════════════════════════════════════════
class CredencialSol(Base, TimestampMixin):
    __tablename__ = "credenciales_sol"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    # 1:1 con contribuyente
    contribuyente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True)
    # Redundante pero útil para el filtro multi-tenant directo
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    usuario_sol: Mapped[str] = mapped_column(String(50), nullable=False)
    # Clave SOL CIFRADA con Fernet (token base64). NUNCA texto plano.
    clave_sol_cifrada: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_usuario: Mapped[int] = mapped_column(Integer, default=2, nullable=False)  # 2=RUC+SOL

    # Trazabilidad legal (cumplimiento constitucional)
    quien_cargo: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"))
    cargado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)

    # Estado de la credencial
    valida: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ultimo_login_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contribuyente: Mapped["Contribuyente"] = relationship(back_populates="credencial")


# ═════════════════════════════════════════════════════════════════════
# 5. Notificacion — cada mensaje de SUNAT (núcleo del producto)
# ═════════════════════════════════════════════════════════════════════
class Notificacion(Base, TimestampMixin):
    __tablename__ = "notificaciones"
    __table_args__ = (
        # REGLA DE ORO: dedup — nunca guardar dos veces el mismo mensaje SUNAT
        UniqueConstraint(
            "contribuyente_id", "cod_mensaje_sunat", "tipo_msj",
            name="uq_notif_dedup"),
        Index("ix_notif_estudio_fecha", "estudio_id", "fecha_publica_sunat"),
        Index("ix_notif_estudio_urgencia", "estudio_id", "urgencia"),
        Index("ix_notif_no_leida", "estudio_id", "leida"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)
    contribuyente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="CASCADE"),
        nullable=False, index=True)

    # Identificación SUNAT (claves de dedup)
    cod_mensaje_sunat: Mapped[str] = mapped_column(String(50), nullable=False)
    tipo_msj: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 o 2

    # Contenido (del JSON real del visor)
    asunto: Mapped[str | None] = mapped_column(Text)                  # desAsunto
    texto_html: Mapped[str | None] = mapped_column(Text)             # msjMensaje
    remitente: Mapped[str | None] = mapped_column(String(100))       # codUsremisor (SUNAT, etc)
    cant_adjuntos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Carpeta de origen en SUNAT (zAlerta-28): señal oficial para clasificar.
    # "[03] Órdenes de Pago", "[04] Resoluciones de Ejecución Coactiva", etc.
    cod_carpeta: Mapped[str | None] = mapped_column(String(10))
    nombre_carpeta: Mapped[str | None] = mapped_column(String(120))

    # Fechas SUNAT (en hora Lima). fec_publica es la que cuenta para plazos.
    fecha_envio_sunat: Mapped[str | None] = mapped_column(String(30))   # fecEnvio (dd/MM/YYYY)
    fecha_publica_sunat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Estado de lectura (para el dashboard del contador)
    leida: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    leida_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Capa 4: clasificación e inteligencia (lógica vendrá después) ──
    urgencia: Mapped[Urgencia] = mapped_column(
        Enum(Urgencia), default=Urgencia.SIN_CLASIFICAR, nullable=False)
    tipo_documento: Mapped[str | None] = mapped_column(String(100))  # Orden de Pago, Esquela, etc (String libre)
    # Enum para filtros fiables (zAlerta-01 A.4). Conserva el String libre de arriba.
    tipo_documento_enum: Mapped[TipoDocumento | None] = mapped_column(
        Enum(TipoDocumento), default=TipoDocumento.OTRO, nullable=True, index=True)
    # Subtipo de resolución coactiva (zAlerta-70): ejecucion/retencion/
    # levantamiento/reduccion/conclusion/fl. VARCHAR (sin enum pg → sin DDL de tipo).
    # Determina grupo (riesgo/alivio/cierre/admin), color, etiqueta y si es deuda.
    subtipo_coactivo: Mapped[str | None] = mapped_column(String(20))
    plazo_vencimiento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clasificado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumen_ia: Mapped[str | None] = mapped_column(Text)  # insight del agente IA
    metadata_ia: Mapped[dict | None] = mapped_column(JSONB)

    # Guardar el JSON crudo del detalle por si necesitamos reprocesar
    raw_detalle: Mapped[dict | None] = mapped_column(JSONB)

    # Control de notificación al usuario (push)
    notificado_push: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notificado_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contribuyente: Mapped["Contribuyente"] = relationship(back_populates="notificaciones")
    adjuntos: Mapped[list["Adjunto"]] = relationship(
        back_populates="notificacion", cascade="all, delete-orphan")
    reacciones: Mapped[list["Reaccion"]] = relationship(
        back_populates="notificacion", cascade="all, delete-orphan")


# ═════════════════════════════════════════════════════════════════════
# 6. Adjunto — PDF de una notificación (en GCS; la BD guarda la key)
# ═════════════════════════════════════════════════════════════════════
class Adjunto(Base, TimestampMixin):
    __tablename__ = "adjuntos"
    __table_args__ = (
        UniqueConstraint("notificacion_id", "cod_archivo_sunat",
                         name="uq_adjunto_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    notificacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notificaciones.id", ondelete="CASCADE"),
        nullable=False, index=True)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    cod_archivo_sunat: Mapped[str] = mapped_column(String(50), nullable=False)
    nombre_archivo: Mapped[str] = mapped_column(String(500), nullable=False)
    tamano_bytes: Mapped[int | None] = mapped_column(Integer)

    # Almacenamiento: la BD guarda la KEY del bucket GCS, no los bytes.
    # (Para el MVP de prueba, bytea_temporal puede contener el PDF; en
    #  producción se migra a gcs_key y bytea_temporal queda NULL.)
    gcs_key: Mapped[str | None] = mapped_column(String(500))
    bytea_temporal: Mapped[bytes | None] = mapped_column()  # solo MVP/prueba

    descargado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    descargado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Custodia (zAlerta-83): 'activo' (en la nube) | 'liberable' (viejo, se puede
    # descargar y liberar) | 'archivado' (bytea liberado; el usuario lo custodia).
    # La antigüedad sale de creado_at. NULL = activo por defecto.
    custodia_estado: Mapped[str | None] = mapped_column(String(20))

    notificacion: Mapped["Notificacion"] = relationship(back_populates="adjuntos")


# ═════════════════════════════════════════════════════════════════════
# 7. Grupo — agrupación/etiqueta de contribuyentes (zAlerta-01 A.1)
# ═════════════════════════════════════════════════════════════════════
class Grupo(Base, TimestampMixin):
    """Agrupación de contribuyentes definida por el estudio. Multi-tenant.

    Son ETIQUETAS, no carpetas rígidas: un contribuyente puede estar en
    varios grupos (ver ContribuyenteGrupo). Por régimen (NRUS, RER, RMT,
    Régimen General) o libres ("Tarapoto", "Pagan tarde").
    """
    __tablename__ = "grupos"
    __table_args__ = (
        UniqueConstraint("estudio_id", "nombre", name="uq_grupo_nombre_estudio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str | None] = mapped_column(String(20))   # hex del borde/punto, ej "#A32D2D"
    icono: Mapped[str | None] = mapped_column(String(50))   # ícono Tabler, ej "ti-folder"
    orden: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    estudio: Mapped["EstudioContable"] = relationship(back_populates="grupos")
    contribuyentes_links: Mapped[list["ContribuyenteGrupo"]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan")
    contribuyentes: Mapped[list["Contribuyente"]] = relationship(
        secondary="contribuyente_grupo", viewonly=True,
        back_populates="grupos")


# ═════════════════════════════════════════════════════════════════════
# 8. ContribuyenteGrupo — tabla puente N:N (zAlerta-01 A.2)
# ═════════════════════════════════════════════════════════════════════
class ContribuyenteGrupo(Base):
    """Un contribuyente puede estar en VARIOS grupos (etiquetas)."""
    __tablename__ = "contribuyente_grupo"
    __table_args__ = (
        UniqueConstraint("contribuyente_id", "grupo_id", name="uq_contrib_grupo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    contribuyente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="CASCADE"),
        nullable=False, index=True)
    grupo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grupos.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # Redundante pero útil para el filtro multi-tenant directo
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    contribuyente: Mapped["Contribuyente"] = relationship(back_populates="grupos_links")
    grupo: Mapped["Grupo"] = relationship(back_populates="contribuyentes_links")


# ═════════════════════════════════════════════════════════════════════
# 9. Reaccion — feedback del contador sobre una notificación (zAlerta-01 A.3)
# ═════════════════════════════════════════════════════════════════════
class Reaccion(Base):
    """Señal de producto: útil / no útil / destacada sobre una notificación."""
    __tablename__ = "reacciones"
    __table_args__ = (
        UniqueConstraint("usuario_id", "notificacion_id", name="uq_reaccion"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"))
    notificacion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notificaciones.id", ondelete="CASCADE"),
        nullable=True, index=True)

    tipo: Mapped[TipoReaccion] = mapped_column(Enum(TipoReaccion), nullable=False)
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)

    notificacion: Mapped["Notificacion"] = relationship(back_populates="reacciones")


# ═════════════════════════════════════════════════════════════════════
# 10. PushSuscripcion — suscripción Web Push por dispositivo (zAlerta-07)
# ═════════════════════════════════════════════════════════════════════
class PushSuscripcion(Base):
    """Una suscripción push por dispositivo/navegador de un usuario.

    Multi-tenant (estudio_id) y UUID, consistente con el resto del modelo
    nuevo. El envío lo hace el worker vía push_service (pywebpush + VAPID).
    """
    __tablename__ = "push_suscripciones"
    __table_args__ = (
        UniqueConstraint("usuario_id", "endpoint", name="uq_push_usuario_endpoint"),
        # Suscripción ligada a la PERSONA (zAlerta-67): unicidad por persona+endpoint.
        UniqueConstraint("persona_id", "endpoint", name="uq_push_persona_endpoint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # usuario_id: modelo viejo (nullable desde zAlerta-67; las personas nuevas no
    # tienen fila en usuarios). persona_id: modelo nuevo (login por DNI).
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=True, index=True)
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=True, index=True)

    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)

    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


# ═════════════════════════════════════════════════════════════════════
# 11. RucCache — caché incremental de la API RUC (zAlerta-10 D)
# ═════════════════════════════════════════════════════════════════════
class RucCache(Base):
    """Caché simple ruc → razón social (germen del futuro 'padrón propio').

    Se llena al consultar la API externa (apis.net.pe) durante la Fase 1 del
    alta. NO es multi-tenant: el padrón RUC↔razón social es público, lo
    comparten todos los estudios. Mantener simple (zAlerta-10 D).
    """
    __tablename__ = "ruc_cache"

    ruc: Mapped[str] = mapped_column(String(11), primary_key=True)
    razon_social: Mapped[str | None] = mapped_column(String(255))
    estado_sunat: Mapped[str | None] = mapped_column(String(50))
    consultado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


# ═════════════════════════════════════════════════════════════════════
# 12. SolicitudValidacionCredencial — "Comprobar conexión" (zAlerta-10 B/D)
# ═════════════════════════════════════════════════════════════════════
class EstadoValidacion(str, enum.Enum):
    """Estado del ciclo flag→worker→resultado de validar credenciales SOL."""
    PENDIENTE = "pendiente"      # la web la encoló; el worker aún no la tomó
    COMPROBANDO = "comprobando"  # el worker la está procesando (login real)
    CONECTA = "conecta"          # login OK
    NO_CONECTA = "no_conecta"    # login falló (clave/usuario incorrectos)
    ERROR = "error"              # error técnico al intentar (no concluyente)


class SolicitudValidacionCredencial(Base):
    """Pedido de 'Comprobar conexión' del alta en 2 fases (zAlerta-10 B).

    La WEB es liviana (sin Playwright) → NO valida el login ella misma. Encola
    aquí la credencial CIFRADA (Fernet); el WORKER (que sí tiene Playwright)
    hace un login-only real, escribe el resultado y BORRA la clave cifrada.
    El front hace polling por id, igual que 'Actualizar ahora' (zAlerta-04).

    Multi-tenant (estudio_id). La clave NUNCA va en texto plano ni en logs.
    Esta tabla es efímera: las filas viejas pueden purgarse sin pérdida.
    """
    __tablename__ = "solicitudes_validacion_credencial"
    __table_args__ = (
        Index("ix_validacion_pendiente", "estado"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    # NULL en el alta pública del empresario (zAlerta-11a): la comprobación
    # ocurre ANTES de crear la organización. El polling es por id (UUID
    # inadivinable) y solo devuelve conecta=bool, sin exponer datos del tenant.
    estudio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=True, index=True)

    ruc: Mapped[str] = mapped_column(String(11), nullable=False)
    usuario_sol: Mapped[str] = mapped_column(String(50), nullable=False)
    # Clave SOL CIFRADA (Fernet). Se pone a NULL en cuanto el worker termina.
    clave_sol_cifrada: Mapped[str | None] = mapped_column(Text)

    # native_enum=False → se guarda como VARCHAR (nombre del miembro, p.ej.
    # "PENDIENTE"), igual que lo crea la migración SIN Alembic. Evita depender
    # de un TIPO nativo de Postgres que la migración no crea.
    estado: Mapped[EstadoValidacion] = mapped_column(
        Enum(EstadoValidacion, native_enum=False, length=20),
        default=EstadoValidacion.PENDIENTE, nullable=False)

    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)
    procesado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ═════════════════════════════════════════════════════════════════════
# 13. LeadActivacion — captura temprana del alta del empresario (zAlerta-11bb B)
# ═════════════════════════════════════════════════════════════════════
class LeadActivacion(Base):
    """RUC + WhatsApp capturados en /activar ANTES de terminar el alta.

    Si el empresario escribe su RUC y WhatsApp pero no completa la activación,
    igual queda un lead recuperable (no perder el contacto). Upsert por RUC.
    El WhatsApp se guarda normalizado (con 51 antepuesto). NO es multi-tenant:
    es un prospecto, todavía no tiene organización.
    """
    __tablename__ = "leads_activacion"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, nullable=False, index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(20))
    razon_social: Mapped[str | None] = mapped_column(String(255))
    # 'lead' mientras no termine; 'activado' cuando completa el alta.
    estado: Mapped[str] = mapped_column(String(20), default="lead", nullable=False)
    # CANDADO de precio (zAlerta-24): precio del mes en que se capturó este lead.
    # Se fija UNA vez; un lead recurrente NO lo sobreescribe (premia al primero).
    precio_congelado: Mapped[int | None] = mapped_column(Integer)
    precio_congelado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)
    actualizado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, onupdate=ahora_lima, nullable=False)


# ═════════════════════════════════════════════════════════════════════
# 14. Recordatorio — "Recuérdame esto" (zAlerta-13 P1)
# ═════════════════════════════════════════════════════════════════════
class ModoRecordatorio(str, enum.Enum):
    """Cómo insistir con una notificación hasta su vencimiento."""
    PROXIMOS_3 = "proximos_3"   # los próximos 3 días (desde que lo activó)
    ULTIMOS_3 = "ultimos_3"     # los últimos 3 días antes de vencer
    HASTA_VENCER = "hasta_vencer"  # todos los días hasta el vencimiento


class Recordatorio(Base):
    """Re-notificación de una notificación a un usuario hasta su vencimiento.

    El worker revisa los activos y reenvía un Web Push según el modo y la fecha
    de hoy vs `fecha_vencimiento`, máximo una vez al día (ultimo_envio_at) y solo
    en los horarios definidos. Multi-tenant (estudio_id). Único por
    (notificacion_id, usuario_id): re-activar actualiza el modo.
    """
    __tablename__ = "recordatorios"
    __table_args__ = (
        UniqueConstraint("notificacion_id", "usuario_id", name="uq_recordatorio"),
        Index("ix_recordatorio_activo", "activo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)
    notificacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notificaciones.id", ondelete="CASCADE"),
        nullable=False, index=True)
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False, index=True)

    modo: Mapped[ModoRecordatorio] = mapped_column(
        Enum(ModoRecordatorio, native_enum=False, length=20), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Copiada de la notificación para calcular sin re-consultarla.
    fecha_vencimiento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_envio_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


# ═════════════════════════════════════════════════════════════════════
# 15. Pago — pago Yape/Plin validado vía PagoOK que activó una suscripción
#     (zAlerta-14). Trazabilidad + base para la futura emisión (Facturalo).
# ═════════════════════════════════════════════════════════════════════
class Pago(Base):
    __tablename__ = "pagos"
    __table_args__ = (
        # Un pago de PagoOK activa UNA sola suscripción (idempotencia local;
        # el reclamo atómico en PagoOK es la garantía dura).
        UniqueConstraint("pagook_id", name="uq_pago_pagook"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

    pagook_id: Mapped[str] = mapped_column(String(64), nullable=False)
    codigo_operacion: Mapped[str | None] = mapped_column(String(64))
    metodo: Mapped[str | None] = mapped_column(String(20))     # yape / plin
    monto: Mapped[str | None] = mapped_column(String(20))      # "5.00"
    titular: Mapped[str | None] = mapped_column(String(160))
    recibido_en: Mapped[str | None] = mapped_column(String(40))  # del voucher
    # Vigencia que dejó este pago (para conciliación).
    vence_resultante: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


# ═════════════════════════════════════════════════════════════════════
# Deuda valorada (zAlerta-34) — el 2º PDF (documento real) y sus datos.
# El PDF íntegro va a GCS (gcs_key); el texto crudo queda para el parser
# (zAlerta-35). Aquí solo cabecera mínima + pdf_texto + gcs_key.
# ═════════════════════════════════════════════════════════════════════
class Tributo(Base):
    """Catálogo de códigos de tributo de SUNAT (sin TIM ni tasas)."""
    __tablename__ = "tributos"

    codigo: Mapped[str] = mapped_column(String(10), primary_key=True)
    descripcion: Mapped[str] = mapped_column(String(160), nullable=False)
    tipo: Mapped[str | None] = mapped_column(String(40))   # igv / renta / essalud / onp / multa…


class DocumentoValorado(Base, TimestampMixin):
    """Cabecera de un documento de deuda (Orden de Pago / Resolución de Multa /
    Coactiva / Determinación / Fraccionamiento). Multi-tenant por contribuyente."""
    __tablename__ = "documentos_valorados"
    __table_args__ = (
        # Un documento de deuda por notificación (dedup práctico al ingestar).
        UniqueConstraint("notificacion_id", name="uq_valorado_notif"),
        # Dedup lógico por nº de documento (cuando el parser lo complete).
        UniqueConstraint("contribuyente_id", "num_documento", "tipo_valorado",
                         name="uq_valorado_doc"),
        Index("ix_valorado_contrib", "contribuyente_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    contribuyente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="CASCADE"),
        nullable=False, index=True)
    notificacion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notificaciones.id", ondelete="CASCADE"),
        index=True)
    # Tenant directo (deriva de la notificación/contribuyente). Nullable por
    # prudencia en Flujo 1; el filtro fuerte es contribuyente_id.
    estudio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        index=True)

    tipo_valorado: Mapped[TipoValorado] = mapped_column(
        Enum(TipoValorado, native_enum=False, length=30), nullable=False)
    num_documento: Mapped[str | None] = mapped_column(String(60), index=True)

    # Cabecera (la llena el parser de zAlerta-35; aquí quedan NULL).
    fecha_emision: Mapped[datetime | None] = mapped_column(Date)
    fecha_notificacion: Mapped[datetime | None] = mapped_column(Date)
    dependencia: Mapped[str | None] = mapped_column(String(120))
    funcionario_emisor: Mapped[str | None] = mapped_column(String(160))
    infraccion_descripcion: Mapped[str | None] = mapped_column(Text)
    infraccion_base_legal: Mapped[str | None] = mapped_column(String(200))
    importe: Mapped[float | None] = mapped_column(Numeric(14, 2))
    interes: Mapped[float | None] = mapped_column(Numeric(14, 2))
    monto_total: Mapped[float | None] = mapped_column(Numeric(14, 2))
    num_resol_coactiva: Mapped[str | None] = mapped_column(String(60))
    plazo_reclamo_dias: Mapped[int | None] = mapped_column(Integer)

    # Fuente de verdad: PDF íntegro en GCS + texto crudo para el parser.
    pdf_texto: Mapped[str | None] = mapped_column(Text)
    gcs_key: Mapped[str | None] = mapped_column(String(300))

    parseado_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(20))
    parseado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    detalles: Mapped[list["DetalleValorado"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan")


class DetalleValorado(Base):
    """Líneas (detalle) de un documento valorado. Las llena el parser (zAlerta-35)."""
    __tablename__ = "detalles_valorados"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    documento_valorado_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documentos_valorados.id", ondelete="CASCADE"),
        nullable=False, index=True)

    periodo: Mapped[str | None] = mapped_column(String(20))
    cod_tributo: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("tributos.codigo"))
    formulario: Mapped[str | None] = mapped_column(String(20))
    num_declaracion: Mapped[str | None] = mapped_column(String(40))
    base_referencia: Mapped[str | None] = mapped_column(String(120))
    tasa_pct: Mapped[float | None] = mapped_column(Numeric(7, 4))
    cod_multa: Mapped[str | None] = mapped_column(String(20))
    monto_insoluto: Mapped[float | None] = mapped_column(Numeric(14, 2))
    interes_linea: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_linea: Mapped[float | None] = mapped_column(Numeric(14, 2))
    fecha_infraccion: Mapped[datetime | None] = mapped_column(Date)

    documento: Mapped["DocumentoValorado"] = relationship(back_populates="detalles")
    tributo: Mapped["Tributo | None"] = relationship()


# ═════════════════════════════════════════════════════════════════════
# Blog de RTFs (zAlerta-40) — contenido público para SEO. Solo web.
# ═════════════════════════════════════════════════════════════════════
class EstadoArticulo(str, enum.Enum):
    BORRADOR = "borrador"
    PUBLICADO = "publicado"


class ArticuloBlog(Base, TimestampMixin):
    """Artículo del blog: una RTF resumida en lenguaje de empresario + su PDF."""
    __tablename__ = "articulos_blog"
    __table_args__ = (
        Index("ix_articulo_estado", "estado"),
        Index("ix_articulo_fecha_pub", "fecha_publicacion"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    titulo: Mapped[str] = mapped_column(String(300), nullable=False)

    etiqueta_area: Mapped[str | None] = mapped_column(String(40))   # Internos / Aduaneros
    tema: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(80))
    numero_rtf: Mapped[str | None] = mapped_column(String(80))

    # Bloques de contenido (plantilla).
    resumen_caso: Mapped[str | None] = mapped_column(Text)
    decision_tribunal: Mapped[str | None] = mapped_column(Text)
    por_que_importa: Mapped[str | None] = mapped_column(Text)
    preguntas_abiertas: Mapped[str | None] = mapped_column(Text)
    cierre: Mapped[str | None] = mapped_column(Text)

    pdf_gcs_key: Mapped[str | None] = mapped_column(String(300))

    # SEO
    meta_title: Mapped[str | None] = mapped_column(String(200))
    meta_description: Mapped[str | None] = mapped_column(String(320))
    og_image_gcs_key: Mapped[str | None] = mapped_column(String(300))
    keywords: Mapped[str | None] = mapped_column(Text)

    estado: Mapped[EstadoArticulo] = mapped_column(
        Enum(EstadoArticulo, native_enum=False, length=20),
        default=EstadoArticulo.BORRADOR, nullable=False)
    fecha_publicacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ═════════════════════════════════════════════════════════════════════
# ACCESO INSTITUCIONAL — FASE 1 (zAlerta-54)
# ─────────────────────────────────────────────────────────────────────
# Separa IDENTIDAD (persona que se autentica, un DNI = una fila) de sus
# ACCESOS (permiso persona → buzón, con cargo y vigencia). Reemplazará la
# mitad "identidad + pertenencia" de Usuario, pero en esta fase las tablas
# quedan VACÍAS y SIN USO: nadie las lee ni escribe todavía. El sistema se
# comporta idéntico. Backfill = Fase 2; dual-read = Fase 3; cutover = Fase 4.
# Diseño completo en zAlerta-53-RESULTADO-DisenoAccesoInstitucional.md.
# ═════════════════════════════════════════════════════════════════════
class CargoInstitucional(str, enum.Enum):
    """Título del acceso dentro de una institución/organización (zAlerta-54).
    Se guarda como VARCHAR (native_enum=False → nombre del miembro). OTRO usa
    el campo libre `cargo_libre`."""
    DECANO = "decano"
    DIRECTOR = "director"
    CONTADOR = "contador"
    ADMINISTRADOR = "administrador"
    ASISTENTE = "asistente"
    DUENO = "dueno"
    OTRO = "otro"


class RolSistema(str, enum.Enum):
    """Rol de SISTEMA de una persona (zAlerta-58). Transversal a los accesos:
    SOPORTE_GLOBAL ve TODOS los buzones (solo lectura, auditable). NULL = normal."""
    SOPORTE_GLOBAL = "soporte_global"


class Persona(Base, TimestampMixin):
    """Identidad que se autentica. Login SOLO por DNI (zAlerta-58, decisión
    permanente): un DNI = una fila. WhatsApp es solo contacto, nunca identidad.
    La credencial (Argon2) se copia/crea aquí en el backfill (zAlerta-58)."""
    __tablename__ = "personas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    dni: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    nombre_completo: Mapped[str | None] = mapped_column(String(255))
    whatsapp: Mapped[str | None] = mapped_column(String(20))   # solo contacto
    correo: Mapped[str | None] = mapped_column(String(255))
    # Argon2 (mismo hashing que el resto).
    clave_hash: Mapped[str | None] = mapped_column(String(255))
    # Clave inicial = DNI → forzar cambio en el primer login (personas nuevas).
    debe_cambiar_clave: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Rol de sistema (SOPORTE_GLOBAL = ve todo, solo lectura). NULL = normal.
    rol_sistema: Mapped[RolSistema | None] = mapped_column(
        Enum(RolSistema, native_enum=False, length=20), nullable=True)

    accesos: Mapped[list["Acceso"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan")


class Acceso(Base, TimestampMixin):
    """Permiso persona → buzón, con cargo y vigencia (zAlerta-54).
    Destino DUAL, exactamente uno: `estudio_id` (acceso a TODA la organización)
    o `contribuyente_id` (acceso a UN solo buzón — el caso asistente). Un acceso
    con `vigencia_fin < hoy` deja de dar visibilidad (declarativo, sin job).
    VACÍA y SIN USO en Fase 1."""
    __tablename__ = "accesos"
    __table_args__ = (
        # Destino dual: exactamente uno de estudio_id / contribuyente_id.
        CheckConstraint(
            "(estudio_id IS NOT NULL) <> (contribuyente_id IS NOT NULL)",
            name="ck_acceso_destino"),
        # cargo_libre solo tiene sentido cuando el cargo es OTRO.
        CheckConstraint(
            "cargo_libre IS NULL OR cargo = 'OTRO'",
            name="ck_acceso_cargo_libre"),
        Index("ix_acceso_persona", "persona_id"),
        Index("ix_acceso_estudio", "estudio_id"),
        Index("ix_acceso_contribuyente", "contribuyente_id"),
        Index("ix_acceso_vigencia_fin", "vigencia_fin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False)

    # Destino (exactamente uno, por el CHECK ck_acceso_destino).
    estudio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=True)
    contribuyente_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="CASCADE"),
        nullable=True)

    # Permisos (reusa RolUsuario) + título institucional (cargo).
    rol: Mapped[RolUsuario] = mapped_column(
        Enum(RolUsuario, native_enum=False, length=20),
        default=RolUsuario.CONTADOR, nullable=False)
    cargo: Mapped[CargoInstitucional | None] = mapped_column(
        Enum(CargoInstitucional, native_enum=False, length=20), nullable=True)
    cargo_libre: Mapped[str | None] = mapped_column(String(60))

    # Vigencia declarativa: fin NULL = sin fin; fin < hoy = no da visibilidad.
    vigencia_inicio: Mapped[date] = mapped_column(
        Date, default=lambda: ahora_lima().date(), nullable=False)
    vigencia_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    es_solo_lectura: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    persona: Mapped["Persona"] = relationship(back_populates="accesos")
    # Relaciones one-directional al destino: NO tocan los modelos existentes.
    estudio: Mapped["EstudioContable | None"] = relationship(
        foreign_keys=[estudio_id])
    contribuyente: Mapped["Contribuyente | None"] = relationship(
        foreign_keys=[contribuyente_id])


class AuditoriaSoporte(Base):
    """Registro de acceso de un SOPORTE_GLOBAL a un buzón ajeno (zAlerta-58).
    Estructura ahora; el INSERT se cablea en Fase 3 cuando el código lea accesos.
    El buzón se identifica por estudio_id y/o contribuyente_id."""
    __tablename__ = "auditoria_soporte"
    __table_args__ = (
        Index("ix_auditoria_persona", "persona_id"),
        Index("ix_auditoria_creado", "creado_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False)
    estudio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="SET NULL"),
        nullable=True)
    contribuyente_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="SET NULL"),
        nullable=True)
    accion: Mapped[str] = mapped_column(String(20), default="VER", nullable=False)
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


class LecturaNotificacion(Base):
    """Estado de lectura POR PERSONA de una notificación (zAlerta-61, Capa 1).
    Una fila = esta persona vio esta notif. Su ausencia = "Nuevo" para ella.
    Reemplaza a `notificaciones.leida` como fuente de verdad para personas
    (el flag global queda como fallback del login viejo). El índice por
    notificacion_id habilita la Capa 2 ("¿quiénes leyeron esta notif?")."""
    __tablename__ = "lectura_notificacion"
    __table_args__ = (
        UniqueConstraint("persona_id", "notificacion_id",
                         name="uq_lectura_persona_notif"),
        Index("ix_lectura_notif", "notificacion_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False)
    notificacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notificaciones.id", ondelete="CASCADE"),
        nullable=False)
    leida_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


class PushEnviado(Base):
    """Registro de push ENVIADO por persona (zAlerta-67). Una fila = a esta
    persona ya se le avisó de esta notif → no re-enviar a ELLA, pero SÍ a cada
    otra persona del buzón. Reemplaza al flag global notificado_push como verdad
    por-persona (el flag sigue como cierre del ciclo/legacy). Alimenta la Capa 3
    ("avisar al que no vio") con el índice por notificacion_id."""
    __tablename__ = "push_enviado"
    __table_args__ = (
        UniqueConstraint("persona_id", "notificacion_id",
                         name="uq_push_enviado_persona_notif"),
        Index("ix_push_enviado_notif", "notificacion_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False)
    notificacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notificaciones.id", ondelete="CASCADE"),
        nullable=False)
    enviado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)


class BarridoMetrica(Base):
    """Tablero de riesgo de ban (zAlerta-83): una fila por barrido a SUNAT.
    Registra peticiones, duración, PDFs bajados y señales de límite para decidir
    con DATOS si es seguro escalar la descarga (no a ciegas)."""
    __tablename__ = "barrido_metricas"
    __table_args__ = (
        Index("ix_barrido_contrib_at", "contribuyente_id", "creado_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    contribuyente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribuyentes.id", ondelete="CASCADE"),
        nullable=False)
    estudio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="SET NULL"))
    modo: Mapped[str | None] = mapped_column(String(20))          # full | incremental
    peticiones: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duracion_seg: Mapped[int | None] = mapped_column(Integer)
    docs_procesados: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pdfs_descargados: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    senales_limite: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    limite_alcanzado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exito: Mapped[bool | None] = mapped_column(Boolean)
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_lima, nullable=False)