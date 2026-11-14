# Informe por pantalla — decl-diag2 fase 2

Volcado: `descargas/decl_diag2_20615446565_20261114_153848` (14/11/2026 15:38,
rango 5 meses = 15/06/2026–14/11/2026). 138 peticiones. Navegación por código: OK
en las 4 pantallas. Todo lo de abajo sale del volcado real.

## 02 — Consulta de declaraciones juradas y pagos (`cdpS01Alias`)
**Columnas reales de la tabla de resultados** (frame `02_djj_pagos_resultados_frame4`):

| (icono) | Fec. Pres. | Periodo | Cod.Form. | Desc.Form. | N° Orden | Detalle |
|---|---|---|---|---|---|---|

- Es la **lista de presentaciones** (qué se declaró y cuándo). No trae tributo ni
  importe: eso está en la constancia (Detalle) o en la Consulta general (01).
- **Constancia (corrección importante):** el enlace de "Detalle" NO es
  `comprobante(...)` sino **`constanciaNP(num_ord, 'cod_for')`**. En el rango
  salieron filas `0621` y `1662`. El flujo es:
  `constanciaNP` → POST `accion=validar_constanciaNP` (devuelve XML `<codigo>`) →
  si hay código, `window.open(...accion=obtener_constanciaNP&cod_ecm=…)` = **un
  PDF**. O sea la constancia 0621 (detalle por tributo: Total Deuda / Monto Pago,
  original/rectificatoria, semana) **vive en un PDF**, no en HTML.
- Por eso en la corrida la "03 constancia" salió `sin_datos`: yo buscaba
  `comprobante(`. **Ya corregido**: ahora el diag detecta `constanciaNP`, cuenta
  los enlaces por formulario y reporta el patrón de URL del PDF **sin descargar**.

## 01 — Consulta general (`consultageneral`) — la grilla que buscamos
Filtra por **período (mes/año) desde–hasta** (`mdesde/adesde/mhasta/ahasta`,
selects) o por fecha de presentación (`fec_preini/fec_prefin`); radio
`declaracion` (1/2), select `formu2`. En la corrida **no envié el filtro** (conté
el calendario). **Ya corregido**: el diag ahora envía el período y vuelca la
grilla. Según tu descripción, trae: Período, N° Form, N° Orden, Descripción,
Fecha Pres., Banco, **Código-Tributo, Importe pagado** → **es la fuente HTML del
período×tributo×importe pagado** (lo que enciende la fila verde). Se confirma al
re-correr.

## 05 — Ingreso como recaudación (`imputS01Alias`)
Lista de boletas (`formOrden` accion=`consDetalle`; paginación
`listadoBoletas.jsp`); el detalle por fila va por `sendNroOrden` con hidden
`norden/fpres/periodo/importe/descform/formul/tributo`. Estructura capturada;
el detalle por boleta se recorre en una pasada corta si decides incluir
detracciones en esta fase.

## 06 — Consulta de NPD (`consultaIndividual.htm`) — HTML directo
Tabla rica ya capturada. Encabezados: **NPD, Creación, Fecha Límite de Pago,
Estado, Importe, RUC, Nombre o Razón Social, Número Valor, Periodo, Código
Tributo, N° REC, Fecha Emisión, Fecha Corte, N° formulario, Fecha Presentación,
N° OP, Monto Exigible**. Es HTML; va directo a tablas propias de detracciones.

## Conclusión para el modelo de datos
- **Pagos período×tributo×importe → de 01 (Consulta general), en HTML.** Es la
  fuente principal de la fila verde. (`pago`: período, formulario, n° orden,
  banco, cod_tributo, importe, fecha.)
- **Declaraciones (qué se declaró, cuándo, original/rectificatoria) → de 02**
  (lista) + la constancia. (`declaracion`.)
- **Detalle por tributo de la 0621 (Total Deuda vs Monto Pago, semana) → PDF**
  de `obtener_constanciaNP`. Es enriquecimiento; requiere **descargar y parsear el
  PDF** (el scraper ya sabe bajar y leer PDFs). (`declaracion_tributo`.)
- **Detracciones: NPD (06, HTML) + recaudación (05).** Tablas propias.

## La decisión que necesito de ti
La 0621 "Total Deuda / Monto Pago por tributo" **solo está en el PDF**
(`obtener_constanciaNP`). Dos caminos:
1. **HTML-first (recomendado para empezar):** armamos la fila verde solo con 01
   (importe pagado por período/tributo) + 02 (declaró/cuándo). Sin tocar PDFs. Más
   simple y cubre "declarado y pagado".
2. **Con PDF:** además bajamos y parseamos la constancia 0621 para el desglose
   Total Deuda vs Monto Pago y original/rectificatoria. Más rico, pero descarga
   PDFs (hay que levantar la regla "no descargar" para el modo de ingesta real).

¿Arrancamos HTML-first (1) y dejamos el PDF (2) para una fase posterior?

## Re-corrida sugerida (con las correcciones ya en el código)
```
railway run -s alertape-worker python run_scraper.py 20615446565 decl-diag2
```
Ahora debería: enviar el filtro de **01** y volcar `01_consulta_general_resultados`
(la grilla real), y en **03** reportar los enlaces `constanciaNP` por formulario
(0621/1662) sin descargar. Pásame ese volcado y cierro las columnas exactas de 01
+ el modelo de datos final.
