"""
migrar_compras.py — alerta.pe  (proyecto vistas-club)
═══════════════════════════════════════════════════════════════════════
ADITIVA e IDEMPOTENTE. Crea SOLO tablas nuevas; NO toca nada existente.

Crea:
  · comprobantes_compra  — una cabecera por CPE recibido (RCE/SIRE): proveedor,
                           tipo/serie/número, bases e impuestos, total.
  · comprobante_items    — una fila por ítem del Detalle del CPE.

Coincide con los modelos ComprobanteCompra / ComprobanteItem de models.py.

⚠️  NO correr en prod sin el OK de Duilio (y con BACKUP). Se queda en la rama
    feat/vistas-club.
Uso (cuando se apruebe):
    python migrar_compras.py            # crea las tablas
    python migrar_compras.py check      # SOLO LECTURA: verifica que existen
"""
import asyncio
import os
import sys

# Para correr en LOCAL con `railway run`: el host interno de Railway no resuelve
# desde tu PC; si existe DATABASE_PUBLIC_URL, se usa esa. Debe ir ANTES de
# importar `db` (que lee DATABASE_URL).
if os.getenv("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]
    print("[migrar] Conectando por DATABASE_PUBLIC_URL...")

from sqlalchemy import text  # noqa: E402

from db import engine  # noqa: E402

TABLAS = ("comprobantes_compra", "comprobante_items")

DDL = [
    # ── comprobantes_compra (una cabecera por CPE recibido) ────────────
    """
    CREATE TABLE IF NOT EXISTS comprobantes_compra (
        id                      UUID PRIMARY KEY,
        contribuyente_id        UUID NOT NULL REFERENCES contribuyentes(id) ON DELETE CASCADE,
        estudio_id              UUID REFERENCES estudios_contables(id) ON DELETE CASCADE,
        periodo                 VARCHAR(6)  NOT NULL,
        cod_car                 VARCHAR(40),
        cod_tipo_cdp            VARCHAR(2)  NOT NULL,
        desc_tipo_cdp           VARCHAR(60),
        serie                   VARCHAR(20) NOT NULL,
        numero                  VARCHAR(20) NOT NULL,
        numero_final            VARCHAR(20),
        fecha_emision           DATE,
        fecha_vencimiento       DATE,
        tipo_doc_proveedor      VARCHAR(2),
        ruc_proveedor           VARCHAR(15) NOT NULL,
        razon_social_proveedor  VARCHAR(250),
        moneda                  VARCHAR(3)  DEFAULT 'PEN',
        tipo_cambio             NUMERIC(10,3),
        cod_estado              VARCHAR(4),
        estado                  VARCHAR(40),
        base_gravada            NUMERIC(14,2),
        igv                     NUMERIC(14,2),
        base_exon_inaf          NUMERIC(14,2),
        valor_adq_ng            NUMERIC(14,2),
        isc                     NUMERIC(14,2),
        icbper                  NUMERIC(14,2),
        otros_tributos          NUMERIC(14,2),
        importe_total           NUMERIC(14,2),
        ind_detraccion          BOOLEAN,
        ind_oper_gratuita       BOOLEAN,
        num_inconsistencias     INTEGER,
        origen                  VARCHAR(10) DEFAULT 'rce',
        fuente_hash             VARCHAR(64),
        parser_version          VARCHAR(20),
        creado_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
        actualizado_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_compra_cpe
            UNIQUE (contribuyente_id, ruc_proveedor, cod_tipo_cdp, serie, numero)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_compra_contrib_periodo ON comprobantes_compra (contribuyente_id, periodo)",
    "CREATE INDEX IF NOT EXISTS ix_compra_estudio ON comprobantes_compra (estudio_id)",
    "CREATE INDEX IF NOT EXISTS ix_compra_proveedor ON comprobantes_compra (ruc_proveedor)",

    # ── comprobante_items (una fila por ítem del Detalle) ──────────────
    """
    CREATE TABLE IF NOT EXISTS comprobante_items (
        id              UUID PRIMARY KEY,
        comprobante_id  UUID NOT NULL REFERENCES comprobantes_compra(id) ON DELETE CASCADE,
        linea           INTEGER NOT NULL,
        codigo          VARCHAR(40),
        descripcion     VARCHAR(300),
        unidad          VARCHAR(10),
        desc_unidad     VARCHAR(40),
        cantidad        NUMERIC(14,4),
        valor_unitario  NUMERIC(14,4),
        descuento       NUMERIC(14,2),
        icbper          NUMERIC(14,2),
        importe_total   NUMERIC(14,2),
        parser_version  VARCHAR(20),
        CONSTRAINT uq_compra_item_linea UNIQUE (comprobante_id, linea)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_compra_item_comprobante ON comprobante_items (comprobante_id)",
]


async def main():
    async with engine.begin() as conn:
        for sentencia in DDL:
            await conn.execute(text(sentencia))
        for tabla in TABLAS:
            n = await conn.scalar(text(f"SELECT count(*) FROM {tabla}"))
            print(f"  {tabla}: OK ({n} filas)")
    print("Migración de Compras lista (aditiva, idempotente).")
    await engine.dispose()


async def check():
    """SOLO LECTURA: verifica que las tablas existen y reporta su conteo. NO
    ejecuta DDL. Uso: python migrar_compras.py check"""
    async with engine.connect() as conn:
        for tabla in TABLAS:
            existe = await conn.scalar(text("SELECT to_regclass(:t)"),
                                       {"t": f"public.{tabla}"})
            if existe is None:
                print(f"  {tabla}: NO EXISTE")
                continue
            n = await conn.scalar(text(f"SELECT count(*) FROM {tabla}"))
            print(f"  {tabla}: existe, {n} fila(s)"
                  f"{'  ← vacía ✔' if n == 0 else ''}")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        asyncio.run(check())
    else:
        asyncio.run(main())
