# decl-carga — carga real de Declaraciones y Pagos (ESCRIBE en BD)

Rama `feat/decl-carga` (sobre `feat/decl-diag`). Construye la ingesta que faltaba:
lee la Consulta general (01) y la Consulta de DDJJ y pagos (02) de SOL y **persiste**
en `declaraciones_juradas` y `pagos_tributarios`.

## Qué hace (diseño acordado)

- **01 por período**, en **bloques ≤6 meses** (`--meses 12` → 2 bloques): declaró /
  pagó / importe + vigencia de rectificatorias. Fuente autoritativa de la rejilla.
- **02 por fecha de presentación**, misma cantidad de bloques ≤6 meses: detecta
  presentaciones **tardías** fuera de la ventana de 01 (insert-if-absent, no pisa a 01).
- **Detalle de Tributos Pagados** de cada boleta → `cod_tributo` fiscal real
  (best-effort: si falla, el pago igual se guarda con `cod_tributo` NULL).
- **Upsert idempotente por `(contribuyente_id, num_orden)`.** Correrlo de nuevo NO
  duplica. **Nunca borra**; solo inserta/actualiza.
- **Atómico por bloque:** si un bloque da `sin_acceso` / `error_filtro` /
  `sin_conexion` / `pantalla_de_aceptacion` / `timeout`, **no escribe nada de ese
  bloque** y lo reporta. Un bloque malo no arrastra a los demás.
- **Timeout por bloque** = 180 s (`DECL_CARGA_TIMEOUT_BLOQUE`).
- **Guardián de aceptación ACTIVO** (REGLA DURA SOL): ante una pantalla de
  "Declaro/acepto/condiciones" se aborta el bloque **sin hacer clic en nada**.
- **Corre a mano** (LOCAL con `railway run`), **nunca** en el worker.

## Comando — prueba con Shevalche primero

Con el worker **pausado** (para no chocar la sesión SOL) y `DATABASE_PUBLIC_URL`
en el entorno del worker (ya configurado para la migración):

```
railway run -s alertape-worker python run_scraper.py 20615643735 decl-carga --meses 12
```

Deberías ver primero `[decl-diag] Conectando por DATABASE_PUBLIC_URL…` (el mismo
gate del diag cubre decl-carga), luego el login y, por bloque, una línea:
`· bloque 202606: 01=con_datos 02=con_datos → N decl, M pagos`.

Luego, cuando valides Shevalche, los otros dos:

```
railway run -s alertape-worker python run_scraper.py 10467016283 decl-carga --meses 12   # Sara
railway run -s alertape-worker python run_scraper.py 20600488121 decl-carga --meses 12   # Oficinas & Depas
```

## Verificación (solo lectura)

```
python migrar_declaraciones.py check     # las 5 tablas + conteo
```
Y la consulta por RUC + rejilla 12 meses (SQL de solo lectura) que ya te pasé.

## Qué mirar / mándame si algo sale raro

El parser y la navegación están probados contra los volcados del diag, pero el
**Detalle de Tributos** (abrir `verDetalleTributos` de cada boleta y volver a la
grilla) es lo único que no pude validar contra SOL en vivo. Si ves `0 pagos` donde
esperabas pagos, o los pagos salen sin `cod_tributo`, pásame el log de la corrida
(está enmascarado: sin usuario SOL ni token) y lo afino. Variables útiles:

- `DECL_CARGA_TIMEOUT_BLOQUE` (def. 180): segundos por bloque.
- `DECL_CARGA_MAX_DETALLES` (def. 80): tope de detalles de tributo por bloque.
- `DECL_DIAG_PAUSA_SEG` (def. 4): pausa entre pantallas (compartida con el diag).

## Nota de ingeniería (merge)

Esta rama parte de `feat/decl-diag` porque la ingesta **depende** de su parser,
modelos y navegación. Trae además `guardian_aceptacion.py` (hoy solo en
`feat/comp-diag`) porque la REGLA DURA exige guardián en todos los modos. Cuando
mergees `feat/decl-diag` a `main` con un **merge normal** (no squash), el PR de
`feat/decl-carga → main` queda limpio (solo la ingesta). Si haces **squash**,
avísame y rebaseo `feat/decl-carga` sobre `main`.
