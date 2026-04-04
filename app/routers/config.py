"""
routers/config.py — Endpoints de configuración de usuario y empresa.

Decisiones técnicas:
- ConfigUsuario: 1:1 con Usuario. Se crea automáticamente si no existe al hacer GET.
- ConfigEmpresa: 1:1 con EmpresaCliente. Igual, se crea si no existe.
- Progreso de configuración: calcula % de campos obligatorios rellenados.
  Se usa para el onboarding gamificado ("Tu perfil está X% configurado").
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario
from app.models.empresas import EmpresaCliente
from app.models.configuracion import (
    ConfigUsuario, ConfigEmpresa,
    TemaUI, FuenteSize, CanalPreferido, TonoIA, VelocidadVoz, RegimenTributario,
)

router = APIRouter(prefix="/config", tags=["configuración"])


# --- Schemas ---

class ConfigUsuarioUpdate(BaseModel):
    tema: Optional[str] = None
    fuente_size: Optional[str] = None
    canal_preferido: Optional[str] = None
    horario_no_molestar_inicio: Optional[str] = None
    horario_no_molestar_fin: Optional[str] = None
    tono_ia: Optional[str] = None
    velocidad_voz: Optional[str] = None
    empresa_default_id: Optional[int] = None


class ConfigEmpresaUpdate(BaseModel):
    regimen_tributario: Optional[str] = None
    ciiu: Optional[str] = None
    umbral_alerta_monto: Optional[float] = None
    tiene_trabajadores: Optional[bool] = None
    exporta: Optional[bool] = None
    dia_cierre_mensual: Optional[int] = None
    palabras_clave_deducibles: Optional[list] = None
    palabras_clave_no_deducibles: Optional[list] = None
    proveedores_frecuentes: Optional[list] = None
    clientes_frecuentes: Optional[list] = None


# --- ConfigUsuario ---

@router.get("/usuario")
def get_config_usuario(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Devuelve la configuración del usuario actual. La crea si no existe."""
    config = db.execute(
        select(ConfigUsuario).where(ConfigUsuario.usuario_id == current_user.id)
    ).scalar_one_or_none()

    if not config:
        config = ConfigUsuario(usuario_id=current_user.id)
        db.add(config)
        db.commit()
        db.refresh(config)

    return {
        "tema": config.tema.value,
        "fuente_size": config.fuente_size.value,
        "canal_preferido": config.canal_preferido.value,
        "horario_no_molestar_inicio": str(config.horario_no_molestar_inicio) if config.horario_no_molestar_inicio else None,
        "horario_no_molestar_fin": str(config.horario_no_molestar_fin) if config.horario_no_molestar_fin else None,
        "tono_ia": config.tono_ia.value,
        "velocidad_voz": config.velocidad_voz.value,
        "empresa_default_id": config.empresa_default_id,
    }


@router.put("/usuario")
def update_config_usuario(
    body: ConfigUsuarioUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza la configuración del usuario. Solo actualiza campos enviados."""
    config = db.execute(
        select(ConfigUsuario).where(ConfigUsuario.usuario_id == current_user.id)
    ).scalar_one_or_none()

    if not config:
        config = ConfigUsuario(usuario_id=current_user.id)
        db.add(config)

    update_data = body.model_dump(exclude_unset=True)

    # Mapear strings a enums
    enum_map = {
        "tema": TemaUI,
        "fuente_size": FuenteSize,
        "canal_preferido": CanalPreferido,
        "tono_ia": TonoIA,
        "velocidad_voz": VelocidadVoz,
    }
    for field, enum_cls in enum_map.items():
        if field in update_data and update_data[field] is not None:
            try:
                update_data[field] = enum_cls(update_data[field])
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Valor inválido para {field}")

    # Parsear time fields
    from datetime import time as dt_time
    for time_field in ("horario_no_molestar_inicio", "horario_no_molestar_fin"):
        if time_field in update_data and update_data[time_field]:
            try:
                parts = update_data[time_field].split(":")
                update_data[time_field] = dt_time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                raise HTTPException(status_code=400, detail=f"Formato inválido para {time_field}. Usar HH:MM")

    for key, value in update_data.items():
        setattr(config, key, value)

    db.commit()
    return {"detail": "Configuración actualizada"}


# --- ConfigEmpresa ---

@router.get("/empresa/{empresa_id}")
def get_config_empresa(
    empresa_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Devuelve la configuración de una empresa. La crea si no existe."""
    config = db.execute(
        select(ConfigEmpresa).where(ConfigEmpresa.empresa_id == empresa_id)
    ).scalar_one_or_none()

    if not config:
        config = ConfigEmpresa(empresa_id=empresa_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    return {
        "empresa_id": config.empresa_id,
        "regimen_tributario": config.regimen_tributario.value if config.regimen_tributario else None,
        "ciiu": config.ciiu,
        "umbral_alerta_monto": float(config.umbral_alerta_monto) if config.umbral_alerta_monto else None,
        "tiene_trabajadores": config.tiene_trabajadores,
        "exporta": config.exporta,
        "dia_cierre_mensual": config.dia_cierre_mensual,
        "palabras_clave_deducibles": config.palabras_clave_deducibles,
        "palabras_clave_no_deducibles": config.palabras_clave_no_deducibles,
        "proveedores_frecuentes": config.proveedores_frecuentes,
        "clientes_frecuentes": config.clientes_frecuentes,
    }


@router.put("/empresa/{empresa_id}")
def update_config_empresa(
    empresa_id: int,
    body: ConfigEmpresaUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza la configuración de una empresa."""
    config = db.execute(
        select(ConfigEmpresa).where(ConfigEmpresa.empresa_id == empresa_id)
    ).scalar_one_or_none()

    if not config:
        config = ConfigEmpresa(empresa_id=empresa_id)
        db.add(config)

    update_data = body.model_dump(exclude_unset=True)

    if "regimen_tributario" in update_data and update_data["regimen_tributario"]:
        try:
            update_data["regimen_tributario"] = RegimenTributario(update_data["regimen_tributario"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Régimen tributario inválido")

    for key, value in update_data.items():
        setattr(config, key, value)

    db.commit()
    return {"detail": "Configuración de empresa actualizada"}


@router.get("/empresa/{empresa_id}/progreso")
def get_progreso_empresa(
    empresa_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Calcula el porcentaje de configuración completada de una empresa.
    Cuenta campos obligatorios no nulos vs total de campos obligatorios.
    """
    empresa = db.execute(
        select(EmpresaCliente).where(EmpresaCliente.id == empresa_id)
    ).scalar_one_or_none()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    config = db.execute(
        select(ConfigEmpresa).where(ConfigEmpresa.empresa_id == empresa_id)
    ).scalar_one_or_none()

    # Campos obligatorios y su estado
    campos = {
        "RUC": empresa.ruc,
        "Razón social": empresa.razon_social,
        "Cuentas bancarias": empresa.cuentas_bancarias,
        "Números Yape/Plin": empresa.numeros_yape_plin,
        "Email notificaciones": empresa.email_notificaciones_bancarias,
        "Régimen tributario": config.regimen_tributario if config else None,
        "CIIU": config.ciiu if config else None,
        "Día cierre mensual": config.dia_cierre_mensual if config else None,
        "Umbral alerta": config.umbral_alerta_monto if config else None,
    }

    completados = sum(1 for v in campos.values() if v is not None and v != [] and v != {})
    total = len(campos)
    faltantes = [k for k, v in campos.items() if v is None or v == [] or v == {}]

    porcentaje = round((completados / total) * 100) if total > 0 else 0

    return {
        "porcentaje": porcentaje,
        "completados": completados,
        "total": total,
        "faltantes": faltantes,
    }
