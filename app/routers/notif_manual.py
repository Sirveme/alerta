"""
routers/notif_manual.py — CRUD de notificaciones manuales del contador.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario
from app.models.notif_manual import NotifManual, TipoNotifManual, CanalNotifManual, EstadoNotifManual

router = APIRouter(prefix="/notif-manual", tags=["notificaciones manuales"])

PLANTILLAS = {
    "dj_presentada": "DJ {periodo} presentada ante SUNAT dentro del plazo.",
    "fraccionamiento_vence": "Su fraccionamiento SUNAT vence {fecha}. Evite perder el beneficio.",
    "reclamo_ganado": "Reclamo resuelto a su favor. No se pagará S/{monto}.",
    "auditoria_laboral": "SUNAFIL inicia auditoría laboral el {fecha}. Preparar documentación.",
    "credito_fiscal": "Crédito fiscal disponible: S/{monto} para el período {periodo}.",
}


class NotifManualCreate(BaseModel):
    empresa_id: int
    tipo: str = "informativo"
    titulo: str
    mensaje: str
    canal: str = "push"
    adjunto_gcs: Optional[str] = None


class NotifRapidaCreate(BaseModel):
    empresa_id: int
    tipo: str = "informativo"
    titulo: str
    mensaje: str
    canal: str = "push"


@router.post("/")
def crear_notificacion(
    body: NotifManualCreate,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crear notificación como borrador."""
    payload = request.state.token_payload
    notif = NotifManual(
        tenant_id=payload.get("tenant_id"),
        contador_id=current_user.id,
        empresa_id=body.empresa_id,
        tipo=TipoNotifManual(body.tipo),
        titulo=body.titulo,
        mensaje=body.mensaje,
        canal=CanalNotifManual(body.canal),
        adjunto_gcs=body.adjunto_gcs,
        estado=EstadoNotifManual.BORRADOR,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return {"id": notif.id, "estado": "borrador"}


@router.put("/{notif_id}/enviar")
def enviar_notificacion(
    notif_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enviar notificación (push + WhatsApp según canal)."""
    notif = db.execute(select(NotifManual).where(NotifManual.id == notif_id)).scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    notif.estado = EstadoNotifManual.ENVIADA
    notif.enviada_en = datetime.now(timezone.utc)
    db.commit()
    # TODO: integrar con servicio de push/WhatsApp real
    return {"detail": "Notificación enviada", "id": notif_id}


@router.get("/recibidas")
def notificaciones_recibidas(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Notificaciones recibidas por el usuario actual."""
    payload = request.state.token_payload
    empresa_id = payload.get("empresa_activa_id")

    query = select(NotifManual).where(
        NotifManual.empresa_id == empresa_id,
        NotifManual.estado != EstadoNotifManual.BORRADOR,
    ).order_by(NotifManual.created_at.desc()).limit(50)

    notifs = db.execute(query).scalars().all()
    return {
        "items": [
            {
                "id": n.id, "tipo": n.tipo.value, "titulo": n.titulo,
                "mensaje": n.mensaje, "estado": n.estado.value,
                "enviada_en": str(n.enviada_en) if n.enviada_en else None,
                "created_at": str(n.created_at),
            }
            for n in notifs
        ],
    }


@router.put("/{notif_id}/leer")
def marcar_leida(notif_id: int, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    notif = db.execute(select(NotifManual).where(NotifManual.id == notif_id)).scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="No encontrada")
    notif.estado = EstadoNotifManual.LEIDA
    notif.leida_en = datetime.now(timezone.utc)
    db.commit()
    return {"detail": "Marcada como leída"}


@router.post("/rapida")
def notif_rapida(
    body: NotifRapidaCreate,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crear y enviar en un solo paso."""
    payload = request.state.token_payload
    notif = NotifManual(
        tenant_id=payload.get("tenant_id"),
        contador_id=current_user.id,
        empresa_id=body.empresa_id,
        tipo=TipoNotifManual(body.tipo),
        titulo=body.titulo,
        mensaje=body.mensaje,
        canal=CanalNotifManual(body.canal),
        estado=EstadoNotifManual.ENVIADA,
        enviada_en=datetime.now(timezone.utc),
    )
    db.add(notif)
    db.commit()
    return {"id": notif.id, "detail": "Enviada"}


@router.get("/plantillas")
def plantillas():
    """Plantillas predefinidas de notificaciones."""
    return {"plantillas": PLANTILLAS}
