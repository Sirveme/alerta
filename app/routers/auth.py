"""
routers/auth.py — Endpoints de autenticación: login, logout, cambiar empresa,
reset de clave por DNI secundario, WebAuthn (biometría).

Decisiones técnicas:
- Login por DNI (8 dígitos) + clave, sin email obligatorio.
- JWT incluye: user_id, tenant_id, empresa_activa_id, rol, tema, fuente_size.
  Esto evita queries adicionales para datos que se usan en cada request del frontend.
- Cambiar empresa: genera nuevo JWT sin logout (el frontend reemplaza el token).
- Reset de clave: flujo en 2 pasos con DNI secundario (familiar).
  Sin correo en ningún paso — muchos usuarios no tienen email.
- WebAuthn: usa la librería webauthn para generate/verify.
  Los challenges se almacenan en memoria (dict) por simplicidad.
  En producción: mover a Redis con TTL de 5 minutos.
- Blacklist de tokens: set en memoria para logout (dev).
  En producción: Redis con TTL = tiempo restante del token.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_reset_token,
    decode_token,
)
from app.core.deps import get_db, get_current_user
from app.models.usuarios import Usuario, UsuarioTenant, RecuperacionClave
from app.models.empresas import EmpresaCliente
from app.models.configuracion import ConfigUsuario
from app.models.auditoria import RegistroAuditoria

router = APIRouter(prefix="/auth", tags=["auth"])

# Blacklist de tokens en memoria (dev). Producción: Redis con TTL.
_token_blacklist: set[str] = set()

# Challenges WebAuthn en memoria (dev). Producción: Redis con TTL 5min.
_webauthn_challenges: dict[str, bytes] = {}


# --- Schemas ---

class LoginRequest(BaseModel):
    dni: str
    clave: str
    tenant_slug: Optional[str] = None

    @field_validator("dni")
    @classmethod
    def validate_dni(cls, v: str) -> str:
        if not re.match(r"^\d{8}$", v):
            raise ValueError("DNI debe ser exactamente 8 dígitos")
        return v


class CambiarEmpresaRequest(BaseModel):
    empresa_id: int


class ResetIniciarRequest(BaseModel):
    dni: str
    dni_secundario: str

    @field_validator("dni", "dni_secundario")
    @classmethod
    def validate_dnis(cls, v: str) -> str:
        if not re.match(r"^\d{8}$", v):
            raise ValueError("DNI debe ser exactamente 8 dígitos")
        return v


class ResetConfirmarRequest(BaseModel):
    token_temporal: str
    nueva_clave: str

    @field_validator("nueva_clave")
    @classmethod
    def validate_clave(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La clave debe tener mínimo 8 caracteres")
        return v


class WebAuthnBeginRequest(BaseModel):
    dni: Optional[str] = None


# --- Endpoints ---

@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Login con DNI + clave. Devuelve JWT con datos de sesión completos."""

    # Buscar usuario por DNI
    usuario = db.execute(
        select(Usuario).where(Usuario.dni == body.dni, Usuario.activo == True, Usuario.deleted_at == None)
    ).scalar_one_or_none()

    if not usuario or not verify_password(body.clave, usuario.password_hash):
        raise HTTPException(status_code=401, detail="DNI o clave incorrectos")

    # Obtener membresía en tenant (tomar el primero activo si no se especifica slug)
    ut_query = select(UsuarioTenant).where(
        UsuarioTenant.usuario_id == usuario.id,
        UsuarioTenant.activo == True,
        UsuarioTenant.deleted_at == None,
    )
    usuario_tenant = db.execute(ut_query).scalars().first()

    if not usuario_tenant:
        raise HTTPException(status_code=403, detail="No tienes acceso a ningún tenant activo")

    # Config de usuario (para tema/fuente en el frontend)
    config = db.execute(
        select(ConfigUsuario).where(ConfigUsuario.usuario_id == usuario.id)
    ).scalar_one_or_none()

    # Empresa activa: la del usuario o la default de config
    empresa_activa_id = usuario.empresa_activa_id
    if not empresa_activa_id and config and config.empresa_default_id:
        empresa_activa_id = config.empresa_default_id

    # Construir JWT payload
    token_data = {
        "sub": str(usuario.id),
        "tenant_id": str(usuario_tenant.tenant_id),
        "empresa_activa_id": empresa_activa_id,
        "rol": usuario_tenant.rol.value,
        "tema": config.tema.value if config else "semi",
        "fuente_size": config.fuente_size.value if config else "md",
        "nombres": usuario.nombres,
        "apellidos": usuario.apellidos,
    }
    token = create_access_token(token_data)

    # Actualizar último acceso
    usuario.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    # Auditoría
    _registrar_auditoria(db, usuario.id, "login", "usuarios", str(usuario.id),
                         ip=request.client.host if request.client else None,
                         user_agent=request.headers.get("user-agent"))

    # Setear cookie httponly para requests de templates
    response.set_cookie(
        key="token", value=token, httponly=True, samesite="lax",
        max_age=8 * 3600, secure=False,  # secure=True en producción con HTTPS
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(usuario.id),
            "dni": usuario.dni,
            "nombres": usuario.nombres,
            "apellidos": usuario.apellidos,
            "rol": usuario_tenant.rol.value,
            "tenant_id": str(usuario_tenant.tenant_id),
            "empresa_activa_id": empresa_activa_id,
            "tema": config.tema.value if config else "semi",
            "fuente_size": config.fuente_size.value if config else "md",
        },
    }


@router.post("/logout")
def logout(request: Request, response: Response, current_user: Usuario = Depends(get_current_user)):
    """Invalida el token actual (blacklist en memoria)."""
    token = request.cookies.get("token") or request.headers.get("authorization", "").replace("Bearer ", "")
    if token:
        _token_blacklist.add(token)
    response.delete_cookie("token")
    return {"detail": "Sesión cerrada"}


@router.post("/cambiar-empresa")
def cambiar_empresa(
    body: CambiarEmpresaRequest,
    request: Request,
    response: Response,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cambia la empresa activa sin logout.
    Valida acceso y devuelve nuevo JWT con empresa_activa_id actualizada.
    """
    payload = request.state.token_payload
    tenant_id = payload.get("tenant_id")

    # Validar que la empresa pertenece al tenant del usuario
    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.id == body.empresa_id,
            EmpresaCliente.tenant_id == tenant_id,
            EmpresaCliente.deleted_at == None,
        )
    ).scalar_one_or_none()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada en tu tenant")

    # Actualizar empresa activa en BD
    current_user.empresa_activa_id = body.empresa_id
    db.commit()

    # Generar nuevo JWT con empresa actualizada
    new_payload = {**payload, "empresa_activa_id": body.empresa_id}
    # Limpiar campos de control JWT
    for k in ("exp", "iat"):
        new_payload.pop(k, None)
    token = create_access_token(new_payload)

    response.set_cookie(
        key="token", value=token, httponly=True, samesite="lax",
        max_age=8 * 3600, secure=False,
    )

    # Auditoría
    _registrar_auditoria(db, current_user.id, "cambio_empresa", "usuarios",
                         str(current_user.id),
                         valor_nuevo={"empresa_activa_id": body.empresa_id},
                         ip=request.client.host if request.client else None)

    return {
        "access_token": token,
        "token_type": "bearer",
        "empresa_activa_id": body.empresa_id,
        "empresa_nombre": empresa.razon_social,
    }


@router.post("/reset-clave/iniciar")
def reset_clave_iniciar(body: ResetIniciarRequest, db: Session = Depends(get_db)):
    """
    Paso 1 del reset de clave: valida DNI + DNI secundario.
    Si coincide, devuelve token temporal de 15 minutos.
    """
    usuario = db.execute(
        select(Usuario).where(Usuario.dni == body.dni, Usuario.activo == True, Usuario.deleted_at == None)
    ).scalar_one_or_none()

    if not usuario:
        # No revelar si el usuario existe
        raise HTTPException(status_code=400, detail="Datos de verificación incorrectos")

    # Buscar recuperación activa con ese DNI secundario
    recuperacion = db.execute(
        select(RecuperacionClave).where(
            RecuperacionClave.usuario_id == usuario.id,
            RecuperacionClave.dni_secundario == body.dni_secundario,
            RecuperacionClave.activo == True,
        )
    ).scalar_one_or_none()

    if not recuperacion:
        raise HTTPException(status_code=400, detail="Datos de verificación incorrectos")

    # Generar token temporal
    token_temporal = create_reset_token(str(usuario.id))

    return {"token_temporal": token_temporal, "expira_en_minutos": 15}


@router.post("/reset-clave/confirmar")
def reset_clave_confirmar(body: ResetConfirmarRequest, db: Session = Depends(get_db)):
    """Paso 2: valida token temporal y actualiza la clave."""
    try:
        payload = decode_token(body.token_temporal)
        if payload.get("type") != "reset":
            raise HTTPException(status_code=400, detail="Token inválido")
        user_id = payload.get("sub")
    except Exception:
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

    usuario = db.execute(
        select(Usuario).where(Usuario.id == user_id, Usuario.activo == True)
    ).scalar_one_or_none()

    if not usuario:
        raise HTTPException(status_code=400, detail="Usuario no encontrado")

    usuario.password_hash = hash_password(body.nueva_clave)
    db.commit()

    return {"detail": "Clave actualizada exitosamente"}


# --- WebAuthn ---
# Decisión: implementación simplificada de WebAuthn.
# Los challenges se almacenan en memoria por user_id (dev).
# En producción: Redis con TTL de 5 minutos.

@router.post("/webauthn/register/begin")
def webauthn_register_begin(
    current_user: Usuario = Depends(get_current_user),
):
    """Genera challenge para registrar nueva credencial WebAuthn."""
    try:
        from webauthn import generate_registration_options
        from webauthn.helpers.structs import (
            AuthenticatorSelectionCriteria,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )
        from webauthn.helpers import bytes_to_base64url

        options = generate_registration_options(
            rp_id="localhost",
            rp_name="alerta.pe",
            user_id=str(current_user.id).encode(),
            user_name=current_user.dni,
            user_display_name=f"{current_user.nombres} {current_user.apellidos}",
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )

        # Guardar challenge
        _webauthn_challenges[str(current_user.id)] = options.challenge

        return {
            "challenge": bytes_to_base64url(options.challenge),
            "rp": {"id": options.rp.id, "name": options.rp.name},
            "user": {
                "id": bytes_to_base64url(options.user.id),
                "name": options.user.name,
                "displayName": options.user.display_name,
            },
            "pubKeyCredParams": [
                {"type": "public-key", "alg": p.alg} for p in options.pub_key_cred_params
            ],
            "timeout": options.timeout,
            "attestation": options.attestation,
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="WebAuthn no disponible en este servidor")


@router.post("/webauthn/register/finish")
def webauthn_register_finish(
    request: Request,
    body: dict,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verifica y guarda la credencial WebAuthn registrada."""
    try:
        from webauthn import verify_registration_response
        from webauthn.helpers import base64url_to_bytes

        challenge = _webauthn_challenges.pop(str(current_user.id), None)
        if not challenge:
            raise HTTPException(status_code=400, detail="Challenge expirado o no encontrado")

        verification = verify_registration_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id="localhost",
            expected_origin="http://localhost:8000",
        )

        # Guardar credencial en BD
        from app.models.usuarios import WebAuthnCredential
        cred = WebAuthnCredential(
            usuario_id=current_user.id,
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            nombre_dispositivo=body.get("device_name", "Dispositivo"),
        )
        db.add(cred)
        db.commit()

        return {"detail": "Credencial biométrica registrada exitosamente"}
    except ImportError:
        raise HTTPException(status_code=501, detail="WebAuthn no disponible")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error de verificación: {str(e)}")


@router.post("/webauthn/login/begin")
def webauthn_login_begin(body: WebAuthnBeginRequest, db: Session = Depends(get_db)):
    """Genera challenge para login con biometría."""
    try:
        from webauthn import generate_authentication_options
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor
        from webauthn.helpers import bytes_to_base64url

        # Buscar credenciales del usuario
        if not body.dni:
            raise HTTPException(status_code=400, detail="DNI requerido")

        usuario = db.execute(
            select(Usuario).where(Usuario.dni == body.dni, Usuario.activo == True)
        ).scalar_one_or_none()

        if not usuario:
            raise HTTPException(status_code=400, detail="Usuario no encontrado")

        from app.models.usuarios import WebAuthnCredential
        creds = db.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.usuario_id == usuario.id)
        ).scalars().all()

        if not creds:
            raise HTTPException(status_code=400, detail="No hay credenciales biométricas registradas")

        allow_credentials = [
            PublicKeyCredentialDescriptor(id=c.credential_id)
            for c in creds
        ]

        options = generate_authentication_options(
            rp_id="localhost",
            allow_credentials=allow_credentials,
        )

        _webauthn_challenges[str(usuario.id)] = options.challenge

        return {
            "challenge": bytes_to_base64url(options.challenge),
            "rpId": "localhost",
            "allowCredentials": [
                {"id": bytes_to_base64url(c.credential_id), "type": "public-key"}
                for c in creds
            ],
            "timeout": options.timeout,
            "user_id": str(usuario.id),
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="WebAuthn no disponible")


@router.post("/webauthn/login/finish")
def webauthn_login_finish(
    body: dict,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Verifica credencial WebAuthn y devuelve JWT."""
    try:
        from webauthn import verify_authentication_response
        from webauthn.helpers import base64url_to_bytes

        user_id = body.get("user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id requerido")

        challenge = _webauthn_challenges.pop(user_id, None)
        if not challenge:
            raise HTTPException(status_code=400, detail="Challenge expirado")

        # Buscar credencial en BD
        from app.models.usuarios import WebAuthnCredential
        credential_id = base64url_to_bytes(body["id"])
        cred = db.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
        ).scalar_one_or_none()

        if not cred:
            raise HTTPException(status_code=400, detail="Credencial no reconocida")

        verification = verify_authentication_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id="localhost",
            expected_origin="http://localhost:8000",
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
        )

        # Actualizar sign_count
        cred.sign_count = verification.new_sign_count
        db.commit()

        # Generar JWT igual que en /login
        usuario = db.execute(
            select(Usuario).where(Usuario.id == cred.usuario_id)
        ).scalar_one()

        ut = db.execute(
            select(UsuarioTenant).where(
                UsuarioTenant.usuario_id == usuario.id,
                UsuarioTenant.activo == True,
            )
        ).scalars().first()

        config = db.execute(
            select(ConfigUsuario).where(ConfigUsuario.usuario_id == usuario.id)
        ).scalar_one_or_none()

        token_data = {
            "sub": str(usuario.id),
            "tenant_id": str(ut.tenant_id) if ut else None,
            "empresa_activa_id": usuario.empresa_activa_id,
            "rol": ut.rol.value if ut else "solo_lectura",
            "tema": config.tema.value if config else "semi",
            "fuente_size": config.fuente_size.value if config else "md",
            "nombres": usuario.nombres,
            "apellidos": usuario.apellidos,
        }
        token = create_access_token(token_data)

        usuario.ultimo_acceso = datetime.now(timezone.utc)
        db.commit()

        response.set_cookie(key="token", value=token, httponly=True, samesite="lax", max_age=8*3600)

        return {"access_token": token, "token_type": "bearer"}
    except ImportError:
        raise HTTPException(status_code=501, detail="WebAuthn no disponible")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error de verificación: {str(e)}")


# --- Utilidades ---

def _registrar_auditoria(
    db: Session,
    usuario_id: uuid.UUID,
    accion: str,
    tabla: str,
    registro_id: str,
    valor_anterior: dict = None,
    valor_nuevo: dict = None,
    ip: str = None,
    user_agent: str = None,
):
    """Registra una acción en la tabla de auditoría."""
    registro = RegistroAuditoria(
        usuario_id=usuario_id,
        accion=accion,
        tabla=tabla,
        registro_id=registro_id,
        valor_anterior=valor_anterior,
        valor_nuevo=valor_nuevo,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(registro)
    db.commit()
