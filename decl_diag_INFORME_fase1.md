# Informe por pantalla — decl-diag fase 1

Volcado: `descargas/decl_diag_20615446565_20261114_151138` (RUC 20615446565,
14/11/2026 15:11). 44 opciones de menú visibles; 977 nodos en el árbol completo;
69 peticiones de red. Todo lo de abajo sale del volcado (enmascarado).

## Hallazgo clave: navegación por CÓDIGO
El menú SOL abre cada operación con
`ejecuta('MenuInternet.htm?action=iconExecute&code=<code>', …)`. El árbol
completo viene como JSON embebido en `00_menu.html` (name=código,
description, url backend). O sea **no hay que adivinar por texto: se navega por
código** (lo que ya hace la fase 2). Autenticación: **basta la sesión SOL** (las
operaciones POSTean al mismo app con la cookie; no vi token aparte en la URL).

## Códigos y URLs confirmados

| # | Pantalla | code | URL backend | invoke |
|---|---|---|---|---|
| 01 | Consulta general (Detalle de declaraciones y pagos) | `12.8.1.1.2` | `/cl-ti-itconsdecdetinf/consultaS01Alias?accion=consgeneral` | 1 |
| 02 | Consulta de declaraciones juradas y pagos | `12.1.1.1.4` | `/cl-ti-itdeclpagcon-mepeco/cdpS01Alias` | 1 |
| 05 | Detracciones — Ingreso como recaudación | `12.4.1.1.3` | `/cl-ti-itconsimput/imputS01Alias` | 1 |
| 06 | Detracciones — Consulta de NPD | `12.4.1.1.6` | `/cl-ti-itnumpagdetr/consultaIndividual.htm` | 3 |
| — | (bonus) Valores pendientes de pago | `15.1.1.1.1` | `/cl-ti-itvalores-consulta/adeudos/inicio` | 3 |

Agrupadores útiles: `12.1` "Presento mis declaraciones y pagos", `12.8` "Consulto
mis declaraciones y pagos", `12.4` "Detracciones".

## Estado por pantalla (fase 1)

- **00_menu** — `leído con datos`. Catálogo completo capturado
  (`00_menu_catalogo.txt` + árbol JSON en `00_menu.html`).
- **01 Consulta general** — `no se pudo leer` en fase 1 (no estaba en el nivel
  visible; mi fragmento de texto no la halló). **Ya mapeada** por código
  `12.8.1.1.2`; se captura en fase 2. Es la que trae tus 8 columnas (Período,
  N° Form, N° Orden, Descripción, Fecha Pres., Banco, Código-Tributo, Importe
  pagado).
- **02 Consulta de DDJJ y pagos** — `leído sin datos` (cargó el formulario; no
  envié rango en fase 1). **Caracterizada del todo** (ver abajo).
- **04 Detalle de Tributos Pagados (1662)** — no aparece como código propio; es
  una sub-vista a la que se llega desde una fila 1662 (constancia). Se confirma en
  fase 2 al abrir una constancia de 1662.
- **05 Ingreso como recaudación** / **06 NPD** — `no se pudo leer` en fase 1
  (texto no visible). Mapeadas por código; se capturan en fase 2.

## Pantalla 02 en detalle (operación `cdpS01Alias`) — es HTML, no JSON

Del `02_consulta_djj_pagos_frame4.html`:

- **Formulario de consulta** `formFechas` → `POST /cl-ti-itdeclpagcon-mepeco/cdpS01Alias`
  con `accion=lista`, `fechaDesde`, `fechaHasta`.
  - Formato de fecha: **DD/MM/YYYY**.
  - **Límites (validados en JS):** fecha inicio **≥ 01/10/2001**, fecha fin **≤
    hoy**, desde < hasta, y **rango ≤ 6 meses (182 días)** —
    `diferenciaMayor6meses` rechaza `>182`.
- **Tabla de resultados:** 7 columnas (`th colspan=7`). Con rango vacío muestra
  "No se encontraron declaraciones para el rango de fechas indicado". Las
  columnas reales se capturan en fase 2 con un rango con datos.
- **Contador / paginación:** "X a Y de Z"; `formPaginacion` → `POST
  /cl-ti-itdeclpagcon-mepeco/ConsultaDeclaracion.jsp` (`pagina`,
  `tamanioPagina`); `NavigFormNext`/`NavigFormBack` (`currPage`, `Action=next|back`).
- **Comprobante (constancia 0621):** `comprobante(formulario, numPres, numNabono,
  numOrden)` → `POST cdpS01Alias` con `accion=ComprobanteCons`. Hay rutas de PDF
  (`comprobanteNSIR` → `obtener_reportPdf` / `descargaArchivoAlias`) que **NO**
  dispararemos (no descargamos). Para 1662 nueva plataforma hay un chequeo previo
  por XML (`accion=consultarTributo`).

## ¿HTML o JSON? ¿token aparte?
- Las listas y la constancia son **HTML** (JSP). Algunos helpers devuelven un XML
  mínimo (`<codigo>`) para decidir qué constancia servir.
- **No se necesita token aparte:** todo va sobre la sesión SOL (cookies). Los
  formularios POST no llevan token visible en la URL.

## Tiempos y fallas
- `06_detracciones_npd` fue `no_se_pudo_leer` en fase 1 (esperado: por texto). En
  fase 2 va por código `12.4.1.1.6`.
- No se observaron timeouts; el login y la 02 cargaron normal. El diag pausa
  `DECL_DIAG_PAUSA_SEG` (def. 4s) entre pantallas.

## Fase 2 (modo `decl-diag2`) — qué hace ahora
Navega por código a 01, 02, 05, 06; en la **02** envía un rango de
`DECL_DIAG2_MESES` (def. **5**, bajo el límite de 6) y pulsa Buscar; vuelca la
tabla de resultados y, de la 1ª fila con enlace **Comprobante** (no NSIR/PDF),
captura la **constancia 0621** (detalle por tributo: Total Deuda / Monto Pago,
original/rectificatoria, semana). Sigue sin escribir en BD y sin descargar PDFs.

Correr (worker pausado, como la fase 1):

```
railway run -s alertape-worker python run_scraper.py 20615446565 decl-diag2
```

Variables: `DECL_DIAG2_MESES` (rango en meses, <6), `DECL_DIAG_PAUSA_SEG`,
`DECL_DIAG_SHOTS=1` (capturas, off por defecto).

Pásame el nuevo volcado `descargas/decl_diag2_20615446565_*/` (log +
`01_consulta_general*.html`, `02_djj_pagos_resultados*.html`,
`03_constancia_0621*.html`) y cierro el modelo de datos con las columnas reales y
el parser.
