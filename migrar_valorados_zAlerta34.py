"""
migrar_valorados_zAlerta34.py — alerta.pe   (C:\\alertape\\...)
═══════════════════════════════════════════════════════════════════════
Migración SIN Alembic para zAlerta-34 (deuda valorada). Idempotente; correr
en prod ANTES de desplegar.

Crea 3 tablas:
  tributos              (catálogo de códigos de tributo SUNAT; se siembra)
  documentos_valorados  (cabecera del 2º PDF de deuda + gcs_key + pdf_texto)
  detalles_valorados    (líneas; las llena el parser de zAlerta-35)

Siembra `tributos` con códigos publicados por SUNAT (sin TIM ni tasas).

Uso:
    python migrar_valorados_zAlerta34.py
"""

from __future__ import annotations

import asyncio
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import text

from db import engine

# Catálogo SUNAT (códigos ciertos). NO incluye TIM ni tasas (no calculamos intereses).
TRIBUTOS = [
    ("1011", "IGV - Operaciones Internas", "igv"),
    ("1041", "IGV - Utilización de Servicios prestados por no domiciliados", "igv"),
    ("3011", "Renta de Primera Categoría", "renta"),
    ("3021", "Renta de Segunda Categoría", "renta"),
    ("3031", "Renta de Tercera Categoría - Cuenta Propia", "renta"),
    ("3041", "Renta de Cuarta Categoría", "renta"),
    ("3052", "Renta de Quinta Categoría - Retenciones", "renta"),
    ("3111", "Régimen Especial de Renta (RER)", "renta"),
    ("4131", "Nuevo Régimen Único Simplificado (Nuevo RUS)", "nrus"),
    ("5210", "ESSALUD - Seguro Regular Trabajadores", "essalud"),
    ("5310", "ONP - Sistema Nacional de Pensiones (D.L. 19990)", "onp"),
    ("7011", "ITAN - Impuesto Temporal a los Activos Netos", "itan"),
]


async def _crear() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tributos (
                codigo       VARCHAR(10) PRIMARY KEY,
                descripcion  VARCHAR(160) NOT NULL,
                tipo         VARCHAR(40)
            );"""))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS documentos_valorados (
                id                     UUID PRIMARY KEY,
                contribuyente_id       UUID NOT NULL REFERENCES contribuyentes(id) ON DELETE CASCADE,
                notificacion_id        UUID REFERENCES notificaciones(id) ON DELETE CASCADE,
                estudio_id             UUID REFERENCES estudios_contables(id) ON DELETE CASCADE,
                tipo_valorado          VARCHAR(30) NOT NULL,
                num_documento          VARCHAR(60),
                fecha_emision          DATE,
                fecha_notificacion     DATE,
                dependencia            VARCHAR(120),
                funcionario_emisor     VARCHAR(160),
                infraccion_descripcion TEXT,
                infraccion_base_legal  VARCHAR(200),
                importe                NUMERIC(14,2),
                interes                NUMERIC(14,2),
                monto_total            NUMERIC(14,2),
                num_resol_coactiva     VARCHAR(60),
                plazo_reclamo_dias     INTEGER,
                pdf_texto              TEXT,
                gcs_key                VARCHAR(300),
                parseado_ok            BOOLEAN NOT NULL DEFAULT false,
                parser_version         VARCHAR(20),
                parseado_at            TIMESTAMPTZ,
                creado_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                actualizado_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_valorado_notif UNIQUE (notificacion_id),
                CONSTRAINT uq_valorado_doc UNIQUE (contribuyente_id, num_documento, tipo_valorado)
            );"""))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_valorado_contrib "
            "ON documentos_valorados (contribuyente_id);"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS detalles_valorados (
                id                    UUID PRIMARY KEY,
                documento_valorado_id UUID NOT NULL REFERENCES documentos_valorados(id) ON DELETE CASCADE,
                periodo               VARCHAR(20),
                cod_tributo           VARCHAR(10) REFERENCES tributos(codigo),
                formulario            VARCHAR(20),
                num_declaracion       VARCHAR(40),
                base_referencia       VARCHAR(120),
                tasa_pct              NUMERIC(7,4),
                cod_multa             VARCHAR(20),
                monto_insoluto        NUMERIC(14,2),
                interes_linea         NUMERIC(14,2),
                total_linea           NUMERIC(14,2),
                fecha_infraccion      DATE
            );"""))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_detalle_valorado "
            "ON detalles_valorados (documento_valorado_id);"))


async def _sembrar() -> None:
    async with engine.begin() as conn:
        for codigo, desc, tipo in TRIBUTOS:
            await conn.execute(text(
                "INSERT INTO tributos (codigo, descripcion, tipo) "
                "VALUES (:c, :d, :t) ON CONFLICT (codigo) DO NOTHING"),
                {"c": codigo, "d": desc, "t": tipo})
    print(f"  ✓ catálogo tributos: {len(TRIBUTOS)} código(s) sembrado(s).")


async def main() -> None:
    print("→ Migrando zAlerta-34 (deuda valorada)…")
    await _crear()
    print("  ✓ tablas tributos / documentos_valorados / detalles_valorados listas.")
    await _sembrar()
    print("✓ Migración zAlerta-34 completa.")


if __name__ == "__main__":
    asyncio.run(main())
