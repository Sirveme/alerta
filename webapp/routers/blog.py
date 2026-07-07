"""
webapp/routers/blog.py — alerta.pe (zAlerta-40)
═══════════════════════════════════════════════════════════════════════
Blog público de RTFs (Resoluciones del Tribunal Fiscal) resumidas para
empresarios. Es contenido para SEO, en el dominio propio.

  GET /blog                → índice (artículos PUBLICADOS, filtrable)
  GET /blog/{slug}         → artículo (bloques + PDF + disclaimer + CTA)
  GET /blog/{slug}/pdf     → PDF oficial de la RTF (signed URL desde GCS)
  GET /sitemap.xml         → sitemap con los artículos publicados
  GET /robots.txt          → permite el blog e indica el sitemap

Público (sin login): atrae, indexa y se comparte.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, func

from db import get_session
from models import ArticuloBlog, EstadoArticulo
from ..core import templates
import gcs

router = APIRouter(tags=["blog"])

BASE_URL = os.getenv("APP_BASE_URL", "https://alerta.pe").rstrip("/")
POR_PAGINA = 12


def _extracto(texto: str | None, n: int = 180) -> str:
    t = (texto or "").strip().replace("\n", " ")
    return (t[:n] + "…") if len(t) > n else t


@router.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request, area: str | None = None,
                     tema: str | None = None, page: int = 1):
    page = max(1, page)
    async with get_session() as session:
        cond = [ArticuloBlog.estado == EstadoArticulo.PUBLICADO]
        if area:
            cond.append(ArticuloBlog.etiqueta_area == area)
        if tema:
            cond.append(ArticuloBlog.tema == tema)
        total = await session.scalar(
            select(func.count(ArticuloBlog.id)).where(*cond)) or 0
        arts = list(await session.scalars(
            select(ArticuloBlog).where(*cond)
            .order_by(ArticuloBlog.fecha_publicacion.desc().nullslast(),
                      ArticuloBlog.creado_at.desc())
            .offset((page - 1) * POR_PAGINA).limit(POR_PAGINA)))
        # Chips de filtro: áreas y temas presentes en lo publicado.
        areas = list(await session.scalars(
            select(ArticuloBlog.etiqueta_area).where(
                ArticuloBlog.estado == EstadoArticulo.PUBLICADO,
                ArticuloBlog.etiqueta_area.is_not(None)).distinct()))
        temas = list(await session.scalars(
            select(ArticuloBlog.tema).where(
                ArticuloBlog.estado == EstadoArticulo.PUBLICADO,
                ArticuloBlog.tema.is_not(None)).distinct()))
    hay_mas = total > page * POR_PAGINA
    return templates.TemplateResponse(request, "blog/index.html", {
        "articulos": arts, "extracto": _extracto,
        "areas": areas, "temas": temas,
        "area_sel": area, "tema_sel": tema,
        "page": page, "hay_mas": hay_mas,
        "base_url": BASE_URL,
        "canonical": f"{BASE_URL}/blog",
        "og_default": f"{BASE_URL}/static/img/icono.svg",
    })


@router.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_articulo(request: Request, slug: str):
    async with get_session() as session:
        art = await session.scalar(
            select(ArticuloBlog).where(
                ArticuloBlog.slug == slug,
                ArticuloBlog.estado == EstadoArticulo.PUBLICADO))
        if not art:
            return templates.TemplateResponse(
                request, "blog/no_encontrado.html",
                {"base_url": BASE_URL}, status_code=404)
        # Contador de vistas simple (sin obsesión por bots).
        art.vistas = (art.vistas or 0) + 1
        await session.commit()
        og_image = (f"{BASE_URL}/blog/{slug}/og" if art.og_image_gcs_key
                    else f"{BASE_URL}/static/img/icono.svg")
        datos = {
            "art": art,
            "canonical": f"{BASE_URL}/blog/{art.slug}",
            "og_image": og_image,
            "base_url": BASE_URL,
            "tiene_pdf": bool(art.pdf_gcs_key),
            "preview": False,
        }
    return templates.TemplateResponse(request, "blog/articulo.html", datos)


@router.get("/blog/{slug}/pdf")
async def blog_pdf(slug: str):
    async with get_session() as session:
        art = await session.scalar(
            select(ArticuloBlog).where(
                ArticuloBlog.slug == slug,
                ArticuloBlog.estado == EstadoArticulo.PUBLICADO))
    if not art or not art.pdf_gcs_key:
        return Response("Documento no disponible.", status_code=404)
    url = gcs.signed_url(art.pdf_gcs_key, minutos=15)
    if not url:
        return Response("El documento no está disponible por ahora.", status_code=503)
    return RedirectResponse(url, status_code=307)


@router.get("/blog/{slug}/og")
async def blog_og(slug: str):
    """Imagen OG del artículo (para compartir en redes) desde GCS."""
    async with get_session() as session:
        art = await session.scalar(
            select(ArticuloBlog).where(ArticuloBlog.slug == slug))
    if not art or not art.og_image_gcs_key:
        return RedirectResponse("/static/img/icono.svg", status_code=307)
    url = gcs.signed_url(art.og_image_gcs_key, minutos=60)
    return RedirectResponse(url or "/static/img/icono.svg", status_code=307)


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    async with get_session() as session:
        arts = list(await session.scalars(
            select(ArticuloBlog).where(
                ArticuloBlog.estado == EstadoArticulo.PUBLICADO)
            .order_by(ArticuloBlog.fecha_publicacion.desc().nullslast())))
    urls = [f"<url><loc>{BASE_URL}/blog</loc><changefreq>daily</changefreq></url>"]
    for a in arts:
        fecha = (a.fecha_publicacion or a.actualizado_at)
        lastmod = fecha.date().isoformat() if fecha else ""
        urls.append(
            f"<url><loc>{BASE_URL}/blog/{a.slug}</loc>"
            + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
            + "<changefreq>monthly</changefreq></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(urls) + "</urlset>")
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt", include_in_schema=False)
async def robots():
    txt = ("User-agent: *\n"
           "Allow: /blog\n"
           "Disallow: /admin\n"
           "Disallow: /resumen\n"
           "Disallow: /mi-cuenta\n"
           f"Sitemap: {BASE_URL}/sitemap.xml\n")
    return Response(txt, media_type="text/plain")
