"""
agents/agente_consultor.py — Agente principal de consultas con OpenAI function calling.

El agente recibe el texto del usuario y decide qué función llamar.
Cada función es un query real a la base de datos.
El agente NO tiene acceso directo a la BD — solo llama funciones del sistema.

Modelo: gpt-4o-mini (suficiente para interpretación de intenciones)
Temperatura: 0.1 (queremos consistencia, no creatividad)
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Eres el asistente contable de alerta.pe. Ayudas a contadores peruanos a consultar
información financiera y tributaria de sus clientes empresariales.

CONTEXTO ACTUAL:
- Empresa activa: {nombre_empresa} (RUC: {ruc_empresa})
- Período activo: {periodo}
- Usuario: {nombre_usuario} ({rol})
- Fecha y hora: {datetime_peru}

REGLAS:
1. Siempre habla en español peruano natural, tono {tono_ia}
2. Nunca pidas el RUC — ya sabes qué empresa está activa
3. Si la consulta es ambigua, asume el período activo
4. Respuestas de voz: máximo 3 oraciones. Conciso y directo.
5. Si no tienes suficiente información para responder con certeza, dilo claramente
6. Montos siempre en soles (S/) salvo que el comprobante sea en dólares
7. Fechas en formato peruano: dd/mm/yyyy
"""

FUNCIONES = [
    {
        "type": "function",
        "function": {
            "name": "consultar_pagos_periodo",
            "description": "Consulta pagos recibidos en un período. Retorna total, desglose por canal y lista.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mes": {"type": "integer", "description": "Mes (1-12). Default: mes actual."},
                    "anio": {"type": "integer", "description": "Año. Default: año actual."},
                    "canal": {"type": "string", "enum": ["yape", "plin", "bcp", "bbva", "interbank", "todos"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_comprobantes_pendientes",
            "description": "Lista comprobantes emitidos que aún no tienen pago registrado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mes": {"type": "integer"},
                    "anio": {"type": "integer"},
                    "tipo": {"type": "string", "enum": ["facturas", "boletas", "todos"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_compras_por_producto",
            "description": "Busca compras de un producto o categoría. Ej: ¿cuánto aceite compramos?",
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {"type": "string", "description": "Nombre o parte del nombre del producto"},
                    "mes_inicio": {"type": "integer"},
                    "mes_fin": {"type": "integer"},
                    "anio": {"type": "integer"},
                },
                "required": ["producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_diferencia_sire",
            "description": "Compara comprobantes del sistema vs. SIRE SUNAT. Detecta facturas no declaradas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mes": {"type": "integer"},
                    "anio": {"type": "integer"},
                    "tipo_registro": {"type": "string", "enum": ["ventas", "compras", "ambos"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_alertas_activas",
            "description": "Lista alertas no leídas de la empresa, priorizando urgentes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nivel": {"type": "string", "enum": ["urgente", "importante", "info", "todas"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resumen_empresa",
            "description": "Dashboard resumido: cobrado, pendiente, alertas, anomalías del período.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mes": {"type": "integer"},
                    "anio": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cambiar_empresa_activa",
            "description": "Cambia la empresa activa por nombre o RUC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre_o_ruc": {"type": "string", "description": "Nombre parcial o RUC completo"},
                },
                "required": ["nombre_o_ruc"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_pagos_sin_comprobante",
            "description": "Lista pagos recibidos sin comprobante asociado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mes": {"type": "integer"},
                    "anio": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
]


async def ejecutar_consulta(
    db: Session,
    empresa_id: int,
    texto: str,
    nombre_empresa: str = "",
    ruc_empresa: str = "",
    nombre_usuario: str = "",
    rol: str = "contador",
    tono: str = "directo",
) -> dict:
    """
    Pipeline completo: texto → agente → función → respuesta formateada.
    """
    from openai import OpenAI
    from app.core.config import settings

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    now = datetime.now(timezone.utc)

    system_prompt = SYSTEM_PROMPT.format(
        nombre_empresa=nombre_empresa,
        ruc_empresa=ruc_empresa,
        periodo=f"{now.month:02d}/{now.year}",
        nombre_usuario=nombre_usuario,
        rol=rol,
        datetime_peru=now.strftime("%d/%m/%Y %H:%M"),
        tono_ia=tono,
    )

    # Paso 1: Llamar al modelo con function calling
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": texto},
        ],
        tools=FUNCIONES,
        tool_choice="auto",
    )

    message = response.choices[0].message

    # Paso 2: Si el modelo quiere llamar una función
    if message.tool_calls:
        tool_call = message.tool_calls[0]
        nombre_fn = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

        logger.info(f"Agente consultor: función={nombre_fn}, args={args}")

        # Ejecutar función
        resultado_fn = await ejecutar_funcion(nombre_fn, args, db, empresa_id)

        # Paso 3: Enviar resultado al modelo para que genere respuesta natural
        response2 = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": texto},
                message,
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(resultado_fn, ensure_ascii=False, default=str),
                },
            ],
        )

        respuesta_texto = response2.choices[0].message.content

        # Detectar si se cambió de empresa
        empresa_cambiada = nombre_fn == "cambiar_empresa_activa" and resultado_fn.get("exito")

        return {
            "respuesta_texto": respuesta_texto,
            "respuesta_display": respuesta_texto,
            "accion": resultado_fn.get("accion"),
            "datos": resultado_fn.get("datos"),
            "empresa_cambiada": empresa_cambiada,
            "nueva_empresa_id": resultado_fn.get("nueva_empresa_id"),
            "confianza_interpretacion": "alta",
            "intencion": nombre_fn,
            "parametros": args,
        }

    # Sin function calling — respuesta directa del modelo
    return {
        "respuesta_texto": message.content or "No entendí tu consulta. ¿Podrías repetirla?",
        "respuesta_display": message.content,
        "accion": None,
        "datos": None,
        "empresa_cambiada": False,
        "nueva_empresa_id": None,
        "confianza_interpretacion": "media",
        "intencion": "respuesta_directa",
        "parametros": {},
    }


async def ejecutar_funcion(nombre: str, args: dict, db: Session, empresa_id: int) -> dict:
    """Dispatcher que mapea nombre de función a query real."""
    funciones_map = {
        "consultar_pagos_periodo": _fn_pagos_periodo,
        "consultar_comprobantes_pendientes": _fn_comprobantes_pendientes,
        "buscar_compras_por_producto": _fn_compras_por_producto,
        "consultar_diferencia_sire": _fn_diferencia_sire,
        "consultar_alertas_activas": _fn_alertas_activas,
        "resumen_empresa": _fn_resumen_empresa,
        "cambiar_empresa_activa": _fn_cambiar_empresa,
        "buscar_pagos_sin_comprobante": _fn_pagos_sin_comprobante,
    }

    fn = funciones_map.get(nombre)
    if not fn:
        return {"error": f"Función desconocida: {nombre}"}

    return await fn(db, empresa_id, args)


# ── Funciones ejecutoras ─────────────────────────────────────

async def _fn_pagos_periodo(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.pagos import Pago

    now = datetime.now(timezone.utc)
    mes = args.get("mes", now.month)
    anio = args.get("anio", now.year)

    query = select(Pago).where(
        Pago.empresa_id == empresa_id,
        func.extract("month", Pago.fecha_pago) == mes,
        func.extract("year", Pago.fecha_pago) == anio,
        Pago.deleted_at == None,
    )

    pagos = db.execute(query).scalars().all()
    total = sum(p.monto for p in pagos)

    # Desglose por canal
    canales = {}
    for p in pagos:
        c = p.canal.value
        canales[c] = canales.get(c, 0) + float(p.monto)

    return {
        "datos": {
            "total": float(total),
            "cantidad": len(pagos),
            "mes": mes,
            "anio": anio,
            "canales": canales,
        },
        "resumen_texto": f"Total cobrado en {mes:02d}/{anio}: S/ {total:,.2f} ({len(pagos)} pagos)",
    }


async def _fn_comprobantes_pendientes(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.comprobantes import Comprobante, EstadoComprobante

    query = select(Comprobante).where(
        Comprobante.empresa_id == empresa_id,
        Comprobante.estado == EstadoComprobante.PENDIENTE,
        Comprobante.deleted_at == None,
    ).order_by(Comprobante.fecha_emision.desc()).limit(20)

    comps = db.execute(query).scalars().all()
    total = sum(c.total for c in comps)

    return {
        "datos": {
            "total_pendiente": float(total),
            "cantidad": len(comps),
            "items": [
                {"serie": c.serie, "correlativo": c.correlativo, "total": float(c.total),
                 "emisor": c.ruc_emisor, "fecha": str(c.fecha_emision)}
                for c in comps[:10]
            ],
        },
        "resumen_texto": f"Hay {len(comps)} comprobantes pendientes por S/ {total:,.2f}",
    }


async def _fn_compras_por_producto(db: Session, empresa_id: int, args: dict) -> dict:
    """Búsqueda de compras por producto usando GIN trigram en comprobante_detalle."""
    from app.models.comprobantes import Comprobante, DetalleComprobante

    producto = args.get("producto", "")
    now = datetime.now(timezone.utc)
    anio = args.get("anio", now.year)
    mes_inicio = args.get("mes_inicio", 1)
    mes_fin = args.get("mes_fin", 12)

    # Búsqueda por ILIKE (funciona sin trigram index también, pero más lento)
    detalles = db.execute(
        select(DetalleComprobante, Comprobante).join(
            Comprobante, DetalleComprobante.comprobante_id == Comprobante.id
        ).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.deleted_at == None,
            func.extract("year", Comprobante.fecha_emision) == anio,
            func.extract("month", Comprobante.fecha_emision) >= mes_inicio,
            func.extract("month", Comprobante.fecha_emision) <= mes_fin,
            DetalleComprobante.descripcion.ilike(f"%{producto}%"),
        )
    ).all()

    total = sum(d[0].total_linea for d in detalles)
    cantidad = sum(d[0].cantidad for d in detalles)

    return {
        "datos": {
            "producto_buscado": producto,
            "total_comprado": float(total),
            "cantidad_items": len(detalles),
            "cantidad_unidades": float(cantidad),
            "periodo": f"{mes_inicio:02d}/{anio} a {mes_fin:02d}/{anio}",
        },
        "resumen_texto": f"Compraste '{producto}' por S/ {total:,.2f} ({len(detalles)} líneas) entre {mes_inicio:02d} y {mes_fin:02d}/{anio}",
    }


async def _fn_diferencia_sire(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.acumulados import AcumSIRE
    from app.models.comprobantes import Comprobante

    now = datetime.now(timezone.utc)
    mes = args.get("mes", now.month)
    anio = args.get("anio", now.year)
    periodo = f"{anio}-{mes:02d}"

    # Total en sistema
    total_sistema = db.execute(
        select(func.coalesce(func.sum(Comprobante.total), 0)).where(
            Comprobante.empresa_id == empresa_id,
            func.extract("month", Comprobante.fecha_emision) == mes,
            func.extract("year", Comprobante.fecha_emision) == anio,
            Comprobante.deleted_at == None,
        )
    ).scalar()

    # Total en SIRE
    sire = db.execute(
        select(AcumSIRE).where(
            AcumSIRE.empresa_id == empresa_id,
            AcumSIRE.periodo == periodo,
        )
    ).scalars().all()

    total_sire = sum(s.total for s in sire)
    diferencia = float(total_sistema) - float(total_sire)

    return {
        "datos": {
            "periodo": periodo,
            "total_sistema": float(total_sistema),
            "total_sire": float(total_sire),
            "diferencia": diferencia,
        },
        "resumen_texto": (
            f"Sistema: S/ {float(total_sistema):,.2f} vs SIRE: S/ {float(total_sire):,.2f}. "
            f"Diferencia: S/ {diferencia:,.2f}"
            if total_sire else f"No hay datos SIRE para {periodo}. Total en sistema: S/ {float(total_sistema):,.2f}"
        ),
    }


async def _fn_alertas_activas(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.alertas import Alerta, EstadoAlerta

    query = select(Alerta).where(
        Alerta.empresa_id == empresa_id,
        Alerta.estado == EstadoAlerta.ACTIVA,
        Alerta.deleted_at == None,
    ).order_by(Alerta.created_at.desc()).limit(10)

    alertas = db.execute(query).scalars().all()

    return {
        "datos": {
            "cantidad": len(alertas),
            "items": [
                {"id": a.id, "titulo": a.titulo, "origen": a.origen.value, "created_at": str(a.created_at)}
                for a in alertas
            ],
        },
        "resumen_texto": (
            f"Hay {len(alertas)} alertas activas. La más reciente: {alertas[0].titulo}"
            if alertas else "No hay alertas pendientes. Todo en orden."
        ),
    }


async def _fn_resumen_empresa(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.pagos import Pago, EstadoPago
    from app.models.comprobantes import Comprobante, EstadoComprobante
    from app.models.alertas import Alerta, EstadoAlerta

    now = datetime.now(timezone.utc)
    mes = args.get("mes", now.month)
    anio = args.get("anio", now.year)

    cobrado = db.execute(
        select(func.coalesce(func.sum(Pago.monto), 0)).where(
            Pago.empresa_id == empresa_id,
            Pago.estado == EstadoPago.CRUZADO,
            func.extract("month", Pago.fecha_pago) == mes,
            func.extract("year", Pago.fecha_pago) == anio,
            Pago.deleted_at == None,
        )
    ).scalar()

    pendiente = db.execute(
        select(func.coalesce(func.sum(Comprobante.total), 0)).where(
            Comprobante.empresa_id == empresa_id,
            Comprobante.estado == EstadoComprobante.PENDIENTE,
            Comprobante.deleted_at == None,
        )
    ).scalar()

    alertas = db.execute(
        select(func.count(Alerta.id)).where(
            Alerta.empresa_id == empresa_id,
            Alerta.estado == EstadoAlerta.ACTIVA,
            Alerta.deleted_at == None,
        )
    ).scalar()

    return {
        "datos": {
            "cobrado": float(cobrado),
            "pendiente": float(pendiente),
            "alertas": alertas,
            "mes": mes,
            "anio": anio,
        },
        "resumen_texto": (
            f"En {mes:02d}/{anio}: cobrado S/ {float(cobrado):,.2f}, "
            f"pendiente S/ {float(pendiente):,.2f}, {alertas} alertas activas."
        ),
    }


async def _fn_cambiar_empresa(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.empresas import EmpresaCliente

    nombre_o_ruc = args.get("nombre_o_ruc", "")
    empresa = db.execute(
        select(EmpresaCliente).where(
            EmpresaCliente.deleted_at == None,
            (EmpresaCliente.ruc == nombre_o_ruc) |
            (EmpresaCliente.razon_social.ilike(f"%{nombre_o_ruc}%")),
        )
    ).scalar_one_or_none()

    if not empresa:
        return {
            "exito": False,
            "resumen_texto": f"No encontré ninguna empresa con '{nombre_o_ruc}'",
        }

    return {
        "exito": True,
        "nueva_empresa_id": empresa.id,
        "accion": f"cambiar_empresa:{empresa.id}",
        "datos": {"id": empresa.id, "nombre": empresa.razon_social, "ruc": empresa.ruc},
        "resumen_texto": f"Cambiando a {empresa.razon_social} (RUC {empresa.ruc})",
    }


async def _fn_pagos_sin_comprobante(db: Session, empresa_id: int, args: dict) -> dict:
    from app.models.pagos import Pago, EstadoPago

    pagos = db.execute(
        select(Pago).where(
            Pago.empresa_id == empresa_id,
            Pago.estado == EstadoPago.SIN_COMPROBANTE,
            Pago.deleted_at == None,
        ).order_by(Pago.fecha_pago.desc()).limit(20)
    ).scalars().all()

    total = sum(p.monto for p in pagos)

    return {
        "datos": {
            "total": float(total),
            "cantidad": len(pagos),
            "items": [
                {"id": p.id, "monto": float(p.monto), "canal": p.canal.value,
                 "fecha": str(p.fecha_pago), "pagador": p.pagador_nombre}
                for p in pagos[:10]
            ],
        },
        "resumen_texto": f"Hay {len(pagos)} pagos sin comprobante por S/ {total:,.2f}",
    }
