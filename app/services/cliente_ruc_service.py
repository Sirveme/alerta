"""Operaciones sobre clientes RUC del contador."""
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import encrypt_secret
from app.models import ClienteRuc, CredencialSol, Plan, Suscripcion, Usuario


async def contar_clientes_activos(db: AsyncSession, contador_id: int) -> int:
    """Cuenta clientes RUC del contador que NO son su propio estudio."""
    result = await db.execute(
        select(func.count(ClienteRuc.id))
        .where(ClienteRuc.contador_id == contador_id)
        .where(ClienteRuc.activo == True)
        .where(ClienteRuc.es_propio_estudio == False)
    )
    return result.scalar() or 0


async def obtener_plan_del_contador(db: AsyncSession, contador_id: int) -> Optional[Plan]:
    """Devuelve el Plan vigente del contador."""
    result = await db.execute(
        select(Plan)
        .join(Suscripcion, Suscripcion.plan_id == Plan.id)
        .where(Suscripcion.usuario_id == contador_id)
        .where(Suscripcion.activa == True)
    )
    return result.scalar_one_or_none()


async def puede_agregar_cliente(
    db: AsyncSession,
    contador_id: int,
    es_propio_estudio: bool,
) -> tuple[bool, str]:
    """Verifica si el contador puede agregar un cliente más.

    Returns:
        (puede_agregar, mensaje_si_no)
    """
    if es_propio_estudio:
        # El propio estudio no cuenta para límite
        return True, ""

    plan = await obtener_plan_del_contador(db, contador_id)
    if not plan:
        return False, "No tienes un plan activo. Contacta soporte."

    actuales = await contar_clientes_activos(db, contador_id)
    if actuales >= plan.max_clientes_ruc:
        return False, (
            f"Has alcanzado el límite de {plan.max_clientes_ruc} clientes RUC "
            f"de tu plan {plan.nombre}. Mejora tu plan para agregar más."
        )
    return True, ""


async def es_estudio_propio(usuario: Usuario, ruc: str) -> bool:
    """Detecta si el RUC corresponde al estudio del contador."""
    if usuario.ruc_propio and usuario.ruc_propio == ruc:
        return True
    return False


async def crear_cliente_ruc(
    db: AsyncSession,
    contador_id: int,
    ruc: str,
    razon_social: str,
    nombre_referencia: Optional[str],
    es_propio_estudio: bool,
    tipo_usuario_sol: int,
    dni_titular: Optional[str],
    usuario_sol: Optional[str],
    clave_sol: str,
) -> ClienteRuc:
    """Crea cliente RUC + credenciales encriptadas en una transacción."""
    cliente = ClienteRuc(
        contador_id=contador_id,
        ruc=ruc.strip(),
        razon_social=razon_social.strip(),
        nombre_referencia=(nombre_referencia or "").strip() or None,
        es_propio_estudio=es_propio_estudio,
        activo=True,
    )
    db.add(cliente)
    await db.flush()  # obtener cliente.id

    credencial = CredencialSol(
        cliente_ruc_id=cliente.id,
        tipo_usuario=tipo_usuario_sol,
        dni_encriptado=encrypt_secret(dni_titular or "") if dni_titular else None,
        usuario_sol_encriptado=encrypt_secret(usuario_sol or "") if usuario_sol else None,
        clave_sol_encriptada=encrypt_secret(clave_sol),
    )
    db.add(credencial)
    await db.commit()
    await db.refresh(cliente)
    return cliente


async def listar_clientes_del_contador(
    db: AsyncSession,
    contador_id: int,
) -> list[ClienteRuc]:
    """Lista clientes RUC del contador, ordenados (estudio propio primero)."""
    result = await db.execute(
        select(ClienteRuc)
        .where(ClienteRuc.contador_id == contador_id)
        .order_by(ClienteRuc.es_propio_estudio.desc(), ClienteRuc.razon_social.asc())
    )
    return list(result.scalars().all())


async def obtener_cliente(
    db: AsyncSession,
    cliente_id: int,
    contador_id: int,
) -> Optional[ClienteRuc]:
    """Obtiene cliente solo si pertenece al contador."""
    result = await db.execute(
        select(ClienteRuc)
        .where(ClienteRuc.id == cliente_id)
        .where(ClienteRuc.contador_id == contador_id)
        .options(selectinload(ClienteRuc.credencial))
    )
    return result.scalar_one_or_none()


async def eliminar_cliente(
    db: AsyncSession,
    cliente_id: int,
    contador_id: int,
) -> bool:
    """Elimina cliente RUC (cascade elimina credenciales y mensajes)."""
    cliente = await obtener_cliente(db, cliente_id, contador_id)
    if not cliente:
        return False
    await db.delete(cliente)
    await db.commit()
    return True
