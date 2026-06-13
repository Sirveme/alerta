"""Robots.txt para SEO y para bloquear bots de proyecto anterior."""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse


router = APIRouter(tags=["robots"])


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Disallow: /clientes/
Disallow: /dashboard
Disallow: /configuracion
Disallow: /auth/
Disallow: /api/

# Patrones de un proyecto previo en este dominio - ya no existen
Disallow: /2020/
Disallow: /2021/
Disallow: /2022/
Disallow: /2023/
Disallow: /2024/
Disallow: /2025/
Disallow: /category/
Disallow: /tag/
Disallow: /author/
Disallow: /wp-
Disallow: /wprss
Disallow: /feed
Disallow: /comments/

Allow: /$
Allow: /sw.js
Allow: /static/
"""