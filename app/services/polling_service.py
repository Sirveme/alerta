"""Verifica el buzón SUNAT de un cliente RUC y guarda mensajes nuevos.

Usa el pool de sesiones SUNAT para reusar logins y evitar bloqueos.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.models import ClienteRuc, CredencialSol, LogPolling, MensajeBuzon
from app.services.sunat.session_pool import sunat_pool

logger = logging.getLogger(__name__)


def _parse_fecha_sunat(fecha_str: str) -> datetime | None:
    """Convierte 'dd/mm/aaaa' de SUNAT a datetime UTC."""
    if not fecha_str:
        return None
    try:
        dt = datetime.strptime(fecha_str.strip(), "%d/%m/%Y")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _decodificar_html_entities(texto: str) -> str:
    """Decodifica entidades HTML básicas que vienen de SUNAT (&Oacute;, &aacute;, etc)."""
    if not texto:
        return texto
    import html
    return html.unescape(texto)


async def verificar_buzon_cliente(
    db: AsyncSession,
    cliente: ClienteRuc,
    timeout_segundos: int = 60,
) -> dict[str, Any]:
    """Ejecuta polling del buzón SUNAT de un cliente.

    Returns:
        {
            "exito": bool,
            "mensajes_nuevos": int,
            "mensajes_total": int,
            "error": str | None,
            "sesion_reusada": bool,
        }
    """
    inicio = datetime.now(timezone.utc)
    log = LogPolling(
        cliente_ruc_id=cliente.id,
        inicio=inicio,
        exito=False,
        mensajes_nuevos=0,
    )

    # Obtener credenciales encriptadas
    cred_result = await db.execute(
        select(CredencialSol).where(CredencialSol.cliente_ruc_id == cliente.id)
    )
    credencial = cred_result.scalar_one_or_none()
    if not credencial:
        log.error_mensaje = "No hay credenciales SOL registradas"
        log.fin = datetime.now(timezone.utc)
        db.add(log)
        await db.commit()
        return {
            "exito": False,
            "mensajes_nuevos": 0,
            "mensajes_total": 0,
            "error": log.error_mensaje,
            "sesion_reusada": False,
        }

    # Desencriptar credenciales
    try:
        clave = decrypt_secret(credencial.clave_sol_encriptada)
        dni = decrypt_secret(credencial.dni_encriptado) if credencial.dni_encriptado else ""
        usuario_sol = decrypt_secret(credencial.usuario_sol_encriptado) if credencial.usuario_sol_encriptado else ""
    except Exception:
        logger.exception("Error desencriptando credenciales")
        log.error_mensaje = "Error desencriptando credenciales (revisar FERNET_KEY)"
        log.fin = datetime.now(timezone.utc)
        db.add(log)
        await db.commit()
        return {
            "exito": False,
            "mensajes_nuevos": 0,
            "mensajes_total": 0,
            "error": log.error_mensaje,
            "sesion_reusada": False,
        }

    # Obtener sesión del pool (reutiliza si está vigente)
    sesion = None
    sesion_reusada = False
    try:
        sesion, fue_nueva = sunat_pool.obtener_o_crear(
            ruc=cliente.ruc,
            tipo_usuario=credencial.tipo_usuario,
            dni=dni,
            usuario_sol=usuario_sol,
            clave_sol=clave,
            timeout_segundos=timeout_segundos,
        )
        sesion_reusada = not fue_nueva
    except RuntimeError as exc:
        # Cooldown activo, sesión en uso, etc.
        log.error_mensaje = str(exc)
        log.fin = datetime.now(timezone.utc)
        db.add(log)
        await db.commit()
        return {
            "exito": False,
            "mensajes_nuevos": 0,
            "mensajes_total": 0,
            "error": str(exc),
            "sesion_reusada": False,
        }
    except Exception as exc:
        # Error de login con SUNAT (timeout, credenciales, etc.)
        logger.exception(f"Error login pool RUC {cliente.ruc}")
        # Forzar limpieza por si quedó algo
        sunat_pool.descartar(cliente.ruc)
        error_msg = f"{type(exc).__name__}: {str(exc)[:300]}"
        credencial.ultima_verificacion = datetime.now(timezone.utc)
        credencial.ultimo_error = error_msg
        log.error_mensaje = error_msg
        log.fin = datetime.now(timezone.utc)
        db.add(log)
        await db.commit()
        return {
            "exito": False,
            "mensajes_nuevos": 0,
            "mensajes_total": 0,
            "error": error_msg,
            "sesion_reusada": False,
        }

    # Consultar buzón
    mensajes_nuevos_count = 0
    mensajes_total = 0

    try:
        cliente_sunat = sesion.cliente

        # Listar carpetas
        carpetas = cliente_sunat.listar_carpetas()

        for carpeta in carpetas:
            cod_carpeta = str(carpeta.get("codCarpeta", "00"))
            nombre_carpeta = _decodificar_html_entities(str(carpeta.get("nomCarpeta", "")))

            # Listar mensajes de la carpeta (page 1, suficiente para detectar nuevos)
            try:
                data = cliente_sunat.listar_mensajes(
                    cod_carpeta=cod_carpeta,
                    tipo_msj=2,
                    page=1,
                )
            except Exception as exc:
                logger.warning(f"Error listando carpeta {cod_carpeta} de RUC {cliente.ruc}: {exc}")
                continue

            rows = data.get("rows", []) or []
            mensajes_total += len(rows)

            for row in rows:
                codigo_msj = row.get("codMensaje")
                if not codigo_msj:
                    continue

                # ¿ya existe en BD?
                existe = await db.execute(
                    select(MensajeBuzon)
                    .where(MensajeBuzon.cliente_ruc_id == cliente.id)
                    .where(MensajeBuzon.codigo_mensaje == int(codigo_msj))
                )
                if existe.scalar_one_or_none():
                    continue

                # Nuevo mensaje
                nuevo = MensajeBuzon(
                    cliente_ruc_id=cliente.id,
                    codigo_mensaje=int(codigo_msj),
                    cod_carpeta=cod_carpeta,
                    nombre_carpeta=nombre_carpeta,
                    asunto=_decodificar_html_entities(str(row.get("desAsunto") or ""))[:1000],
                    fecha_envio_sunat=_parse_fecha_sunat(row.get("fecEnvio", "")),
                    tiene_adjunto=bool(row.get("cantidadArchAdj", 0) > 0),
                    cantidad_adjuntos=int(row.get("cantidadArchAdj") or 0),
                    fecha_detectado=datetime.now(timezone.utc),
                    visto=False,
                )
                db.add(nuevo)
                mensajes_nuevos_count += 1

        credencial.ultima_verificacion = datetime.now(timezone.utc)
        credencial.ultima_verificacion_exitosa = datetime.now(timezone.utc)
        credencial.ultimo_error = None

        log.exito = True
        log.mensajes_nuevos = mensajes_nuevos_count
        log.fin = datetime.now(timezone.utc)
        db.add(log)
        await db.commit()

        return {
            "exito": True,
            "mensajes_nuevos": mensajes_nuevos_count,
            "mensajes_total": mensajes_total,
            "error": None,
            "sesion_reusada": sesion_reusada,
        }

    except Exception as exc:
        logger.exception(f"Error consultando buzón RUC {cliente.ruc}")
        # Descartar sesión, probablemente venció
        sunat_pool.descartar(cliente.ruc)
        error_msg = f"{type(exc).__name__}: {str(exc)[:300]}"
        credencial.ultima_verificacion = datetime.now(timezone.utc)
        credencial.ultimo_error = error_msg
        log.error_mensaje = error_msg
        log.fin = datetime.now(timezone.utc)
        db.add(log)
        await db.commit()
        return {
            "exito": False,
            "mensajes_nuevos": 0,
            "mensajes_total": 0,
            "error": error_msg,
            "sesion_reusada": False,
        }
    finally:
        # Liberar sesión para próxima verificación (NO descartar, solo desocupar)
        if sesion:
            sunat_pool.liberar(cliente.ruc)
