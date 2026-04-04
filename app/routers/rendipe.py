"""
routers/rendipe.py — Endpoints del módulo RendiPe (Rendición de Gastos y Viáticos).

Secciones:
- Configuración: config general y rubros de gasto
- Servidores públicos: CRUD de comisionados
- Comisiones de servicio: ciclo de vida completo
- Gastos: registro por foto (OCR) o manual, aprobación
- Rendición: saldo, presentación, aprobación, PDF
- Informe de resultados: generación IA, edición, PDF, envío
- Dashboard y reportes

Todas las rutas requieren autenticación (get_current_user).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario, UsuarioTenant
from app.models.rendipe import (
    Comision,
    GastoComision,
    InformeComision,
    Servidor,
    InstitucionConfig,
    SaldoComision,
    EstadoComision,
    EstadoValidacionGasto,
    OrigenGasto,
)
from app.services.rendipe_service import (
    calcular_dias_comision,
    calcular_fecha_limite_rendicion,
    calcular_saldo_comision,
    generar_informe_ia,
    generar_pdf_rendicion,
    generar_pdf_informe,
    procesar_foto_gasto,
    verificar_vencimiento_rendiciones,
    calcular_rubros_propios,
    validar_gasto_vs_cobertura,
    validar_asistencia,
    registrar_asistencia,
    crear_gasto_dj,
    generar_pdf_dj,
    validar_limites_dj,
    crear_gasto_exterior,
)
from app.services.rendipe_alertas import crear_alerta_rendipe

router = APIRouter(prefix="/rendipe", tags=["rendipe"])


def _get_tenant_id(current_user: Usuario, db: Session) -> str:
    """Obtiene el tenant_id del usuario actual desde UsuarioTenant."""
    ut = db.execute(
        select(UsuarioTenant.tenant_id).where(
            UsuarioTenant.usuario_id == current_user.id,
            UsuarioTenant.activo == True,
            UsuarioTenant.deleted_at == None,
        )
    ).scalar()
    if not ut:
        raise HTTPException(status_code=403, detail="Sin tenant activo")
    return str(ut)


# ==========================================================================
# Pydantic schemas (inline)
# ==========================================================================

class InstitucionConfigOut(BaseModel):
    plazo_rendicion_dias: int = 10
    rubros_habilitados: list[str] = []
    moneda: str = "PEN"
    requiere_aprobacion_jefe: bool = True
    requiere_aprobacion_tesoreria: bool = True

    class Config:
        from_attributes = True


class InstitucionConfigUpdate(BaseModel):
    plazo_rendicion_dias: Optional[int] = None
    moneda: Optional[str] = None
    requiere_aprobacion_jefe: Optional[bool] = None
    requiere_aprobacion_tesoreria: Optional[bool] = None


class RubroIn(BaseModel):
    codigo: str
    nombre: str
    tope_diario: Optional[Decimal] = None
    requiere_comprobante: bool = True


class ServidorIn(BaseModel):
    dni: str = Field(..., min_length=8, max_length=8)
    nombres: str
    apellidos: str
    cargo: Optional[str] = None
    area: Optional[str] = None
    regimen_laboral: Optional[str] = None


class ServidorOut(BaseModel):
    id: int
    dni: str
    nombres: str
    apellidos: str
    cargo: Optional[str] = None
    area: Optional[str] = None
    regimen_laboral: Optional[str] = None

    class Config:
        from_attributes = True


class ComisionIn(BaseModel):
    servidor_id: int
    destino: str
    motivo: Optional[str] = None
    objetivo: Optional[str] = None
    fecha_inicio: date
    fecha_fin: date
    monto_asignado: Decimal = Field(..., gt=0)
    documento_autorizacion: Optional[str] = None


class ComisionOut(BaseModel):
    id: int
    servidor_id: int
    destino: str
    motivo: Optional[str] = None
    objetivo: Optional[str] = None
    fecha_inicio: date
    fecha_fin: date
    monto_asignado: Decimal
    estado: str
    dias_comision: Optional[int] = None
    fecha_limite_rendicion: Optional[date] = None

    class Config:
        from_attributes = True


class EstadoUpdate(BaseModel):
    estado: str
    observacion: Optional[str] = None


class GastoManualIn(BaseModel):
    fecha: date
    ruc_emisor: Optional[str] = None
    razon_social_emisor: Optional[str] = None
    tipo_comprobante: Optional[str] = None
    serie: Optional[str] = None
    correlativo: Optional[str] = None
    monto: Decimal = Field(..., gt=0)
    rubro: str
    descripcion: Optional[str] = None


class GastoUpdate(BaseModel):
    fecha: Optional[date] = None
    ruc_emisor: Optional[str] = None
    razon_social_emisor: Optional[str] = None
    tipo_comprobante: Optional[str] = None
    serie: Optional[str] = None
    correlativo: Optional[str] = None
    monto: Optional[Decimal] = None
    rubro: Optional[str] = None
    descripcion: Optional[str] = None


class GastoOut(BaseModel):
    id: int
    comision_id: int
    fecha: Optional[date] = None
    ruc_emisor: Optional[str] = None
    razon_social_emisor: Optional[str] = None
    tipo_comprobante: Optional[str] = None
    serie: Optional[str] = None
    correlativo: Optional[str] = None
    monto: Decimal
    rubro: Optional[str] = None
    descripcion: Optional[str] = None
    estado: str
    imagen_url: Optional[str] = None

    class Config:
        from_attributes = True


class SaldoOut(BaseModel):
    total_asignado: Decimal
    total_gastado: Decimal
    total_observado: Decimal
    saldo: Decimal
    tipo_saldo: str


class InformeOut(BaseModel):
    id: int
    comision_id: int
    contenido: Optional[str] = None
    generado_por_ia: bool = False
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InformeUpdate(BaseModel):
    contenido: str


class SaldoRegistrar(BaseModel):
    tipo: str = Field(..., description="'devolucion' o 'reembolso'")
    monto: Decimal = Field(..., gt=0)
    medio_pago: Optional[str] = None
    numero_recibo: Optional[str] = None
    observacion: Optional[str] = None


# ==========================================================================
# CONFIGURACIÓN
# ==========================================================================

@router.get("/config", response_model=InstitucionConfigOut)
def obtener_config(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Obtiene la configuración RendiPe del tenant del usuario."""
    config = db.execute(
        select(InstitucionConfig).where(InstitucionConfig.tenant_id == _get_tenant_id(current_user, db))
    ).scalar_one_or_none()
    if not config:
        return InstitucionConfigOut()
    return config


@router.put("/config", response_model=InstitucionConfigOut)
def actualizar_config(
    datos: InstitucionConfigUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza la configuración RendiPe del tenant."""
    config = db.execute(
        select(InstitucionConfig).where(InstitucionConfig.tenant_id == _get_tenant_id(current_user, db))
    ).scalar_one_or_none()
    if not config:
        config = InstitucionConfig(tenant_id=_get_tenant_id(current_user, db))
        db.add(config)

    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(config, campo, valor)

    db.commit()
    db.refresh(config)
    return config


@router.post("/config/rubros", status_code=status.HTTP_201_CREATED)
def crear_rubro(
    rubro: RubroIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Agrega un rubro de gasto a la configuración del tenant (JSONB en InstitucionConfig)."""
    config = db.execute(
        select(InstitucionConfig).where(InstitucionConfig.tenant_id == _get_tenant_id(current_user, db))
    ).scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configuración no encontrada")
    rubros = config.rubros_habilitados or []
    rubros.append({
        "codigo": rubro.codigo,
        "nombre": rubro.nombre,
        "tope_diario": rubro.tope_diario,
        "requiere_comprobante": rubro.requiere_comprobante,
    })
    config.rubros_habilitados = rubros
    db.commit()
    db.refresh(nuevo)
    return {"id": nuevo.id, "codigo": nuevo.codigo, "nombre": nuevo.nombre}


# ==========================================================================
# SERVIDORES
# ==========================================================================

@router.get("/servidores/", response_model=list[ServidorOut])
def listar_servidores(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista los servidores públicos del tenant."""
    servidores = db.execute(
        select(Servidor).where(
            Servidor.tenant_id == _get_tenant_id(current_user, db),
            Servidor.deleted_at.is_(None),
        )
    ).scalars().all()
    return servidores


@router.post("/servidores/", response_model=ServidorOut, status_code=status.HTTP_201_CREATED)
def crear_servidor(
    datos: ServidorIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registra un nuevo servidor público comisionable."""
    servidor = Servidor(
        tenant_id=_get_tenant_id(current_user, db),
        dni=datos.dni,
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        cargo=datos.cargo,
        area=datos.area,
        regimen_laboral=datos.regimen_laboral,
    )
    db.add(servidor)
    db.commit()
    db.refresh(servidor)
    return servidor


@router.put("/servidores/{servidor_id}", response_model=ServidorOut)
def actualizar_servidor(
    servidor_id: int,
    datos: ServidorIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza datos de un servidor público."""
    servidor = db.execute(
        select(Servidor).where(
            Servidor.id == servidor_id,
            Servidor.tenant_id == _get_tenant_id(current_user, db),
            Servidor.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not servidor:
        raise HTTPException(status_code=404, detail="Servidor no encontrado")

    for campo, valor in datos.model_dump().items():
        setattr(servidor, campo, valor)

    db.commit()
    db.refresh(servidor)
    return servidor


# ==========================================================================
# COMISIONES
# ==========================================================================

@router.get("/comisiones/", response_model=list[ComisionOut])
def listar_comisiones(
    estado: Optional[str] = None,
    servidor_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista comisiones del tenant con filtros opcionales."""
    query = select(Comision).where(
        Comision.tenant_id == _get_tenant_id(current_user, db),
        Comision.deleted_at.is_(None),
    )
    if estado:
        query = query.where(Comision.estado == estado)
    if servidor_id:
        query = query.where(Comision.servidor_id == servidor_id)

    query = query.order_by(Comision.fecha_inicio.desc())
    query = query.offset((page - 1) * size).limit(size)

    comisiones = db.execute(query).scalars().all()
    return comisiones


@router.post("/comisiones/", response_model=ComisionOut, status_code=status.HTTP_201_CREATED)
def crear_comision(
    datos: ComisionIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea una nueva comisión de servicio."""
    # Validar que el servidor pertenezca al tenant
    servidor = db.execute(
        select(Servidor).where(
            Servidor.id == datos.servidor_id,
            Servidor.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not servidor:
        raise HTTPException(status_code=404, detail="Servidor no encontrado en este tenant")

    if datos.fecha_fin < datos.fecha_inicio:
        raise HTTPException(status_code=400, detail="fecha_fin debe ser >= fecha_inicio")

    # Obtener config para plazo de rendición
    config = db.execute(
        select(InstitucionConfig).where(InstitucionConfig.tenant_id == _get_tenant_id(current_user, db))
    ).scalar_one_or_none()
    plazo = config.plazo_rendicion_dias if config else 10

    dias = calcular_dias_comision(datos.fecha_inicio, datos.fecha_fin)
    fecha_limite = calcular_fecha_limite_rendicion(datos.fecha_fin, plazo)

    comision = Comision(
        tenant_id=_get_tenant_id(current_user, db),
        servidor_id=datos.servidor_id,
        destino=datos.destino,
        motivo=datos.motivo,
        objetivo=datos.objetivo,
        fecha_inicio=datos.fecha_inicio,
        fecha_fin=datos.fecha_fin,
        monto_asignado=datos.monto_asignado,
        documento_autorizacion=datos.documento_autorizacion,
        dias_comision=dias,
        fecha_limite_rendicion=fecha_limite,
        estado=EstadoComision.CREADA,
        creado_por_id=current_user.id,
    )
    db.add(comision)
    db.commit()
    db.refresh(comision)

    crear_alerta_rendipe(
        db=db,
        tenant_id=_get_tenant_id(current_user, db),
        tipo="comision_creada",
        comision_id=comision.id,
        mensaje=f"Comisión a {datos.destino} para {servidor.nombres} {servidor.apellidos}.",
    )

    return comision


@router.get("/comisiones/{comision_id}", response_model=ComisionOut)
def obtener_comision(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Obtiene detalle de una comisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")
    return comision


@router.put("/comisiones/{comision_id}/estado")
def cambiar_estado_comision(
    comision_id: int,
    datos: EstadoUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cambia el estado de una comisión (aprobar, iniciar rendición, etc.)."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    comision.estado = datos.estado
    if datos.observacion:
        comision.observacion_estado = datos.observacion

    db.commit()
    db.refresh(comision)
    return {"id": comision.id, "estado": comision.estado}


@router.get("/comisiones/pendientes-rend", response_model=list[ComisionOut])
def listar_pendientes_rendicion(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista comisiones pendientes de rendición."""
    comisiones = db.execute(
        select(Comision).where(
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.estado.in_([
                EstadoComision.PENDIENTE_RENDICION,
                EstadoComision.EN_RENDICION,
            ]),
            Comision.deleted_at.is_(None),
        ).order_by(Comision.fecha_limite_rendicion)
    ).scalars().all()
    return comisiones


@router.get("/comisiones/vencidas", response_model=list[ComisionOut])
def listar_vencidas(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista comisiones con rendición vencida."""
    hoy = date.today()
    comisiones = db.execute(
        select(Comision).where(
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.estado.in_([
                EstadoComision.PENDIENTE_RENDICION,
                EstadoComision.EN_RENDICION,
            ]),
            Comision.fecha_limite_rendicion < hoy,
            Comision.deleted_at.is_(None),
        ).order_by(Comision.fecha_limite_rendicion)
    ).scalars().all()
    return comisiones


# ==========================================================================
# GASTOS
# ==========================================================================

@router.get("/comisiones/{comision_id}/gastos", response_model=list[GastoOut])
def listar_gastos(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista gastos de una comisión."""
    # Verificar acceso a la comisión
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    gastos = db.execute(
        select(GastoComision).where(
            GastoComision.comision_id == comision_id,
            GastoComision.deleted_at.is_(None),
        ).order_by(GastoComision.fecha)
    ).scalars().all()
    return gastos


@router.post("/comisiones/{comision_id}/gastos/foto", response_model=GastoOut)
async def registrar_gasto_foto(
    comision_id: int,
    rubro: str = Query(..., description="Rubro del gasto"),
    archivo: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registra un gasto a partir de una foto de comprobante (OCR)."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    imagen_bytes = await archivo.read()
    if not imagen_bytes:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    try:
        gasto = procesar_foto_gasto(imagen_bytes, comision_id, rubro, db)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Error procesando imagen: {str(e)}")

    return gasto


@router.post("/comisiones/{comision_id}/gastos/manual", response_model=GastoOut, status_code=status.HTTP_201_CREATED)
def registrar_gasto_manual(
    comision_id: int,
    datos: GastoManualIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registra un gasto de forma manual (sin foto)."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    gasto = GastoComision(
        comision_id=comision_id,
        tenant_id=comision.tenant_id,
        fecha=datos.fecha,
        ruc_emisor=datos.ruc_emisor,
        razon_social_emisor=datos.razon_social_emisor,
        tipo_comprobante=datos.tipo_comprobante,
        serie=datos.serie,
        correlativo=datos.correlativo,
        monto=datos.monto,
        rubro=datos.rubro,
        descripcion=datos.descripcion,
        estado=EstadoValidacionGasto.PENDIENTE,
    )
    db.add(gasto)
    db.commit()
    db.refresh(gasto)
    return gasto


@router.put("/gastos/{gasto_id}", response_model=GastoOut)
def actualizar_gasto(
    gasto_id: int,
    datos: GastoUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza un gasto existente (solo si está en estado pendiente u observado)."""
    gasto = db.execute(
        select(GastoComision).where(
            GastoComision.id == gasto_id,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not gasto:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    if gasto.estado not in (EstadoValidacionGasto.PENDIENTE, EstadoValidacionGasto.OBSERVADO):
        raise HTTPException(status_code=400, detail="Solo se pueden editar gastos pendientes u observados")

    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(gasto, campo, valor)

    db.commit()
    db.refresh(gasto)
    return gasto


@router.delete("/gastos/{gasto_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_gasto(
    gasto_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Elimina (soft delete) un gasto."""
    gasto = db.execute(
        select(GastoComision).where(
            GastoComision.id == gasto_id,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not gasto:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    gasto.deleted_at = datetime.utcnow()
    db.commit()


@router.put("/gastos/{gasto_id}/aprobar")
def aprobar_gasto(
    gasto_id: int,
    observacion: Optional[str] = Query(None),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aprueba o marca como observado un gasto individual."""
    gasto = db.execute(
        select(GastoComision).where(
            GastoComision.id == gasto_id,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not gasto:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    if observacion:
        gasto.estado = EstadoValidacionGasto.OBSERVADO
        gasto.observacion = observacion
        crear_alerta_rendipe(
            db=db,
            tenant_id=gasto.tenant_id,
            tipo="gasto_observado",
            comision_id=gasto.comision_id,
            mensaje=f"Gasto #{gasto.id} observado: {observacion}",
        )
    else:
        gasto.estado = EstadoValidacionGasto.APROBADO

    gasto.aprobado_por_id = current_user.id
    db.commit()
    db.refresh(gasto)
    return {"id": gasto.id, "estado": gasto.estado}


# ==========================================================================
# RENDICIÓN (saldo, presentar, PDF, aprobar)
# ==========================================================================

@router.get("/comisiones/{comision_id}/saldo", response_model=SaldoOut)
def obtener_saldo(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Calcula el saldo actual de la comisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    return calcular_saldo_comision(comision_id, db)


@router.post("/comisiones/{comision_id}/presentar")
def presentar_rendicion(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Presenta la rendición de gastos para revisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    # Verificar que hay al menos un gasto
    count = db.execute(
        select(func.count()).where(
            GastoComision.comision_id == comision_id,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar()
    if not count:
        raise HTTPException(status_code=400, detail="No hay gastos registrados para presentar")

    comision.estado = EstadoComision.RENDICION_PRESENTADA
    comision.fecha_presentacion = datetime.utcnow()
    db.commit()
    return {"id": comision.id, "estado": comision.estado, "mensaje": "Rendición presentada correctamente"}


@router.get("/comisiones/{comision_id}/pdf-rendicion")
def descargar_pdf_rendicion(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera y descarga el PDF de la planilla de rendición."""
    from fastapi.responses import Response

    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    pdf_bytes = generar_pdf_rendicion(comision_id, db)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=rendicion_{comision_id}.pdf"},
    )


@router.post("/comisiones/{comision_id}/aprobar-rend")
def aprobar_rendicion(
    comision_id: int,
    observacion: Optional[str] = Query(None),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aprueba o rechaza la rendición completa."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    if observacion:
        comision.estado = EstadoComision.RENDICION_OBSERVADA
        comision.observacion_estado = observacion
        crear_alerta_rendipe(
            db=db,
            tenant_id=comision.tenant_id,
            tipo="rendicion_rechazada",
            comision_id=comision.id,
            mensaje=f"Rendición observada: {observacion}",
        )
    else:
        comision.estado = EstadoComision.RENDICION_APROBADA
        comision.aprobado_por_id = current_user.id
        crear_alerta_rendipe(
            db=db,
            tenant_id=comision.tenant_id,
            tipo="rendicion_aprobada",
            comision_id=comision.id,
            mensaje=f"Rendición de comisión a {comision.destino} aprobada.",
        )

    db.commit()
    return {"id": comision.id, "estado": comision.estado}


@router.post("/comisiones/{comision_id}/saldo/registrar")
def registrar_saldo(
    comision_id: int,
    datos: SaldoRegistrar,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registra la devolución o reembolso del saldo de la comisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    comision.saldo_tipo = datos.tipo
    comision.saldo_monto = datos.monto
    comision.saldo_medio_pago = datos.medio_pago
    comision.saldo_numero_recibo = datos.numero_recibo
    comision.saldo_observacion = datos.observacion
    comision.saldo_registrado_por_id = current_user.id
    comision.saldo_fecha = datetime.utcnow()

    db.commit()
    return {"id": comision.id, "saldo_registrado": True, "tipo": datos.tipo, "monto": str(datos.monto)}


# ==========================================================================
# INFORME DE RESULTADOS
# ==========================================================================

@router.get("/comisiones/{comision_id}/informe", response_model=InformeOut)
def obtener_informe(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Obtiene el informe de resultados de una comisión."""
    informe = db.execute(
        select(InformeComision).where(InformeComision.comision_id == comision_id)
    ).scalar_one_or_none()
    if not informe:
        raise HTTPException(status_code=404, detail="Informe no encontrado. Genere uno primero.")
    return informe


@router.post("/comisiones/{comision_id}/informe/generar", response_model=InformeOut)
def generar_informe(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera un borrador de informe usando IA."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    contenido = generar_informe_ia(comision_id, db)

    # Crear o actualizar informe
    informe = db.execute(
        select(InformeComision).where(InformeComision.comision_id == comision_id)
    ).scalar_one_or_none()

    if informe:
        informe.contenido = contenido
        informe.generado_por_ia = True
    else:
        informe = InformeComision(
            comision_id=comision_id,
            tenant_id=comision.tenant_id,
            contenido=contenido,
            generado_por_ia=True,
            creado_por_id=current_user.id,
        )
        db.add(informe)

    db.commit()
    db.refresh(informe)
    return informe


@router.put("/comisiones/{comision_id}/informe", response_model=InformeOut)
def editar_informe(
    comision_id: int,
    datos: InformeUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edita el contenido del informe de resultados."""
    informe = db.execute(
        select(InformeComision).where(InformeComision.comision_id == comision_id)
    ).scalar_one_or_none()
    if not informe:
        raise HTTPException(status_code=404, detail="Informe no encontrado")

    informe.contenido = datos.contenido
    db.commit()
    db.refresh(informe)
    return informe


@router.post("/comisiones/{comision_id}/informe/pdf")
def descargar_pdf_informe(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera y descarga el PDF del informe de resultados."""
    from fastapi.responses import Response

    informe = db.execute(
        select(InformeComision).where(InformeComision.comision_id == comision_id)
    ).scalar_one_or_none()
    if not informe:
        raise HTTPException(status_code=404, detail="Informe no encontrado")

    pdf_bytes = generar_pdf_informe(informe.id, db)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=informe_{comision_id}.pdf"},
    )


@router.post("/comisiones/{comision_id}/informe/enviar")
def enviar_informe(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marca el informe como enviado/presentado oficialmente."""
    informe = db.execute(
        select(InformeComision).where(InformeComision.comision_id == comision_id)
    ).scalar_one_or_none()
    if not informe:
        raise HTTPException(status_code=404, detail="Informe no encontrado")

    if not informe.contenido:
        raise HTTPException(status_code=400, detail="El informe no tiene contenido")

    informe.enviado = True
    informe.fecha_envio = datetime.utcnow()
    informe.enviado_por_id = current_user.id

    # Actualizar estado de la comisión
    comision = db.execute(
        select(Comision).where(Comision.id == comision_id)
    ).scalar_one_or_none()
    if comision:
        comision.estado = EstadoComision.INFORME_PRESENTADO

    db.commit()
    return {"id": informe.id, "enviado": True, "mensaje": "Informe enviado correctamente"}


# ==========================================================================
# DASHBOARD Y REPORTES
# ==========================================================================

@router.get("/dashboard")
def dashboard_rendipe(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard resumen del módulo RendiPe para el tenant actual."""
    tenant_id = _get_tenant_id(current_user, db)
    hoy = date.today()

    # Comisiones por estado
    total_comisiones = db.execute(
        select(func.count()).where(
            Comision.tenant_id == tenant_id,
            Comision.deleted_at.is_(None),
        )
    ).scalar() or 0

    pendientes_rendicion = db.execute(
        select(func.count()).where(
            Comision.tenant_id == tenant_id,
            Comision.estado.in_([EstadoComision.PENDIENTE_RENDICION, EstadoComision.EN_RENDICION]),
            Comision.deleted_at.is_(None),
        )
    ).scalar() or 0

    vencidas = db.execute(
        select(func.count()).where(
            Comision.tenant_id == tenant_id,
            Comision.estado.in_([EstadoComision.PENDIENTE_RENDICION, EstadoComision.EN_RENDICION]),
            Comision.fecha_limite_rendicion < hoy,
            Comision.deleted_at.is_(None),
        )
    ).scalar() or 0

    # Montos totales del mes actual
    monto_asignado_mes = db.execute(
        select(func.coalesce(func.sum(Comision.monto_asignado), 0)).where(
            Comision.tenant_id == tenant_id,
            func.extract("month", Comision.fecha_inicio) == hoy.month,
            func.extract("year", Comision.fecha_inicio) == hoy.year,
            Comision.deleted_at.is_(None),
        )
    ).scalar()

    monto_gastado_mes = db.execute(
        select(func.coalesce(func.sum(GastoComision.monto), 0)).where(
            GastoComision.tenant_id == tenant_id,
            GastoComision.estado == EstadoValidacionGasto.APROBADO,
            func.extract("month", GastoComision.fecha) == hoy.month,
            func.extract("year", GastoComision.fecha) == hoy.year,
            GastoComision.deleted_at.is_(None),
        )
    ).scalar()

    return {
        "total_comisiones": total_comisiones,
        "pendientes_rendicion": pendientes_rendicion,
        "vencidas": vencidas,
        "monto_asignado_mes": str(monto_asignado_mes),
        "monto_gastado_mes": str(monto_gastado_mes),
        "fecha": str(hoy),
    }


@router.get("/reportes/mes/{anio}/{mes}")
def reporte_mensual(
    anio: int,
    mes: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reporte mensual de comisiones y gastos."""
    if mes < 1 or mes > 12:
        raise HTTPException(status_code=400, detail="Mes debe ser entre 1 y 12")

    tenant_id = _get_tenant_id(current_user, db)

    comisiones = db.execute(
        select(Comision).where(
            Comision.tenant_id == tenant_id,
            func.extract("month", Comision.fecha_inicio) == mes,
            func.extract("year", Comision.fecha_inicio) == anio,
            Comision.deleted_at.is_(None),
        ).order_by(Comision.fecha_inicio)
    ).scalars().all()

    resumen = []
    total_asignado = Decimal("0")
    total_gastado = Decimal("0")

    for c in comisiones:
        saldo = calcular_saldo_comision(c.id, db)
        total_asignado += saldo["total_asignado"]
        total_gastado += saldo["total_gastado"]

        servidor = db.execute(
            select(Servidor).where(Servidor.id == c.servidor_id)
        ).scalar_one_or_none()

        resumen.append({
            "comision_id": c.id,
            "servidor": f"{servidor.nombres} {servidor.apellidos}" if servidor else "—",
            "destino": c.destino,
            "fecha_inicio": str(c.fecha_inicio),
            "fecha_fin": str(c.fecha_fin),
            "monto_asignado": str(saldo["total_asignado"]),
            "monto_gastado": str(saldo["total_gastado"]),
            "saldo": str(saldo["saldo"]),
            "estado": c.estado,
        })

    return {
        "anio": anio,
        "mes": mes,
        "total_comisiones": len(comisiones),
        "total_asignado": str(total_asignado),
        "total_gastado": str(total_gastado),
        "saldo_neto": str(total_asignado - total_gastado),
        "comisiones": resumen,
    }


# ==========================================================================
# SESIÓN 8: GEOLOCALIZACIÓN, DJ, EXTERIOR, SELFIE
# ==========================================================================

# ── Pydantic schemas (sesión 8) ──────────────────────────────

class GastoDJIn(BaseModel):
    rubro: str
    monto: Decimal = Field(..., gt=0)
    descripcion: str
    establecimiento: str
    motivo_sin_ce: str
    fecha_gasto: date


class GastoExteriorIn(BaseModel):
    rubro: str
    monto_ext: Decimal = Field(..., gt=0)
    moneda_ext: str = Field(..., min_length=3, max_length=3)
    descripcion: str
    establecimiento: str
    fecha_gasto: date
    tipo_cambio: Optional[Decimal] = None


class LugarUpdate(BaseModel):
    lugar_especifico: Optional[str] = None
    lugar_latitud: Optional[Decimal] = None
    lugar_longitud: Optional[Decimal] = None
    lugar_radio_metros: Optional[int] = None


class CoberturaInvitacionUpdate(BaseModel):
    cobertura_invitacion: dict


# ── Endpoints (sesión 8) ─────────────────────────────────────

@router.post("/gastos/{gasto_id}/asistencia")
async def endpoint_registrar_asistencia(
    gasto_id: int,
    lat: float = Query(..., description="Latitud del servidor"),
    lon: float = Query(..., description="Longitud del servidor"),
    foto: UploadFile = File(..., description="Selfie del servidor en el lugar"),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Registra asistencia/presencia del servidor en campo con selfie y GPS."""
    # Verificar que el gasto existe y pertenece al tenant
    gasto = db.execute(
        select(GastoComision).where(GastoComision.id == gasto_id)
    ).scalar_one_or_none()
    if not gasto:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    comision = db.execute(
        select(Comision).where(
            Comision.id == gasto.comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada o sin acceso")

    foto_bytes = await foto.read()
    if not foto_bytes:
        raise HTTPException(status_code=400, detail="Foto vacía")

    try:
        resultado = await registrar_asistencia(gasto_id, lat, lon, foto_bytes, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return resultado


@router.post("/comisiones/{comision_id}/gastos/dj")
async def endpoint_crear_gasto_dj(
    comision_id: int,
    datos: GastoDJIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea un gasto por Declaración Jurada (sin comprobante electrónico)."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    try:
        resultado = await crear_gasto_dj(
            comision_id=comision_id,
            rubro=datos.rubro,
            monto=float(datos.monto),
            descripcion=datos.descripcion,
            establecimiento=datos.establecimiento,
            motivo_sin_ce=datos.motivo_sin_ce,
            fecha_gasto=datos.fecha_gasto,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return resultado


@router.get("/gastos/{gasto_id}/dj/pdf")
async def endpoint_descargar_pdf_dj(
    gasto_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera y descarga el PDF de la Declaración Jurada de un gasto."""
    from fastapi.responses import Response

    gasto = db.execute(
        select(GastoComision).where(GastoComision.id == gasto_id)
    ).scalar_one_or_none()
    if not gasto:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    # Verificar acceso al tenant
    comision = db.execute(
        select(Comision).where(
            Comision.id == gasto.comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Sin acceso a esta comisión")

    try:
        pdf_bytes = await generar_pdf_dj(gasto_id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=dj_gasto_{gasto_id}.pdf"},
    )


@router.get("/comisiones/{comision_id}/dj/limites")
def endpoint_validar_limites_dj(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consulta los límites de DJ acumulados para la comisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    try:
        return validar_limites_dj(comision_id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/comisiones/{comision_id}/gastos/exterior")
async def endpoint_crear_gasto_exterior(
    comision_id: int,
    datos: GastoExteriorIn,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea un gasto en moneda extranjera para comisión internacional."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    try:
        resultado = await crear_gasto_exterior(
            comision_id=comision_id,
            rubro=datos.rubro,
            monto_ext=float(datos.monto_ext),
            moneda_ext=datos.moneda_ext,
            descripcion=datos.descripcion,
            establecimiento=datos.establecimiento,
            fecha_gasto=datos.fecha_gasto,
            tipo_cambio=float(datos.tipo_cambio) if datos.tipo_cambio else None,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return resultado


@router.put("/comisiones/{comision_id}/lugar")
def endpoint_actualizar_lugar(
    comision_id: int,
    datos: LugarUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza el lugar específico, coordenadas y radio de tolerancia de la comisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(comision, campo, valor)

    db.commit()
    db.refresh(comision)

    return {
        "id": comision.id,
        "lugar_especifico": comision.lugar_especifico,
        "lugar_latitud": str(comision.lugar_latitud) if comision.lugar_latitud else None,
        "lugar_longitud": str(comision.lugar_longitud) if comision.lugar_longitud else None,
        "lugar_radio_metros": comision.lugar_radio_metros,
    }


@router.put("/comisiones/{comision_id}/cobertura-invitacion")
def endpoint_actualizar_cobertura(
    comision_id: int,
    datos: CoberturaInvitacionUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza la cobertura de invitación (JSONB) de la comisión."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
            Comision.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    comision.cobertura_invitacion = datos.cobertura_invitacion
    db.commit()
    db.refresh(comision)

    return {
        "id": comision.id,
        "cobertura_invitacion": comision.cobertura_invitacion,
    }


@router.get("/comisiones/{comision_id}/rubros-propios")
def endpoint_rubros_propios(
    comision_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Calcula qué rubros/días debe pagar la institución (no cubiertos por invitante)."""
    comision = db.execute(
        select(Comision).where(
            Comision.id == comision_id,
            Comision.tenant_id == _get_tenant_id(current_user, db),
        )
    ).scalar_one_or_none()
    if not comision:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    return calcular_rubros_propios(comision)
