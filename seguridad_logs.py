"""
seguridad_logs.py — alerta.pe   (raíz, junto al motor)
═══════════════════════════════════════════════════════════════════════
Enmascarado de datos sensibles en logs. Vive en la RAÍZ para que lo importen
el worker, el scraper, run_scraper y el vigilante sin acoplar webapp/.

Regla: NUNCA imprimir en claro un token de sesión (token=/jwt=), la Clave SOL,
el usuario SOL ni un DNI. `enmascarar()` se aplica en el SINK de cada log().

No depende de nada del proyecto (solo re) → seguro de importar en cualquier proc.
"""
from __future__ import annotations

import re

# token=... / clave=... / claveSol=... / password=... / state=... / code=... en
# URLs o texto (hasta separador &, espacio, comilla o fin). Case-insensitive.
# `state` y `code` son parámetros del OAuth de SOL: nunca deben loguearse enteros.
_RE_PARAM = re.compile(
    r"(?i)\b(token|jwt|clave|clavesol|claveSOL|password|passwd|pwd|pass|auth|secret|key|state|code)"
    r"(\s*[=:]\s*)([^\s&\"'}>]{2,})")
# DNI = 8 dígitos exactos, NO embebidos en un número más largo (el RUC de 11
# dígitos SÍ puede loguearse: es público e identifica al contribuyente).
_RE_DNI = re.compile(r"(?<!\d)(\d{8})(?!\d)")
# Email → deja la 1ª letra y el dominio.
_RE_EMAIL = re.compile(r"(?i)\b([A-Z0-9._%+-])[A-Z0-9._%+-]*(@[A-Z0-9.-]+\.[A-Z]{2,})\b")


def enmascarar(texto) -> str:
    """Devuelve el texto con token/clave/usuario SOL/DNI/email enmascarados."""
    if texto is None:
        return ""
    s = str(texto)
    s = _RE_PARAM.sub(lambda m: f"{m.group(1)}{m.group(2)}***", s)
    s = _RE_DNI.sub(lambda m: m.group(1)[:2] + "******", s)
    s = _RE_EMAIL.sub(lambda m: m.group(1) + "***" + m.group(2), s)
    return s


def enmascarar_usuario_sol(usuario_sol: str | None) -> str:
    """Un usuario SOL nunca debe verse entero. Deja RUC (si lo prefija) + ***."""
    if not usuario_sol:
        return "***"
    u = str(usuario_sol)
    # Si empieza con el RUC (11 díg) es público; oculta solo el usuario tras él.
    m = re.match(r"^(\d{11})(.*)$", u)
    if m:
        return m.group(1) + "/***"
    return (u[:2] + "***") if len(u) > 2 else "***"
