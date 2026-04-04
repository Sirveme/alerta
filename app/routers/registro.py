"""
routers/registro.py — Flujo de registro de nuevos tenants via invitacion.

Endpoints:
  GET  /registro?token=XXX  — Mostrar formulario de registro
  POST /registro             — Procesar registro y crear tenant + usuario
"""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import hash_password, create_access_token
from app.models.tenants import Tenant, TipoServicio, PlanTenant, Invitacion
from app.models.usuarios import Usuario, UsuarioTenant, RolUsuario
from app.models.configuracion import ConfigUsuario
from app.models.auditoria import RegistroAuditoria

router = APIRouter(tags=["registro"])
templates = Jinja2Templates(directory="app/templates")


# ── Schemas ───────────────────────────────────────────────────

class RegistroRequest(BaseModel):
    token: str
    dni: str
    nombres: str
    apellidos: str
    whatsapp: str = ""
    clave: str
    nombre_tenant: str
    ruc_tenant: str = ""

    @field_validator("dni")
    @classmethod
    def validate_dni(cls, v: str) -> str:
        if not re.match(r"^\d{8}$", v):
            raise ValueError("DNI debe ser exactamente 8 digitos")
        return v

    @field_validator("clave")
    @classmethod
    def validate_clave(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La clave debe tener minimo 8 caracteres")
        return v

    @field_validator("ruc_tenant")
    @classmethod
    def validate_ruc(cls, v: str) -> str:
        if v and not re.match(r"^\d{11}$", v):
            raise ValueError("RUC debe ser exactamente 11 digitos")
        return v


# ── Mapa tipo_tenant -> TipoServicio ─────────────────────────
_TIPO_MAP = {
    "estudio_contable": TipoServicio.ALERTA,
    "contador_independiente": TipoServicio.ALERTA,
    "empresa": TipoServicio.ALERTA,
    "institucion_publica": TipoServicio.AMBOS,
    "academia": TipoServicio.NOTIFICADO,
    "gimnasio": TipoServicio.NOTIFICADO,
    "condominio": TipoServicio.NOTIFICADO,
}

_PLAN_MAP = {
    "gratis": PlanTenant.GRATIS,
    "basico": PlanTenant.BASICO,
    "pro": PlanTenant.PRO,
    "enterprise": PlanTenant.ENTERPRISE,
}


# ── GET /registro ─────────────────────────────────────────────

@router.get("/registro", response_class=HTMLResponse)
def registro_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    """Muestra formulario de registro si el token es valido."""
    if not token:
        return templates.TemplateResponse("registro/index.html", {
            "request": request,
            "error": "Token de invitacion requerido",
            "invitacion": None,
        })

    inv = db.execute(
        select(Invitacion).where(Invitacion.token == token)
    ).scalar_one_or_none()

    if not inv:
        return templates.TemplateResponse("registro/index.html", {
            "request": request,
            "error": "Token de invitacion invalido",
            "invitacion": None,
        })

    ahora = datetime.now(timezone.utc)
    expira = inv.expira_en.replace(tzinfo=timezone.utc) if inv.expira_en and inv.expira_en.tzinfo is None else inv.expira_en
    if expira and expira < ahora:
        return templates.TemplateResponse("registro/index.html", {
            "request": request,
            "error": "Esta invitacion ha expirado",
            "invitacion": None,
        })

    if inv.usado_en:
        return templates.TemplateResponse("registro/index.html", {
            "request": request,
            "error": "Esta invitacion ya fue utilizada",
            "invitacion": None,
        })

    return templates.TemplateResponse("registro/index.html", {
        "request": request,
        "error": None,
        "invitacion": {
            "token": inv.token,
            "tipo_tenant": inv.tipo_tenant,
            "nombre_contacto": inv.nombre_contacto,
        },
    })


# ── POST /registro ────────────────────────────────────────────

@router.post("/registro")
def registrar(
    body: RegistroRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Procesa registro: valida token, crea tenant + usuario admin, auto-login."""

    # Validar token
    inv = db.execute(
        select(Invitacion).where(Invitacion.token == body.token)
    ).scalar_one_or_none()

    if not inv:
        raise HTTPException(status_code=400, detail="Token de invitacion invalido")

    ahora = datetime.now(timezone.utc)
    expira = inv.expira_en.replace(tzinfo=timezone.utc) if inv.expira_en and inv.expira_en.tzinfo is None else inv.expira_en
    if expira and expira < ahora:
        raise HTTPException(status_code=400, detail="Token de invitacion expirado")

    if inv.usado_en:
        raise HTTPException(status_code=400, detail="Token ya fue utilizado")

    # Validar DNI no existe
    existing = db.execute(
        select(Usuario).where(Usuario.dni == body.dni)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese DNI")

    # Validar RUC no existe (si se proporciona)
    if body.ruc_tenant:
        existing_tenant = db.execute(
            select(Tenant).where(Tenant.ruc == body.ruc_tenant, Tenant.deleted_at == None)
        ).scalar_one_or_none()
        if existing_tenant:
            raise HTTPException(status_code=400, detail="Ya existe un tenant con ese RUC")

    # Crear Tenant
    tipo_servicio = _TIPO_MAP.get(inv.tipo_tenant, TipoServicio.ALERTA)
    plan = _PLAN_MAP.get(inv.plan or "gratis", PlanTenant.GRATIS)

    tenant = Tenant(
        nombre=body.nombre_tenant,
        ruc=body.ruc_tenant or None,
        tipo_servicio=tipo_servicio,
        plan=plan,
        activo=True,
        es_produccion=False,
    )
    db.add(tenant)
    db.flush()

    # Crear Usuario admin
    usuario = Usuario(
        dni=body.dni,
        nombres=body.nombres,
        apellidos=body.apellidos,
        password_hash=hash_password(body.clave),
        telefono=body.whatsapp or None,
        activo=True,
        ultimo_acceso=ahora,
    )
    db.add(usuario)
    db.flush()

    # Vincular usuario al tenant como admin
    ut = UsuarioTenant(
        usuario_id=usuario.id,
        tenant_id=tenant.id,
        rol=RolUsuario.ADMIN,
        activo=True,
    )
    db.add(ut)

    # Crear ConfigUsuario
    config = ConfigUsuario(usuario_id=usuario.id)
    db.add(config)

    # Marcar invitacion como usada
    inv.usado_en = ahora
    inv.tenant_creado_id = tenant.id
    db.flush()

    # Auditoria
    audit = RegistroAuditoria(
        usuario_id=usuario.id,
        accion="registro_tenant",
        tabla="tenants",
        registro_id=str(tenant.id),
        valor_nuevo={
            "tenant_nombre": tenant.nombre,
            "usuario_dni": usuario.dni,
            "invitacion_id": inv.id,
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(audit)

    db.commit()

    # Auto-login: generar JWT
    token_data = {
        "sub": str(usuario.id),
        "tenant_id": str(tenant.id),
        "empresa_activa_id": None,
        "rol": RolUsuario.ADMIN.value,
        "tema": "semi",
        "fuente_size": "md",
        "nombres": usuario.nombres,
        "apellidos": usuario.apellidos,
    }
    token = create_access_token(token_data)

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=28800,
        path="/",
    )

    return {
        "exito": True,
        "access_token": token,
        "redirect": "/dashboard",
        "mensaje": f"Bienvenido {usuario.nombres}. Tu cuenta ha sido creada.",
    }
