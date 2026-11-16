# vistas-club — Compras + vistas para video (Prompt B)

Rama `feat/vistas-club` (desde `main`). **Sin merge sin OK de Duilio.** Las
migraciones van en archivo y las corre Duilio con backup.

Objetivo: mostrar **Declaraciones** y **Compras** con descripción, listo para
grabar videos, con el **Club de Contadores como protagonista** y "con alerta.pe"
en pequeño. Tema oscuro (nunca fondo `#ffffff`).

## Estado

### ✅ Construido y probado (base, no depende de decisiones)
- **Modelos** (`models.py`): `ComprobanteCompra` (cabecera por CPE) + `ComprobanteItem`
  (una fila por ítem). UUID PK, multi-tenant (`contribuyente_id`/`estudio_id`),
  idempotente por `(contribuyente, proveedor, tipo, serie, número)` y por
  `(comprobante, línea)`.
- **Migración en archivo** (`migrar_compras.py`): aditiva/idempotente, con modo
  `check` (solo lectura). **La corres tú con backup.**
- **Parser puro** (`parser_compras.py`): `parse_lista_compras` (propuesta RCE,
  paginada) + `parse_detalle_items` (ítems del Detalle, limpia la basura `@#@` de
  `desItem`). Probado contra los volcados `comp_diag_*`: 20 cabeceras/pág,
  23/24 detalles OK (1 volcado corrupto, no el parser), 111 ítems, cruce lista↔detalle
  por clave correcto.

### ✅ Construido (decisión 1A — ingesta real)
- **`ingesta_compras.py`** — modo `compras-carga <RUC> <AAAAMM>`: login SOL → abre
  SIRE Compras (menú `60.2.2.1.1`) → **captura el bearer de la red** → pagina la
  propuesta RCE (endpoint confirmado) → **upsert idempotente** en `comprobantes_compra`.
  Guardián ACTIVO (aborta pantallas de aceptación sin tocar nada). PARCIAL si algo falla.
- **Wiring en `run_scraper.py`**: `compras-carga` + período AAAAMM, mismo gate
  `DATABASE_PUBLIC_URL` que decl-diag; log `COMPRAS-CARGA OK`/`PARCIAL`.
- `guardian_aceptacion.py` traído a la rama (la REGLA DURA exige guardián en todos
  los modos; en `main` no estaba).
- **Ítems (Detalle api-cpe)**: el código los baja y los persiste en `comprobante_items`,
  pero **solo si está definido `COMPRAS_DETALLE_CPE_URL`** (ver decisión abajo): la URL
  exacta del detalle no quedó en ningún artefacto del diag. Sin esa URL, `compras-carga`
  escribe **solo las cabeceras** y marca los ítems como pendientes (PARCIAL).

Comando (worker pausado, con `DATABASE_PUBLIC_URL`, tras correr la migración):
```
railway run -s alertape-worker python run_scraper.py 20615643735 compras-carga 202610
```

Variables de entorno (ingesta):
- `COMPRAS_DETALLE_CPE_URL` — URL del detalle api-cpe (placeholders: `{ruc}` `{periodo}`
  `{ruc_prov}` `{tipo}` `{serie}` `{numero}`). Si falta, no baja ítems.
- `COMPRAS_CODE_SIRE` (def. `60.2.2.1.1`), `COMPRAS_CODE_CPE` (def. `11.38.1.1.1`),
  `COMPRAS_PER_PAGE` (def. 20), `COMPRAS_MAX_PAGINAS` (def. 50),
  `COMPRAS_TIMEOUT_BEARER` (def. 40 s).

### ⏳ Pendiente (depende de las 2 decisiones de abajo)
- **Endpoint api-cpe del detalle** (para cargar ítems completos) — ver DECISIÓN 3.
- Vista A: rejilla 12 meses por cliente (declaró/pagó/pagó fuera de plazo/sin DJ).
- Vista B: Compras del período (una fila por ítem, filtros proveedor+texto, total,
  botón "Descargar Excel").
- "Modo demostración" (enmascara RUC/nombre cliente, RUC/razón proveedores, montos
  opcional) persistente mientras se graba, con Excel también enmascarado si está activo.
- Marca "Club de Contadores" (hoy no existe en las plantillas; se introduce aquí).

## Hallazgos que fijan el diseño
- **Compras = RCE/SIRE "propuesta"** (solo CONSULTA, REGLA DURA SOL). Endpoint real:
  `api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros/rce/propuesta/web/propuesta/{periodo}/busqueda`
  (paginado `perPage≈20`). El detalle de ítems es otra llamada por CPE.
- **Los volcados existentes cubren solo 202610** (Oficinas y Shevalche con detalle;
  Sara sin lista). **202609 NO está volcado** → requiere consultar SOL.
- **Rejilla 12 meses**: se arma cruzando lo de Declaraciones ya en BD
  (`declaraciones_juradas` + `pagos_tributarios`) con `cronograma.clasificar_pago`
  (estados PAGO_DENTRO / PAGO_FUERA / SIN_PAGO / SIN_VENCIMIENTO). **Depende de que
  `decl-carga` haya corrido** para esos RUC (hoy solo Shevalche, parcial; el PR
  feat/decl-carga aún no se mergea).
- **Tema oscuro**: tokens en `webapp/static/css/app.css` (`--fondo #0E1117`,
  `--superficie #161B24`, …), `data-color="dark"` por defecto. Las vistas nuevas usan
  `var(--fondo)`/`var(--superficie)`, nunca `#fff`.
- **Excel**: `excel_export.py` (openpyxl, maduro) + patrón `webapp/routers/exportar.py`.
- **Enmascarado**: hoy solo existe para LOGS (`seguridad_logs.py`); el enmascarado de
  nombres/montos para pantalla y el "modo demostración" **se construyen desde cero**.

## DECISIONES (copiar/pegar)

### 1) Compras — ¿de dónde saco los datos? (202609 falta en los volcados)
```
A) Ingesta real `compras-carga <RUC> <periodo>` que consulta el SIRE RCE propuesta
   por SOL (como decl-carga: login_sol, API api-sire, parser, upsert idempotente,
   corre a mano). Sirve para 202609, 202610 y cualquier período. (Recomendada:
   es el camino de producción y te deja ambos meses.)
B) Cargar solo de los volcados existentes (202610, Oficinas+Shevalche). Rápido para
   el video, pero sin 202609 y sin Sara.
C) Las dos: construyo la ingesta real Y, mientras, cargo 202610 de los volcados.
```

### 2) "Modo demostración" — ¿cómo lo hago?
```
A) Server-side (cookie de sesión): el toggle vive en cookie; el enmascarado se
   aplica en la plantilla Y en el Excel; persiste al navegar; garantiza que el Excel
   descargado también salga enmascarado. (Recomendada.)
B) Client-side (JS + localStorage): más simple, pero el dato real sigue en el DOM y
   el Excel no saldría enmascarado salvo que igual lo maneje el server.
```

> Montos: el enmascarado de montos va en **casilla aparte** (opcional), como pediste,
> en ambas opciones.

### 3) Detalle de ítems (api-cpe) — falta la URL exacta del detalle
```
El diag que generó los comp_diag_* ya bajaba el Detalle de ítems (api-cpe), pero su
URL no quedó en ningún artefacto ni en el repo (fue ad-hoc). Para cargar ítems:
  A) Pásame esa URL (o el script ad-hoc del diag de compras, que la tiene y ya
     funciona). La cableo en COMPRAS_DETALLE_CPE_URL y los ítems cargan completos.
  B) Por ahora cargo solo cabeceras (compras-carga ya las trae); los ítems quedan
     pendientes hasta que me pases la URL. (La rejilla de Compras por comprobante
     funciona; "una fila por ítem" necesita el detalle.)
```

## Decisiones tomadas
- **1A** (16/11/2026): ingesta real `compras-carga` vía SIRE + api-cpe. Prueba
  Shevalche 202610 primero.
- **2A** (16/11/2026): modo demostración server-side (cookie), enmascara plantilla +
  Excel; casilla aparte para montos.
