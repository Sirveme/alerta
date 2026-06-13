"""CRUD de clientes RUC."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_authenticated
from app.core.templates import templates
from app.models import Usuario
from app.services.cliente_ruc_service import (
    crear_cliente_ruc,
    eliminar_cliente,
    es_estudio_propio,
    listar_clientes_del_contador,
    obtener_cliente,
    obtener_plan_del_contador,
    puede_agregar_cliente,
)
from app.services.ruc_validator import validar_ruc

router = APIRouter(prefix="/clientes", tags=["clientes_ruc"])


# === LISTAR ===

@router.get("", response_class=HTMLResponse)
async def vista_lista_clientes(
    request: Request,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    clientes = await listar_clientes_del_contador(db, usuario.id)
    plan = await obtener_plan_del_contador(db, usuario.id)
    return templates.TemplateResponse(
        request,
        "clientes/lista.html",
        {"usuario": usuario, "clientes": clientes, "plan": plan},
    )


# === NUEVO ===

@router.get("/nuevo", response_class=HTMLResponse)
async def vista_nuevo_cliente(
    request: Request,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    plan = await obtener_plan_del_contador(db, usuario.id)
    return templates.TemplateResponse(
        request,
        "clientes/nuevo.html",
        {"usuario": usuario, "plan": plan},
    )


@router.post("/nuevo")
async def crear_cliente(
    request: Request,
    ruc: str = Form(...),
    razon_social: str = Form(...),
    nombre_referencia: str = Form(""),
    tipo_usuario_sol: int = Form(2),
    dni_titular: str = Form(""),
    usuario_sol: str = Form(""),
    clave_sol: str = Form(...),
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    ruc = (ruc or "").strip()
    if len(ruc) != 11 or not ruc.isdigit():
        raise HTTPException(400, "RUC inválido")

    es_propio = await es_estudio_propio(usuario, ruc)
    puede, mensaje = await puede_agregar_cliente(db, usuario.id, es_propio)
    if not puede:
        plan = await obtener_plan_del_contador(db, usuario.id)
        return templates.TemplateResponse(
            request,
            "clientes/nuevo.html",
            {
                "usuario": usuario,
                "plan": plan,
                "limite_alcanzado": True,
                "mensaje_limite": mensaje,
                "datos_form": {
                    "ruc": ruc,
                    "razon_social": razon_social,
                    "nombre_referencia": nombre_referencia,
                },
            },
        )

    await crear_cliente_ruc(
        db=db,
        contador_id=usuario.id,
        ruc=ruc,
        razon_social=razon_social.strip(),
        nombre_referencia=nombre_referencia,
        es_propio_estudio=es_propio,
        tipo_usuario_sol=tipo_usuario_sol,
        dni_titular=dni_titular.strip() if dni_titular else None,
        usuario_sol=usuario_sol.strip() if usuario_sol else None,
        clave_sol=clave_sol,
    )
    return RedirectResponse(url="/clientes?alta=ok", status_code=303)


# === DETALLE ===

@router.get("/{cliente_id}", response_class=HTMLResponse)
async def vista_detalle_cliente(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    cliente = await obtener_cliente(db, cliente_id, usuario.id)
    if not cliente:
        raise HTTPException(404, "Cliente no encontrado")

    # Lista de mensajes del cliente
    from sqlalchemy import select
    from app.models import MensajeBuzon

    msj_result = await db.execute(
        select(MensajeBuzon)
        .where(MensajeBuzon.cliente_ruc_id == cliente.id)
        .order_by(MensajeBuzon.fecha_envio_sunat.desc().nulls_last(),
                  MensajeBuzon.fecha_detectado.desc())
        .limit(50)
    )
    mensajes = list(msj_result.scalars().all())

    return templates.TemplateResponse(
        request,
        "clientes/detalle.html",
        {"usuario": usuario, "cliente": cliente, "mensajes": mensajes},
    )


# === ELIMINAR ===

@router.post("/{cliente_id}/eliminar")
async def borrar_cliente(
    cliente_id: int,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    ok = await eliminar_cliente(db, cliente_id, usuario.id)
    if not ok:
        raise HTTPException(404, "Cliente no encontrado")
    return RedirectResponse(url="/clientes?eliminado=ok", status_code=303)


# === VERIFICAR AHORA (manual) ===

@router.post("/{cliente_id}/verificar")
async def verificar_cliente_ahora(
    cliente_id: int,
    usuario: Usuario = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    cliente = await obtener_cliente(db, cliente_id, usuario.id)
    if not cliente:
        raise HTTPException(404, "Cliente no encontrado")

    from app.services.polling_service import verificar_buzon_cliente

    resultado = await verificar_buzon_cliente(db, cliente)
    return JSONResponse(resultado)


# === API: VALIDACIÓN RUC ===

@router.get("/api/validar-ruc/{ruc}")
async def api_validar_ruc(
    ruc: str,
    usuario: Usuario = Depends(require_authenticated),
):
    """Endpoint para que el formulario valide RUC en vivo."""
    data = await validar_ruc(ruc)
    if not data:
        return JSONResponse({"valido": False, "error": "RUC inválido o no encontrado"})
    return JSONResponse({"valido": True, **data})
