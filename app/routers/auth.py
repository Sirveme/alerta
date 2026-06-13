"""Router de autenticación."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.security import decode_token, create_access_token
from app.core.templates import templates
from app.services.auth_service import autenticar_usuario, generar_tokens
from sqlalchemy import select
from app.models import Usuario

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def vista_login(request: Request):
    return templates.TemplateResponse(request, "auth/login.html")


@router.post("/login")
async def hacer_login(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    usuario = await autenticar_usuario(db, email, password)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
        )
    tokens = generar_tokens(usuario)
    redirect = RedirectResponse(url="/dashboard", status_code=303)
    redirect.set_cookie(
        key="access_token",
        value=tokens["access_token"],
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
    )
    redirect.set_cookie(
        key="refresh_token",
        value=tokens["refresh_token"],
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
    )
    return redirect


@router.post("/logout")
async def hacer_logout():
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.delete_cookie("access_token")
    redirect.delete_cookie("refresh_token")
    return redirect


@router.post("/refresh")
async def refrescar_token(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    refresh = request.cookies.get("refresh_token")
    if not refresh:
        raise HTTPException(status_code=401, detail="Sin refresh token")
    payload = decode_token(refresh)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Refresh token inválido")
    user_id = payload.get("sub")
    result = await db.execute(select(Usuario).where(Usuario.id == int(user_id)))
    usuario = result.scalar_one_or_none()
    if not usuario or not usuario.activo:
        raise HTTPException(status_code=401, detail="Usuario inactivo")

    nuevo_access = create_access_token(
        subject=str(usuario.id),
        rol=usuario.rol.value,
        extra_claims={"email": usuario.email, "nombre": usuario.nombre},
    )
    response.set_cookie(
        key="access_token",
        value=nuevo_access,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
    )
    return {"ok": True}
