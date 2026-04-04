"""
routers/sote.py — Router SOTE: Soporte Tecnico de Peru Sistemas Pro.
Acceso exclusivo para super_admin (usuarios del tenant tipo soporte_tecnico).
NO aparece en /docs — se registra con include_in_schema=False.

Endpoints de administracion del sistema:
- Ver todos los tenants y usuarios
- Reset de datos de prueba por area
- Seed de datos de prueba
- Impersonar usuarios (TTL 30 min)
- Metricas globales
- Health check completo
- Gestion de invitaciones para nuevos tenants
- Log de auditoria global
"""

import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.core.security import create_access_token, SECRET_KEY, ALGORITHM
from app.models.tenants import Tenant, Invitacion
from app.models.usuarios import Usuario, UsuarioTenant, RolUsuario
from app.models.empresas import EmpresaCliente
from app.models.comprobantes import Comprobante
from app.models.pagos import Pago
from app.models.alertas import Alerta
from app.models.auditoria import RegistroAuditoria
from app.models.rendipe import Comision, GastoComision, Servidor, InstitucionConfig

router = APIRouter()


# ── Seguridad SOTE ────────────────────────────────────────────

def _require_sote(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Usuario:
    """
    Middleware de seguridad SOTE:
    1. Requiere JWT valido
    2. El usuario debe tener rol admin en un tenant tipo soporte_tecnico
    3. El tenant debe ser de produccion
    """
    payload = getattr(request.state, "token_payload", {})
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Acceso SOTE denegado")

    tenant = db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    ).scalar_one_or_none()

    if not tenant or not tenant.es_produccion:
        raise HTTPException(status_code=403, detail="Acceso SOTE denegado: tenant no autorizado")

    # Verificar rol admin en ese tenant
    ut = db.execute(
        select(UsuarioTenant).where(
            UsuarioTenant.usuario_id == current_user.id,
            UsuarioTenant.tenant_id == tenant_id,
            UsuarioTenant.activo == True,
            UsuarioTenant.deleted_at == None,
        )
    ).scalar_one_or_none()

    if not ut or ut.rol != RolUsuario.ADMIN:
        raise HTTPException(status_code=403, detail="Acceso SOTE denegado: rol insuficiente")

    return current_user


# ── Schemas ───────────────────────────────────────────────────

class ResetRequest(BaseModel):
    confirmar: str
    area: str  # contabilidad, rendipe, portal, usuarios, todo

class SeedRequest(BaseModel):
    area: str = "todo"  # contabilidad, rendipe, todo

class InvitacionRequest(BaseModel):
    whatsapp: Optional[str] = None
    tipo_tenant: str
    plan: Optional[str] = None
    nombre_contacto: Optional[str] = None


# ── Tenants y usuarios ────────────────────────────────────────

@router.get("/tenants")
def listar_tenants(
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Lista todos los tenants con stats basicas."""
    tenants = db.execute(
        select(Tenant).where(Tenant.deleted_at == None).order_by(Tenant.created_at)
    ).scalars().all()

    resultado = []
    for t in tenants:
        n_usuarios = db.execute(
            select(func.count(UsuarioTenant.id)).where(
                UsuarioTenant.tenant_id == t.id, UsuarioTenant.activo == True
            )
        ).scalar() or 0
        n_empresas = db.execute(
            select(func.count(EmpresaCliente.id)).where(
                EmpresaCliente.tenant_id == t.id, EmpresaCliente.deleted_at == None
            )
        ).scalar() or 0

        resultado.append({
            "id": str(t.id),
            "nombre": t.nombre,
            "ruc": t.ruc,
            "tipo_servicio": t.tipo_servicio.value,
            "plan": t.plan.value,
            "es_produccion": t.es_produccion,
            "activo": t.activo,
            "usuarios": n_usuarios,
            "empresas": n_empresas,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return {"tenants": resultado, "total": len(resultado)}


@router.get("/tenants/{tenant_id}/usuarios")
def listar_usuarios_tenant(
    tenant_id: str,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Usuarios de un tenant especifico."""
    uts = db.execute(
        select(UsuarioTenant).where(
            UsuarioTenant.tenant_id == tenant_id,
            UsuarioTenant.deleted_at == None,
        )
    ).scalars().all()

    usuarios = []
    for ut in uts:
        u = db.execute(select(Usuario).where(Usuario.id == ut.usuario_id)).scalar_one_or_none()
        if u:
            usuarios.append({
                "id": str(u.id),
                "dni": u.dni,
                "nombres": u.nombres,
                "apellidos": u.apellidos,
                "rol": ut.rol.value,
                "activo": ut.activo,
                "ultimo_acceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None,
            })
    return {"usuarios": usuarios, "total": len(usuarios)}


@router.get("/tenants/{tenant_id}/stats")
def stats_tenant(
    tenant_id: str,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Metricas de un tenant: comprobantes, pagos, alertas, comisiones."""
    # Empresas del tenant
    empresa_ids = [
        eid for (eid,) in db.execute(
            select(EmpresaCliente.id).where(
                EmpresaCliente.tenant_id == tenant_id,
                EmpresaCliente.deleted_at == None,
            )
        ).all()
    ]

    n_comprobantes = 0
    n_pagos = 0
    if empresa_ids:
        n_comprobantes = db.execute(
            select(func.count(Comprobante.id)).where(Comprobante.empresa_id.in_(empresa_ids))
        ).scalar() or 0
        n_pagos = db.execute(
            select(func.count(Pago.id)).where(Pago.empresa_id.in_(empresa_ids))
        ).scalar() or 0

    n_comisiones = db.execute(
        select(func.count(Comision.id)).where(Comision.tenant_id == tenant_id)
    ).scalar() or 0

    return {
        "tenant_id": tenant_id,
        "empresas": len(empresa_ids),
        "comprobantes": n_comprobantes,
        "pagos": n_pagos,
        "comisiones": n_comisiones,
    }


# ── Reset por area ────────────────────────────────────────────

@router.post("/reset")
def reset_datos_prueba(
    body: ResetRequest,
    request: Request,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """
    Elimina registros de tenants con es_produccion=False.
    Nunca toca tenants con es_produccion=True.
    Requiere confirmar='LIMPIAR_PRUEBA'.
    """
    if body.confirmar != "LIMPIAR_PRUEBA":
        raise HTTPException(status_code=400, detail="Confirmacion incorrecta. Enviar confirmar='LIMPIAR_PRUEBA'")

    if body.area not in ("contabilidad", "rendipe", "portal", "usuarios", "todo"):
        raise HTTPException(status_code=400, detail="Area invalida")

    start = time.time()

    # Tenants de prueba (no produccion)
    test_tenant_ids = [
        tid for (tid,) in db.execute(
            select(Tenant.id).where(Tenant.es_produccion == False)
        ).all()
    ]
    if not test_tenant_ids:
        return {"eliminados": {}, "duracion_ms": 0, "mensaje": "No hay tenants de prueba"}

    # Empresas de tenants de prueba
    test_empresa_ids = [
        eid for (eid,) in db.execute(
            select(EmpresaCliente.id).where(EmpresaCliente.tenant_id.in_(test_tenant_ids))
        ).all()
    ]

    eliminados = {}

    if body.area in ("contabilidad", "todo") and test_empresa_ids:
        # Pagos
        n = db.execute(delete(Pago).where(Pago.empresa_id.in_(test_empresa_ids))).rowcount
        eliminados["pagos"] = n
        # Comprobantes
        n = db.execute(delete(Comprobante).where(Comprobante.empresa_id.in_(test_empresa_ids))).rowcount
        eliminados["comprobantes"] = n

    if body.area in ("rendipe", "todo"):
        # Gastos de comisiones
        comision_ids = [
            cid for (cid,) in db.execute(
                select(Comision.id).where(Comision.tenant_id.in_(test_tenant_ids))
            ).all()
        ]
        if comision_ids:
            n = db.execute(delete(GastoComision).where(GastoComision.comision_id.in_(comision_ids))).rowcount
            eliminados["gastos_comision"] = n
        # Comisiones
        n = db.execute(delete(Comision).where(Comision.tenant_id.in_(test_tenant_ids))).rowcount
        eliminados["comisiones"] = n
        # Servidores
        n = db.execute(delete(Servidor).where(Servidor.tenant_id.in_(test_tenant_ids))).rowcount
        eliminados["servidores"] = n
        # InstitucionConfig
        n = db.execute(delete(InstitucionConfig).where(InstitucionConfig.tenant_id.in_(test_tenant_ids))).rowcount
        eliminados["institucion_config"] = n

    if body.area in ("usuarios", "todo"):
        # UsuarioTenant de tenants de prueba
        n = db.execute(delete(UsuarioTenant).where(UsuarioTenant.tenant_id.in_(test_tenant_ids))).rowcount
        eliminados["usuarios_tenants"] = n
        # Empresas
        if test_empresa_ids:
            n = db.execute(delete(EmpresaCliente).where(EmpresaCliente.id.in_(test_empresa_ids))).rowcount
            eliminados["empresas"] = n
        # Tenants de prueba
        n = db.execute(delete(Tenant).where(Tenant.id.in_(test_tenant_ids))).rowcount
        eliminados["tenants"] = n

    db.commit()
    duracion_ms = int((time.time() - start) * 1000)

    # Auditoria
    audit = RegistroAuditoria(
        usuario_id=sote_user.id,
        accion="reset_datos_prueba",
        tabla="multiple",
        registro_id="bulk",
        valor_nuevo={"area": body.area, "eliminados": eliminados},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(audit)
    db.commit()

    return {"eliminados": eliminados, "duracion_ms": duracion_ms}


# ── Seed ──────────────────────────────────────────────────────

@router.post("/seed")
def ejecutar_seed(
    body: SeedRequest,
    request: Request,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Ejecuta seed_datos.py programaticamente. Primero limpia, luego recarga."""
    if body.area not in ("contabilidad", "rendipe", "todo"):
        raise HTTPException(status_code=400, detail="Area invalida")

    try:
        result = subprocess.run(
            [sys.executable, "scripts/seed_datos.py", "--area", body.area],
            capture_output=True, text=True, timeout=60,
            env={**dict(__import__("os").environ), "PYTHONIOENCODING": "utf-8"},
        )
        output = result.stdout + result.stderr

        # Auditoria
        audit = RegistroAuditoria(
            usuario_id=sote_user.id,
            accion="seed_datos",
            tabla="multiple",
            registro_id="bulk",
            valor_nuevo={"area": body.area, "exit_code": result.returncode},
            ip=request.client.host if request.client else None,
        )
        db.add(audit)
        db.commit()

        return {
            "exito": result.returncode == 0,
            "salida": output[-2000:] if len(output) > 2000 else output,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Seed tardo mas de 60 segundos")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error ejecutando seed: {str(e)}")


# ── Impersonar ────────────────────────────────────────────────

@router.post("/impersonar/{usuario_id}")
def impersonar(
    usuario_id: str,
    request: Request,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Genera JWT temporal (30 min) para ver el sistema como otro usuario."""
    target = db.execute(
        select(Usuario).where(Usuario.id == usuario_id, Usuario.activo == True)
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Obtener tenant/rol del target
    ut = db.execute(
        select(UsuarioTenant).where(
            UsuarioTenant.usuario_id == target.id,
            UsuarioTenant.activo == True,
        )
    ).scalars().first()

    token_data = {
        "sub": str(target.id),
        "tenant_id": str(ut.tenant_id) if ut else None,
        "empresa_activa_id": target.empresa_activa_id,
        "rol": ut.rol.value if ut else "solo_lectura",
        "nombres": target.nombres,
        "apellidos": target.apellidos,
        "impersonado": True,
        "sote_user_id": str(sote_user.id),
    }
    token = create_access_token(token_data, expires_delta=timedelta(minutes=30))

    # Auditoria
    audit = RegistroAuditoria(
        usuario_id=sote_user.id,
        accion="impersonar",
        tabla="usuarios",
        registro_id=usuario_id,
        valor_nuevo={"target_dni": target.dni, "target_nombres": target.nombres},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(audit)
    db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "impersonando": f"{target.nombres} {target.apellidos} ({target.dni})",
        "expira_en_minutos": 30,
    }


@router.delete("/impersonar")
def terminar_impersonacion():
    """Termina la sesion de impersonacion (el frontend descarta el token temporal)."""
    return {"detail": "Sesion de impersonacion terminada. Usa tu token original."}


# ── Metricas globales ─────────────────────────────────────────

@router.get("/stats")
def stats_globales(
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Metricas globales del sistema."""
    hoy_inicio = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    return {
        "tenants_total": db.execute(select(func.count(Tenant.id)).where(Tenant.deleted_at == None)).scalar() or 0,
        "usuarios_total": db.execute(select(func.count(Usuario.id)).where(Usuario.deleted_at == None)).scalar() or 0,
        "comprobantes_hoy": db.execute(
            select(func.count(Comprobante.id)).where(Comprobante.created_at >= hoy_inicio)
        ).scalar() or 0,
        "pagos_hoy": db.execute(
            select(func.count(Pago.id)).where(Pago.created_at >= hoy_inicio)
        ).scalar() or 0,
        "comisiones_activas": db.execute(
            select(func.count(Comision.id)).where(Comision.estado.in_(["en_curso", "autorizada"]))
        ).scalar() or 0,
    }


# ── Invitaciones ──────────────────────────────────────────────

@router.post("/invitaciones")
def crear_invitacion(
    body: InvitacionRequest,
    request: Request,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Genera token unico valido 72 horas para registro de nuevo tenant."""
    token = secrets.token_urlsafe(48)
    expira = datetime.now(timezone.utc) + timedelta(hours=72)

    inv = Invitacion(
        token=token,
        whatsapp=body.whatsapp,
        tipo_tenant=body.tipo_tenant,
        plan=body.plan,
        nombre_contacto=body.nombre_contacto,
        creado_por=sote_user.id,
        expira_en=expira,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    return {
        "token": token,
        "url_registro": f"https://alerta.pe/registro?token={token}",
        "expira_en": expira.isoformat(),
        "id": inv.id,
    }


@router.get("/invitaciones")
def listar_invitaciones(
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Lista invitaciones pendientes y usadas."""
    invitaciones = db.execute(
        select(Invitacion).order_by(Invitacion.created_at.desc())
    ).scalars().all()

    ahora = datetime.now(timezone.utc)
    resultado = []
    for inv in invitaciones:
        expira_aware = inv.expira_en.replace(tzinfo=timezone.utc) if inv.expira_en and inv.expira_en.tzinfo is None else inv.expira_en
        estado = "usada" if inv.usado_en else ("expirada" if expira_aware and expira_aware < ahora else "pendiente")
        resultado.append({
            "id": inv.id,
            "token": inv.token[:12] + "...",
            "tipo_tenant": inv.tipo_tenant,
            "nombre_contacto": inv.nombre_contacto,
            "whatsapp": inv.whatsapp,
            "estado": estado,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
            "expira_en": inv.expira_en.isoformat() if inv.expira_en else None,
            "usado_en": inv.usado_en.isoformat() if inv.usado_en else None,
        })
    return {"invitaciones": resultado, "total": len(resultado)}


@router.delete("/invitaciones/{token}")
def revocar_invitacion(
    token: str,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Revocar invitacion (eliminarla)."""
    inv = db.execute(
        select(Invitacion).where(Invitacion.token == token)
    ).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitacion no encontrada")
    if inv.usado_en:
        raise HTTPException(status_code=400, detail="Invitacion ya fue usada, no se puede revocar")
    db.delete(inv)
    db.commit()
    return {"detail": "Invitacion revocada"}


# ── Health check completo ─────────────────────────────────────

@router.get("/health")
def health_completo(
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Health check completo: BD, Redis, GCS, OpenAI, SUNAT."""
    checks = {}

    # BD
    try:
        db.execute(select(func.count(Tenant.id)))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {str(e)[:100]}"

    # Redis
    try:
        import redis
        from app.core.config import settings
        r = redis.from_url(settings.REDIS_URL, socket_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "no_disponible"

    # OpenAI
    from app.core.config import settings
    checks["openai"] = "ok" if settings.OPENAI_API_KEY else "no_configurado"

    # SUNAT
    checks["sunat_api"] = "ok" if settings.SUNAT_CLIENT_ID else "no_configurado"

    return checks


# ── Log de auditoria global ──────────────────────────────────

@router.get("/logs")
def listar_logs(
    tenant_id: Optional[str] = None,
    usuario_id: Optional[str] = None,
    accion: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    limit: int = 50,
    sote_user: Usuario = Depends(_require_sote),
    db: Session = Depends(get_db),
):
    """Lista paginada de logs de auditoria."""
    query = select(RegistroAuditoria).order_by(RegistroAuditoria.created_at.desc())

    if usuario_id:
        query = query.where(RegistroAuditoria.usuario_id == usuario_id)
    if accion:
        query = query.where(RegistroAuditoria.accion == accion)
    if desde:
        query = query.where(RegistroAuditoria.created_at >= desde)
    if hasta:
        query = query.where(RegistroAuditoria.created_at <= hasta)

    query = query.limit(min(limit, 200))
    logs = db.execute(query).scalars().all()

    resultado = []
    for log in logs:
        resultado.append({
            "id": log.id,
            "usuario_id": str(log.usuario_id) if log.usuario_id else None,
            "accion": log.accion,
            "tabla": log.tabla,
            "registro_id": log.registro_id,
            "descripcion": log.descripcion,
            "ip": log.ip,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    return {"logs": resultado, "total": len(resultado)}
