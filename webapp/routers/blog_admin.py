"""
webapp/routers/blog_admin.py — alerta.pe (zAlerta-40)
═══════════════════════════════════════════════════════════════════════
Panel de administración del blog. SOLO ADMIN (requiere_admin). El empresario
y el asistente NO acceden.

  GET  /admin/blog                 → listar (borradores + publicados)
  GET  /admin/blog/nuevo           → formulario de creación
  GET  /admin/blog/{id}/editar     → formulario de edición
  POST /admin/blog/guardar         → crear/actualizar (+ subir PDF/imagen a GCS)
  GET  /admin/blog/{id}/preview    → previsualizar como se verá público
  POST /admin/blog/{id}/publicar   → publicar (setea fecha_publicacion)
  POST /admin/blog/{id}/despublicar

Reusa gcs.py para PDF e imagen OG. Sin diálogos nativos.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from db import get_session
from models import ArticuloBlog, EstadoArticulo, ahora_lima
from ..core import templates
from ..deps import UsuarioActual, requiere_admin
import gcs

router = APIRouter(tags=["blog-admin"])


def _slugify(texto: str) -> str:
    base = unicodedata.normalize("NFD", (texto or "").lower())
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:180] or "articulo"


async def _slug_unico(session, base: str, excluir_id=None) -> str:
    slug = base
    n = 2
    while True:
        q = select(ArticuloBlog.id).where(ArticuloBlog.slug == slug)
        if excluir_id:
            q = q.where(ArticuloBlog.id != excluir_id)
        if not await session.scalar(q):
            return slug
        slug = f"{base}-{n}"
        n += 1


@router.get("/admin/blog", response_class=HTMLResponse)
async def admin_lista(request: Request,
                      user: UsuarioActual = Depends(requiere_admin)):
    async with get_session() as session:
        arts = list(await session.scalars(
            select(ArticuloBlog).order_by(ArticuloBlog.actualizado_at.desc())))
    return templates.TemplateResponse(request, "blog/admin_lista.html", {
        "user": user, "articulos": arts})


@router.get("/admin/blog/nuevo", response_class=HTMLResponse)
async def admin_nuevo(request: Request,
                      user: UsuarioActual = Depends(requiere_admin)):
    return templates.TemplateResponse(request, "blog/admin_form.html", {
        "user": user, "art": None})


@router.get("/admin/blog/{art_id}/editar", response_class=HTMLResponse)
async def admin_editar(request: Request, art_id: uuid.UUID,
                       user: UsuarioActual = Depends(requiere_admin)):
    async with get_session() as session:
        art = await session.get(ArticuloBlog, art_id)
    if not art:
        return RedirectResponse("/admin/blog", status_code=303)
    return templates.TemplateResponse(request, "blog/admin_form.html", {
        "user": user, "art": art})


@router.post("/admin/blog/guardar")
async def admin_guardar(
        user: UsuarioActual = Depends(requiere_admin),
        art_id: str = Form(""), titulo: str = Form(...), slug: str = Form(""),
        etiqueta_area: str = Form(""), tema: str = Form(""), region: str = Form(""),
        numero_rtf: str = Form(""), resumen_caso: str = Form(""),
        decision_tribunal: str = Form(""), por_que_importa: str = Form(""),
        preguntas_abiertas: str = Form(""), cierre: str = Form(""),
        meta_title: str = Form(""), meta_description: str = Form(""),
        keywords: str = Form(""),
        pdf: UploadFile = File(None), og_image: UploadFile = File(None)):
    async with get_session() as session:
        art = None
        if art_id:
            try:
                art = await session.get(ArticuloBlog, uuid.UUID(art_id))
            except (ValueError, TypeError):
                art = None
        nuevo = art is None
        if nuevo:
            art = ArticuloBlog(id=uuid.uuid4(), titulo=titulo,
                               estado=EstadoArticulo.BORRADOR)
            session.add(art)

        art.titulo = titulo
        base_slug = _slugify(slug or titulo)
        art.slug = await _slug_unico(session, base_slug,
                                     excluir_id=None if nuevo else art.id)
        art.etiqueta_area = etiqueta_area or None
        art.tema = tema or None
        art.region = region or None
        art.numero_rtf = numero_rtf or None
        art.resumen_caso = resumen_caso or None
        art.decision_tribunal = decision_tribunal or None
        art.por_que_importa = por_que_importa or None
        art.preguntas_abiertas = preguntas_abiertas or None
        art.cierre = cierre or None
        art.meta_title = meta_title or None
        art.meta_description = meta_description or None
        art.keywords = keywords or None

        await session.flush()   # asegura art.id para el gcs_key

        # Subidas a GCS (opcionales). Fallo de GCS NO bloquea el guardado.
        if pdf is not None and pdf.filename:
            data = await pdf.read()
            if data:
                key = f"blog/rtf/{art.slug}.pdf"
                subido = gcs.subir_pdf(data, key, content_type="application/pdf")
                if subido:
                    art.pdf_gcs_key = subido
        if og_image is not None and og_image.filename:
            data = await og_image.read()
            if data:
                ext = (og_image.filename.rsplit(".", 1)[-1] or "png").lower()
                ct = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
                key = f"blog/og/{art.slug}.{ 'jpg' if ext in ('jpg','jpeg') else 'png' }"
                subido = gcs.subir_pdf(data, key, content_type=ct)
                if subido:
                    art.og_image_gcs_key = subido

        await session.commit()
        aid = str(art.id)
    return RedirectResponse(f"/admin/blog/{aid}/editar", status_code=303)


@router.get("/admin/blog/{art_id}/preview", response_class=HTMLResponse)
async def admin_preview(request: Request, art_id: uuid.UUID,
                        user: UsuarioActual = Depends(requiere_admin)):
    import os
    base_url = os.getenv("APP_BASE_URL", "https://alerta.pe").rstrip("/")
    async with get_session() as session:
        art = await session.get(ArticuloBlog, art_id)
    if not art:
        return RedirectResponse("/admin/blog", status_code=303)
    return templates.TemplateResponse(request, "blog/articulo.html", {
        "art": art, "canonical": f"{base_url}/blog/{art.slug}",
        "og_image": f"{base_url}/static/img/icono.svg", "base_url": base_url,
        "tiene_pdf": bool(art.pdf_gcs_key), "preview": True})


@router.post("/admin/blog/{art_id}/publicar")
async def admin_publicar(art_id: uuid.UUID,
                         user: UsuarioActual = Depends(requiere_admin)):
    async with get_session() as session:
        art = await session.get(ArticuloBlog, art_id)
        if art:
            art.estado = EstadoArticulo.PUBLICADO
            if not art.fecha_publicacion:
                art.fecha_publicacion = ahora_lima()
            await session.commit()
    return RedirectResponse("/admin/blog", status_code=303)


@router.post("/admin/blog/{art_id}/despublicar")
async def admin_despublicar(art_id: uuid.UUID,
                            user: UsuarioActual = Depends(requiere_admin)):
    async with get_session() as session:
        art = await session.get(ArticuloBlog, art_id)
        if art:
            art.estado = EstadoArticulo.BORRADOR
            await session.commit()
    return RedirectResponse("/admin/blog", status_code=303)
