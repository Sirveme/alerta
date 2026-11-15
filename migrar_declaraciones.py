"""
migrar_declaraciones.py — alerta.pe  (proyecto decl-diag, HTML-first)
═══════════════════════════════════════════════════════════════════════
ADITIVA e IDEMPOTENTE. Crea SOLO tablas nuevas; NO toca nada existente.

Crea:
  · declaraciones_juradas   — una presentación de DJ (0621…) por fila.
  · pagos_tributarios       — boletas/pagos (1662/1663…) con banco, tributo e importe.
  · declaraciones_tributos  — desglose por tributo (fase posterior: constancia PDF
                              o 'Detalle de Tributos Pagados' HTML).
  · cronograma_vencimientos — vencimientos SUNAT por período y último dígito de RUC
                              (estado "pagó fuera de plazo"; se carga de fuente
                              oficial cada año, NO hardcodeado).
  · padron_buenos_contribuyentes — RUCs del Régimen de Buenos Contribuyentes
                              (resuelve es_buen_contribuyente; se carga del padrón
                              SUNAT, diag-first).

Coincide con los modelos DeclaracionJurada / PagoTributario / DeclaracionTributo
de models.py.

⚠️  NO correr en prod sin el OK de Duilio. Se queda en la rama feat/decl-diag.
Uso (cuando se apruebe):
    python migrar_declaraciones.py
"""
import asyncio

from sqlalchemy import text

from db import engine

DDL = [
    # ── declaraciones_juradas ──────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS declaraciones_juradas (
        id                  UUID PRIMARY KEY,
        contribuyente_id    UUID NOT NULL REFERENCES contribuyentes(id) ON DELETE CASCADE,
        estudio_id          UUID REFERENCES estudios_contables(id) ON DELETE CASCADE,
        periodo             VARCHAR(6)  NOT NULL,
        cod_formulario      VARCHAR(10) NOT NULL,
        desc_formulario     VARCHAR(120),
        num_orden           VARCHAR(20) NOT NULL,
        fecha_presentacion  DATE,
        es_rectificatoria   BOOLEAN NOT NULL DEFAULT FALSE,
        es_vigente          BOOLEAN NOT NULL DEFAULT TRUE,
        importe_pagado      NUMERIC(14,2),
        origen              VARCHAR(2),
        fuente_hash         VARCHAR(64),
        parser_version      VARCHAR(20),
        creado_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        actualizado_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_declaracion_numorden UNIQUE (contribuyente_id, num_orden)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_declaraciones_juradas_contribuyente_id ON declaraciones_juradas (contribuyente_id)",
    "CREATE INDEX IF NOT EXISTS ix_declaraciones_juradas_estudio_id ON declaraciones_juradas (estudio_id)",
    "CREATE INDEX IF NOT EXISTS ix_declaracion_contrib_periodo ON declaraciones_juradas (contribuyente_id, periodo)",
    "CREATE INDEX IF NOT EXISTS ix_declaracion_vigente ON declaraciones_juradas (contribuyente_id, periodo, es_vigente)",

    # ── pagos_tributarios ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS pagos_tributarios (
        id                  UUID PRIMARY KEY,
        contribuyente_id    UUID NOT NULL REFERENCES contribuyentes(id) ON DELETE CASCADE,
        estudio_id          UUID REFERENCES estudios_contables(id) ON DELETE CASCADE,
        periodo             VARCHAR(6)  NOT NULL,
        cod_formulario      VARCHAR(10),
        desc_formulario     VARCHAR(120),
        num_orden           VARCHAR(20) NOT NULL,
        fecha_pago          DATE,
        banco_receptor      VARCHAR(60),
        cod_tributo         VARCHAR(10) REFERENCES tributos(codigo),
        importe             NUMERIC(14,2),
        declaracion_id      UUID REFERENCES declaraciones_juradas(id) ON DELETE SET NULL,
        origen              VARCHAR(2),
        fuente_hash         VARCHAR(64),
        parser_version      VARCHAR(20),
        creado_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        actualizado_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_pago_numorden UNIQUE (contribuyente_id, num_orden)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pagos_tributarios_contribuyente_id ON pagos_tributarios (contribuyente_id)",
    "CREATE INDEX IF NOT EXISTS ix_pagos_tributarios_estudio_id ON pagos_tributarios (estudio_id)",
    "CREATE INDEX IF NOT EXISTS ix_pagos_tributarios_declaracion_id ON pagos_tributarios (declaracion_id)",
    "CREATE INDEX IF NOT EXISTS ix_pago_contrib_periodo ON pagos_tributarios (contribuyente_id, periodo)",

    # ── declaraciones_tributos (fase posterior) ────────────────────────
    """
    CREATE TABLE IF NOT EXISTS declaraciones_tributos (
        id                  UUID PRIMARY KEY,
        declaracion_id      UUID REFERENCES declaraciones_juradas(id) ON DELETE CASCADE,
        pago_id             UUID REFERENCES pagos_tributarios(id) ON DELETE CASCADE,
        periodo_tributo     VARCHAR(6),
        cod_tributo         VARCHAR(10) REFERENCES tributos(codigo),
        desc_tributo        VARCHAR(160),
        importe_declarado   NUMERIC(14,2),
        importe_pagado      NUMERIC(14,2),
        origen              VARCHAR(20),
        parser_version      VARCHAR(20)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_decltrib_decl ON declaraciones_tributos (declaracion_id)",
    "CREATE INDEX IF NOT EXISTS ix_decltrib_pago ON declaraciones_tributos (pago_id)",
    "CREATE INDEX IF NOT EXISTS ix_decltrib_periodo_trib ON declaraciones_tributos (periodo_tributo, cod_tributo)",

    # ── cronograma_vencimientos (estado "pagó fuera de plazo") ─────────
    # Catálogo GLOBAL (no por contribuyente): por período y último dígito de RUC,
    # la fecha de vencimiento oficial. Se CARGA de la fuente SUNAT cada año
    # (ver decl_diag_PAGO_FUERA_DE_PLAZO.md). NO hardcodeado.
    """
    CREATE TABLE IF NOT EXISTS cronograma_vencimientos (
        id                  UUID PRIMARY KEY,
        anio                INTEGER     NOT NULL,
        periodo             VARCHAR(6)  NOT NULL,
        digito_ruc          INTEGER     NOT NULL,
        grupo               VARCHAR(24) NOT NULL DEFAULT 'general',
        fecha_vencimiento   DATE        NOT NULL,
        num_resolucion      VARCHAR(60),
        fuente              VARCHAR(300),
        validado_pdf_at     TIMESTAMPTZ,
        cargado_at          TIMESTAMPTZ,
        CONSTRAINT uq_cronograma_periodo_digito UNIQUE (periodo, digito_ruc, grupo)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cronograma_periodo ON cronograma_vencimientos (periodo)",

    # ── padron_buenos_contribuyentes (resuelve es_buen_contribuyente) ──
    # Lista oficial de RUCs del Régimen de Buenos Contribuyentes. Se carga del
    # padrón SUNAT (diag-first). Si un RUC no está → regla conservadora.
    """
    CREATE TABLE IF NOT EXISTS padron_buenos_contribuyentes (
        id              UUID PRIMARY KEY,
        ruc             VARCHAR(11) NOT NULL,
        grupo           VARCHAR(24) NOT NULL DEFAULT 'buenos_contribuyentes',
        vigente         BOOLEAN NOT NULL DEFAULT TRUE,
        vigencia_desde  DATE,
        vigencia_hasta  DATE,
        fuente          VARCHAR(300),
        cargado_at      TIMESTAMPTZ,
        CONSTRAINT uq_padron_bc_ruc UNIQUE (ruc)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_padron_bc_vigente ON padron_buenos_contribuyentes (vigente)",
]


async def main():
    async with engine.begin() as conn:
        for sentencia in DDL:
            await conn.execute(text(sentencia))
        # Verificación.
        for tabla in ("declaraciones_juradas", "pagos_tributarios",
                      "declaraciones_tributos", "cronograma_vencimientos",
                      "padron_buenos_contribuyentes"):
            n = await conn.scalar(text(f"SELECT count(*) FROM {tabla}"))
            print(f"  {tabla}: OK ({n} filas)")
    print("Migración de Declaraciones y Pagos lista (aditiva, idempotente).")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
