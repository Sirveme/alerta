"""
routers/empresas.py — CRUD de empresas cliente y dashboard rápido.

Decisiones técnicas:
- mis-empresas: lista empresas del tenant del usuario con estado visual
  (al_dia, pendientes, alertas) calculado en base a alertas y pagos pendientes.
- resumen: dashboard rápido con métricas del mes actual.
- Creación/actualización valida que el usuario pertenezca al tenant.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.core.security import encrypt_sensitive
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.alertas import Alerta, EstadoAlerta
from app.models.pagos import Pago, EstadoPago

router = APIRouter(prefix="/empresas", tags=["empresas"])


# --- Schemas ---

class EmpresaCreate(BaseModel):
    ruc: str
    razon_social: str
    nombre_comercial: Optional[str] = None
    cuentas_bancarias: Optional[list] = None
    numeros_yape_plin: Optional[list] = None
    email_notificaciones_bancarias: Optional[str] = None

    @field_validator("ruc")
    @classmethod
    def validate_ruc(cls, v: str) -> str:
        if len(v) != 11 or not v.isdigit():
            raise ValueError("RUC debe ser exactamente 11 dígitos")
        return v


class EmpresaUpdate(BaseModel):
    razon_social: Optional[str] = None
    nombre_comercial: Optional[str] = None
    cuentas_bancarias: Optional[list] = None
    numeros_yape_plin: Optional[list] = None
    clave_sol_usuario: Optional[str] = None
    clave_sol_password: Optional[str] = None
    email_notificaciones_bancarias: Optional[str] = None
    notas: Optional[str] = None


# --- Endpoints ---

@router.get("/mis-empresas")
def listar_empresas(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lista empresas del tenant del usuario con estado visual.
    Estado = al_dia | pendientes | alertas basado en alertas activas y pagos pendientes.
    """
    payload = request.state.token_payload
    tenant_id = payload.get("tenant_id")

    empresas = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.tenant_id == tenant_id,
            EmpresaCliente.deleted_at == None,
        ).order_by(EmpresaCliente.razon_social)
    ).scalars().all()

    resultado = []
    for emp in empresas:
        # Contar alertas activas
        alertas_activas = db.execute(
            select(func.count(Alerta.id)).where(
                Alerta.empresa_id == emp.id,
                Alerta.estado == EstadoAlerta.ACTIVA,
                Alerta.deleted_at == None,
            )
        ).scalar() or 0

        # Contar pagos pendientes de cruce
        pagos_pendientes = db.execute(
            select(func.count(Pago.id)).where(
                Pago.empresa_id == emp.id,
                Pago.estado == EstadoPago.PENDIENTE_CRUCE,
                Pago.deleted_at == None,
            )
        ).scalar() or 0

        # Determinar estado visual
        if alertas_activas > 0:
            estado = "alertas"
        elif pagos_pendientes > 0:
            estado = "pendientes"
        else:
            estado = "al_dia"

        resultado.append({
            "id": emp.id,
            "ruc": emp.ruc,
            "razon_social": emp.razon_social,
            "nombre_comercial": emp.nombre_comercial,
            "estado": estado,
            "alertas_activas": alertas_activas,
            "pagos_pendientes": pagos_pendientes,
        })

    return {"empresas": resultado}


@router.post("/")
def crear_empresa(
    body: EmpresaCreate,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea una nueva empresa cliente en el tenant del usuario."""
    payload = request.state.token_payload
    tenant_id = payload.get("tenant_id")

    # Verificar que no exista ya en el tenant
    existente = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.tenant_id == tenant_id,
            EmpresaCliente.ruc == body.ruc,
            EmpresaCliente.deleted_at == None,
        )
    ).scalar_one_or_none()

    if existente:
        raise HTTPException(status_code=409, detail=f"Ya existe una empresa con RUC {body.ruc} en tu tenant")

    empresa = EmpresaCliente(
        tenant_id=tenant_id,
        ruc=body.ruc,
        razon_social=body.razon_social,
        nombre_comercial=body.nombre_comercial,
        cuentas_bancarias=body.cuentas_bancarias or [],
        numeros_yape_plin=body.numeros_yape_plin or [],
        email_notificaciones_bancarias=body.email_notificaciones_bancarias,
    )
    db.add(empresa)
    db.commit()
    db.refresh(empresa)

    return {"id": empresa.id, "ruc": empresa.ruc, "razon_social": empresa.razon_social}


@router.put("/{empresa_id}")
def actualizar_empresa(
    empresa_id: int,
    body: EmpresaUpdate,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza datos de una empresa. Encripta credenciales SOL si se envían."""
    payload = request.state.token_payload
    tenant_id = payload.get("tenant_id")

    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.id == empresa_id,
            EmpresaCliente.tenant_id == tenant_id,
            EmpresaCliente.deleted_at == None,
        )
    ).scalar_one_or_none()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    update_data = body.model_dump(exclude_unset=True)

    # Encriptar credenciales SOL si se proporcionan
    if "clave_sol_usuario" in update_data and update_data["clave_sol_usuario"]:
        update_data["clave_sol_usuario"] = encrypt_sensitive(update_data["clave_sol_usuario"])
    if "clave_sol_password" in update_data and update_data["clave_sol_password"]:
        update_data["clave_sol_password"] = encrypt_sensitive(update_data["clave_sol_password"])

    for key, value in update_data.items():
        setattr(empresa, key, value)

    db.commit()
    return {"detail": "Empresa actualizada", "id": empresa_id}


@router.get("/{empresa_id}/resumen")
def resumen_empresa(
    empresa_id: int,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard rápido: métricas del mes actual de la empresa."""
    payload = request.state.token_payload
    tenant_id = payload.get("tenant_id")

    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.id == empresa_id,
            EmpresaCliente.tenant_id == tenant_id,
            EmpresaCliente.deleted_at == None,
        )
    ).scalar_one_or_none()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    now = datetime.now(timezone.utc)

    # Total cobrado este mes (pagos cruzados)
    total_cobrado = db.execute(
        select(func.coalesce(func.sum(Pago.monto), 0)).where(
            Pago.empresa_id == empresa_id,
            Pago.estado == EstadoPago.CRUZADO,
            func.extract("month", Pago.fecha_pago) == now.month,
            func.extract("year", Pago.fecha_pago) == now.year,
            Pago.deleted_at == None,
        )
    ).scalar()

    # Pagos pendientes de cruce
    total_pendiente = db.execute(
        select(func.coalesce(func.sum(Pago.monto), 0)).where(
            Pago.empresa_id == empresa_id,
            Pago.estado == EstadoPago.PENDIENTE_CRUCE,
            Pago.deleted_at == None,
        )
    ).scalar()

    # Alertas activas
    alertas_activas = db.execute(
        select(func.count(Alerta.id)).where(
            Alerta.empresa_id == empresa_id,
            Alerta.estado == EstadoAlerta.ACTIVA,
            Alerta.deleted_at == None,
        )
    ).scalar()

    return {
        "empresa_id": empresa_id,
        "razon_social": empresa.razon_social,
        "mes": now.month,
        "anio": now.year,
        "total_cobrado": float(total_cobrado),
        "total_pendiente": float(total_pendiente),
        "alertas_activas": alertas_activas,
    }
