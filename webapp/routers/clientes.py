"""
webapp/routers/clientes.py — alerta.pe (zAlerta-02 · acciones rápidas)
═══════════════════════════════════════════════════════════════════════
Alta de contribuyentes desde el botón "+":
  - GET  /api/grupos          → grupos del estudio (para el form, JSON).
  - POST /contribuyentes       → crea contribuyente + credencial SOL CIFRADA
                                 (Fernet) + asigna grupo(s). Multi-tenant.
  - POST /contribuyentes/importar → recibe .xlsx (MVP: UI + endpoint listos;
                                 el parseo masivo es fase aparte).

NUNCA se expone la clave SOL: se cifra al entrar y no se devuelve jamás.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy import select

from db import get_session
from models import Contribuyente, CredencialSol, Grupo, ContribuyenteGrupo, EstadoContribuyente
from cifrado import cifrar_clave_sol
from ..deps import UsuarioActual, usuario_actual, requiere_escritura

router = APIRouter(tags=["clientes"])


async def autocompletar_ficha(ruc: str) -> dict | None:
    """HOOK: ficha RUC vía API externa (Facturalo, etc.). MVP: sin red → None."""
    return None


@router.get("/api/grupos")
async def api_grupos(user: UsuarioActual = Depends(usuario_actual)):
    async with get_session() as session:
        grupos = (await session.scalars(
            select(Grupo).where(Grupo.estudio_id == user.estudio_id)
            .order_by(Grupo.orden, Grupo.nombre))).all()
    return JSONResponse([
        {"id": str(g.id), "nombre": g.nombre, "color": g.color or "#5B8DEF"}
        for g in grupos])


@router.post("/contribuyentes")
async def crear_contribuyente(
    request: Request, user: UsuarioActual = Depends(requiere_escritura)):
    data = await request.json()
    ruc = (data.get("ruc") or "").strip()
    usuario_sol = (data.get("usuario_sol") or "").strip()
    clave_sol = data.get("clave_sol") or ""
    razon_social = (data.get("razon_social") or "").strip() or None
    grupos_ids = data.get("grupos") or []

    if not (ruc.isdigit() and len(ruc) == 11):
        return JSONResponse({"ok": False, "error": "RUC inválido (11 dígitos)."}, status_code=400)
    if not usuario_sol or not clave_sol:
        return JSONResponse({"ok": False, "error": "Usuario y clave SOL son obligatorios."}, status_code=400)

    async with get_session() as session:
        # Unicidad de RUC dentro del estudio
        existe = await session.scalar(
            select(Contribuyente.id).where(
                Contribuyente.estudio_id == user.estudio_id,
                Contribuyente.ruc == ruc))
        if existe:
            return JSONResponse({"ok": False, "error": "Ese RUC ya está registrado."}, status_code=409)

        # Autocompletar ficha si hay hook disponible
        ficha = await autocompletar_ficha(ruc)
        if ficha and not razon_social:
            razon_social = ficha.get("razon_social")

        contrib = Contribuyente(
            estudio_id=user.estudio_id, ruc=ruc, razon_social=razon_social,
            estado=EstadoContribuyente.ACTIVO)
        if ficha:
            contrib.estado_sunat = ficha.get("estado")
            contrib.condicion_sunat = ficha.get("condicion")
            contrib.domicilio_fiscal = ficha.get("domicilio")
        session.add(contrib)
        await session.flush()

        # Credencial SOL — clave CIFRADA (Fernet)
        session.add(CredencialSol(
            contribuyente_id=contrib.id, estudio_id=user.estudio_id,
            usuario_sol=usuario_sol, clave_sol_cifrada=cifrar_clave_sol(clave_sol),
            tipo_usuario=2, quien_cargo=user.id, valida=True))

        # Asignar a grupo(s) válidos del estudio
        for gid in grupos_ids:
            try:
                gid_u = uuid.UUID(str(gid))
            except ValueError:
                continue
            g = await session.scalar(
                select(Grupo.id).where(Grupo.id == gid_u,
                                       Grupo.estudio_id == user.estudio_id))
            if g:
                session.add(ContribuyenteGrupo(
                    contribuyente_id=contrib.id, grupo_id=gid_u,
                    estudio_id=user.estudio_id))

        await session.commit()
        return JSONResponse({"ok": True, "id": str(contrib.id)})


@router.post("/contribuyentes/importar")
async def importar_excel(
    archivo: UploadFile = File(...),
    user: UsuarioActual = Depends(requiere_escritura)):
    """MVP: confirma recepción. El parseo masivo .xlsx es una fase aparte."""
    contenido = await archivo.read()
    # HOOK: parsear con openpyxl columnas RUC/usuario/clave/grupo y crear en lote.
    return JSONResponse({
        "ok": True,
        "mensaje": (f"Archivo «{archivo.filename}» recibido "
                    f"({len(contenido)} bytes). El procesamiento masivo se "
                    f"habilitará en la siguiente fase."),
    })
