"""
__init__.py — Índice central de todos los modelos de alerta.pe / notificado.pro.

Importar desde aquí para que Alembic y FastAPI descubran todos los modelos:
    from app.models import Base, Usuario, Tenant, ...

Todos los modelos se importan explícitamente para que SQLAlchemy registre
las tablas en Base.metadata al momento del import.
"""

# Base y mixins
from app.models.base import Base, TimestampMixin, SoftDeleteMixin

# Tenants
from app.models.tenants import Tenant, TipoServicio, PlanTenant

# Usuarios
from app.models.usuarios import (
    Usuario,
    UsuarioTenant,
    WebAuthnCredential,
    RecuperacionClave,
    RolUsuario,
)

# Empresas
from app.models.empresas import EmpresaCliente

# Pagos
from app.models.pagos import Pago, EstadoPago, CanalPago

# Documentos (productos no deducibles)
from app.models.documentos import ProductoNoDeducible

# Comprobantes
from app.models.comprobantes import (
    Comprobante,
    DetalleComprobante,
    TipoComprobante,
    EstadoComprobante,
    ClasificadoPor,
)

# Acumulados
from app.models.acumulados import (
    AcumBancos,
    AcumSIRE,
    AcumMensual,
    TipoRegistroSIRE,
)

# Deudas (notificado.pro)
from app.models.deudas import Deuda, DeudaPago, CicloDeuda, EstadoDeuda, NivelEscalamiento

# Notificaciones
from app.models.notificaciones import (
    Notificacion,
    CanalNotificacion,
    EstadoNotificacion,
    NivelAlertaNotificacion,
)

# Alertas
from app.models.alertas import Alerta, OrigenAlerta, EstadoAlerta

# Correos
from app.models.correos import CorreoCapturado

# Configuración
from app.models.configuracion import (
    ConfigUsuario,
    ConfigEmpresa,
    TemaUI,
    FuenteSize,
    CanalPreferido,
    TonoIA,
    VelocidadVoz,
    RegimenTributario,
)

# Voz
from app.models.voz import ConsultaVoz

# Auditoría
from app.models.auditoria import RegistroAuditoria

# Notificaciones manuales (sesión 5)
from app.models.notif_manual import NotifManual

# Contabilidad (sesión 5)
from app.models.contabilidad import (
    PlanContable, AsientoContable, LineaAsiento,
    TipoCambioHistorico, CronogramaSunat, SeguimientoCorreccion,
)

# Portal público (sesión 6)
from app.models.portal import EnvioPortal, EstadoSistema

# RendiPe — Rendición de Gastos (sesión 7a)
from app.models.rendipe import (
    InstitucionConfig, Servidor, Comision,
    GastoComision, InformeComision, SaldoComision,
)

__all__ = [
    # Base
    "Base", "TimestampMixin", "SoftDeleteMixin",
    # Tenants
    "Tenant", "TipoServicio", "PlanTenant",
    # Usuarios
    "Usuario", "UsuarioTenant", "WebAuthnCredential", "RecuperacionClave", "RolUsuario",
    # Empresas
    "EmpresaCliente",
    # Pagos
    "Pago", "EstadoPago", "CanalPago",
    # Documentos
    "ProductoNoDeducible",
    # Comprobantes
    "Comprobante", "DetalleComprobante", "TipoComprobante", "EstadoComprobante", "ClasificadoPor",
    # Acumulados
    "AcumBancos", "AcumSIRE", "AcumMensual", "TipoRegistroSIRE",
    # Deudas
    "Deuda", "DeudaPago", "CicloDeuda", "EstadoDeuda", "NivelEscalamiento",
    # Notificaciones
    "Notificacion", "CanalNotificacion", "EstadoNotificacion", "NivelAlertaNotificacion",
    # Alertas
    "Alerta", "OrigenAlerta", "EstadoAlerta",
    # Correos
    "CorreoCapturado",
    # Configuración
    "ConfigUsuario", "ConfigEmpresa", "TemaUI", "FuenteSize",
    "CanalPreferido", "TonoIA", "VelocidadVoz", "RegimenTributario",
    # Voz
    "ConsultaVoz",
    # Auditoría
    "RegistroAuditoria",
]
