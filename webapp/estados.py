"""
webapp/estados.py — alerta.pe (zAlerta-18)
═══════════════════════════════════════════════════════════════════════
Estado REAL de vigilancia de un RUC, comunicado con HONESTIDAD. Reusa los
campos existentes (estado del contribuyente, credencial, ultimo_login_ok_at).
NUNCA decir "vigilado" si la conexión no está confirmada.

  - VIGILADO   (verde) → hay credencial y el último login real fue exitoso.
  - VERIFICANDO (ámbar)→ hay credencial pero aún sin confirmar.
  - PENDIENTE  (ámbar) → no hay credenciales (eligió "pido al contador" o no las puso).
  - ERROR      (rojo)  → la credencial dejó de servir (zAlerta-13).
"""

from __future__ import annotations

from models import EstadoContribuyente

# clase de color para la UI (mapea a estilos del semáforo/badges).
ESTADOS = {
    "vigilado": {
        "badge": "Vigilado", "clase": "verde",
        "titulo": "Tu buzón SUNAT está siendo vigilado",
        "mensaje": "Te avisaremos apenas llegue algo.",
        "protegido": True,
    },
    "verificando": {
        "badge": "Verificando", "clase": "ambar",
        "titulo": "Estamos verificando tu conexión",
        "mensaje": ("Te confirmaremos cuando tu buzón quede activo. Por ahora aún "
                    "no podemos garantizar el monitoreo."),
        "protegido": False,
    },
    "pendiente": {
        "badge": "Pendiente", "clase": "ambar",
        "titulo": "Aún NO estamos vigilando tu buzón",
        "mensaje": ("Necesitamos tus credenciales SOL (o las de tu contador) para "
                    "empezar. En cuanto conecten, te avisamos y arrancan tus 7 días "
                    "de prueba."),
        "protegido": False,
    },
    "error": {
        "badge": "Revisar clave", "clase": "rojo",
        "titulo": "No pudimos entrar a tu buzón",
        "mensaje": ("¿Cambiaste tu Clave SOL? Actualízala para seguir vigilando tu "
                    "buzón."),
        "protegido": False,
    },
}


def clave_estado_conexion(contrib, cred) -> str:
    """Devuelve la clave del estado ('vigilado'|'verificando'|'pendiente'|'error')."""
    if contrib is not None and contrib.estado == EstadoContribuyente.ERROR_CREDENCIAL:
        return "error"
    if cred is None:
        return "pendiente"
    if getattr(cred, "ultimo_login_ok_at", None) is not None:
        return "vigilado"
    return "verificando"


def estado_conexion(contrib, cred) -> dict:
    """Estado completo (clave + textos honestos) para mostrar en la UI."""
    clave = clave_estado_conexion(contrib, cred)
    return {"clave": clave, **ESTADOS[clave]}
