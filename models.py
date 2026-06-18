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
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index, func,
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
    "aviso": "Avisos",
    "otro": "Otros",
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
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=nuevo_uuid)
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False, index=True)

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
    estudio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estudios_contables.id", ondelete="CASCADE"),
        nullable=False, index=True)

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