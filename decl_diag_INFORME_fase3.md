# Cierre decl-diag — columnas 01, modelo de datos, parser, carga y rejilla

Volcado base: `descargas/decl_diag2_20615446565_20261114_205112` (RUC con poco
historial: declaraciones de mar–ago/sep 2026). Todo lo de abajo sale del volcado
real y está probado con `parser_declaraciones.py` (sin BD, sin red).

Modelo **HTML-first** confirmado: la fila verde se arma con **01** (importe
pagado por período) + **02** (declaró/cuándo). La constancia 0621 (PDF) y el
desglose fino por tributo quedan para una fase posterior.

---

## 1) Columnas reales de la 01 (Consulta general) — CERRADAS

Tabla `#tblDetalleDeclPagos` (frame `01_consulta_general_resultados_frame4.html`):

| # | Columna | Ejemplo | Notas |
|---|---|---|---|
| 0 | (checkbox) | — | selección; se ignora |
| 1 | **Periodo** | `202609` | AAAAMM |
| 2 | **N° Form** | `0621` / `1662` | formulario |
| 3 | **N° Orden** | `1212055737` | **único por presentación** (llave) |
| 4 | **Descripción** (form) | `Simplificado IGV- Renta mensual` / `BOLETA DE PAGO - VIRTUAL` | |
| 5 | **Fecha de Pres.** | `20/10/2026` | DD/MM/YYYY |
| 6 | **Banco Receptor** | `-` / `VISA RECAUDACION` | solo en filas de pago |
| 7 | **Codigo-Tributo** | `-` / `3121` | solo en filas de pago |
| 8 | **Descripción** (tributo) | `-` / enlace *Detalle de Tributos Pagados* | el enlace es HTML (`verDetalleTributos`→vpS01Alias), no PDF |
| 9 | **Importe pagado** | `0,00` / `17,00` | formato peruano (`,` decimal) |

**Hallazgo clave:** la 01 mezcla **dos tipos de fila por período**:
- **Declaración** (`0621`): fecha de presentación + n° orden; banco/tributo `-`,
  importe `0,00` (en RUS se paga aparte).
- **Pago/boleta** (`1662`): banco + código-tributo (`3121` = Nuevo RUS) + importe,
  con enlace **HTML** al *Detalle de Tributos Pagados*.

Filtro de 01: por **período** mes/año desde–hasta (selects `mdesde/adesde/
mhasta/ahasta`). Cabecera confirma `DESDE 202606 HASTA 202611`. Importe pagado
filtrable `>= 0`. Hay *Exportar a Excel* (`consultaS01Alias?accion=exportarExcel`)
— no lo usamos.

**02 (DDJJ y pagos)** es la lista de presentaciones por **fecha de presentación**
(13 en el rango; 10 por página). Columnas: `# · Fec.Pres. · Periodo · Cod.Form. ·
Desc.Form. · N° Orden · Comprobante`. El *Comprobante* es `constanciaNP(num_ord,
'cod_for')` → **PDF** (fase posterior). Nota: 02 ve períodos fuera de la ventana de
01 (p.ej. `202602`, `202605` presentados el 07/07/2026) → **sirve para detectar
presentaciones tardías** en la carga incremental.

---

## 2) Modelo de datos (en rama; migración sin correr)

`models.py` (raíz, lado worker) + `migrar_declaraciones.py` (aditiva, idempotente):

- **`declaraciones_juradas`** — una presentación de DJ (0621…). Llave natural
  `(contribuyente_id, num_orden)`. Rectificatorias: **se conservan todas**; la de
  mayor fecha/num_orden por `(periodo, cod_formulario)` queda `es_vigente=TRUE`.
- **`pagos_tributarios`** — boletas/pagos (1662…): banco, `cod_tributo`, importe.
  `declaracion_id` opcional (vínculo informativo al período).
- **`declaraciones_tributos`** — desglose por tributo (período × tributo fino:
  IGV, Renta, EsSalud…). **Fase posterior**: fuente = constancia 0621 (PDF) o
  *Detalle de Tributos Pagados* del 1662 (HTML). Se deja definida, sin poblar.

Llave de rejilla pedida: **RUC + período + cod_tributo**. En HTML-first el grano
real es **período** (declaró / pagó total); el `cod_tributo` fino llega con la
fase del desglose. `periodo` se guarda normalizado AAAAMM (resuelve el texto libre
de `detalles_valorados.periodo`).

---

## 3) Parser (`parser_declaraciones.py`) — probado contra el volcado

Funciones puras sobre el HTML (testeable contra los volcados):
- `parse_consulta_general(html)` → `{cabecera, filas, declaraciones, pagos}`.
- `parse_djj_pagos(html)` → `{total, presentaciones[]}` (referencia la constancia,
  no la descarga).
- `marcar_vigentes(declaraciones)` → vigencia de rectificatorias.
- `rejilla_periodos(cg)` → filas de rejilla por período (declaró/fecha/pagado),
  **solo datos de fuente, sin calcular saldos**.
- CLI: `python parser_declaraciones.py <carpeta_volcado>` (sin BD, sin red).

**Salida real del volcado** (RUC 20615446565):
```
01: 4 declaraciones (0621, 202606–202609, todas vigentes), 3 pagos (1662, trib 3121)
02: total=13, 10 presentaciones en la página 1
REJILLA período × tributo:
  202609  declaró=sí (2026-10-20)  pagado=17.00  tributos=['3121']
  202608  declaró=sí (2026-09-18)  pagado=29.00  tributos=['3121']
  202607  declaró=sí (2026-08-19)  pagado=None   tributos=[]
  202606  declaró=sí (2026-07-07)  pagado=11.00  tributos=['3121']
```
(202607 declaró pero no tiene boleta 1662 en el rango → `pagado` sin dato; no se
inventa un 0.)

---

## 4) Primera carga (4 años) y carga incremental

**Corre dentro del worker** (raíz), con el **timeout por RUC del fix**
(worker-latido) y en **horario de baja carga** (madrugada Lima). Idempotente:
upsert por `(contribuyente_id, num_orden)`.

### Primera carga — por bloques de 6 meses
- **01 por período**, en bloques de 6 meses hacia atrás, `DECL_ANIOS=4` → **8
  bloques**. 01 es por período, así que captura todo lo de cada período aunque se
  haya presentado tarde.
- **02 por fecha de presentación**, mismo rango en bloques ≤6 meses, para traer la
  referencia de constancia y validar presentaciones.
- Guardar en `contribuyente`: `decl_cargado_hasta` (período más antiguo cubierto)
  y `decl_ultima_sync_at` (campos nuevos, en la misma migración si se aprueba).

### Carga incremental — mensual (o por ciclo)
- **01 por período**: últimos **K=4 períodos** → refresca importes, pagos y
  rectificatorias recientes.
- **02 por fecha de presentación**: últimos **~40 días** → detecta `num_orden`
  nuevos, **incluidas presentaciones tardías de períodos viejos**; por cada
  período viejo detectado fuera de la ventana, re-consultar 01 de ese período.

### Estimación de tiempo (empresa 3–5 años)
| Paso | Tiempo aprox. |
|---|---|
| Login SOL | 10–15 s |
| 01 × 8 bloques (llenar selects + enviar + parsear + pausa 4 s) | ~60–80 s |
| 02 × 8 rangos (enviar + 1–2 páginas c/u) | ~60–90 s |
| **Primera carga total / RUC** | **~2.5–4 min** |
| **Incremental / RUC** | **~30–45 s** |

Recomendado: timeout por RUC de **5 min** para la primera carga; **90 s** para la
incremental. Empezar por lotes pequeños en la madrugada.

---

## 5) Cómo queda la fila de la rejilla (período + tributo)

Solo datos de fuente (declaró / fecha / pagado) + el semáforo del buzón **al
lado** (ese sí ya existe). **No se calculan saldos ni deuda.**

| Período | Tributo | Declaró | Fecha pres. | Pagado (S/) | Buzón (al lado) |
|---|---|---|---|---|---|
| 2026-09 | Nuevo RUS (3121) | ✅ Sí | 20/10/2026 | 17.00 | 🟢 al día |
| 2026-08 | Nuevo RUS (3121) | ✅ Sí | 18/09/2026 | 29.00 | 🟢 al día |
| 2026-07 | IGV-Renta (0621) | ✅ Sí | 19/08/2026 | — *(sin boleta en fuente)* | 🟡 revisar |
| 2026-06 | Nuevo RUS (3121) | ✅ Sí | 07/07/2026 | 11.00 | 🟢 al día |

Notas de presentación:
- `Tributo` en HTML-first = código/descr. de la boleta (`3121`) o del formulario
  (`0621 → IGV-Renta`). El desglose fino IGV vs Renta es la fase del PDF/detalle.
- `Pagado = —` significa **sin dato en la fuente** (no "pagó 0"). alerta.pe
  **informa, no asesora**: no dice "debes S/ X".
- El semáforo 🟢🟡 es el del buzón (ya existente), mostrado al costado; **no** se
  mezcla con declaró/pagó.

---

## 6) Probar con un RUC con más historial, sin tocar prod

El modo **`decl-diag2` ya es seguro**: no escribe en la BD y no descarga PDFs;
solo vuelca estructura. Para validar el parser con más casos (rectificatorias,
régimen general con IGV+Renta en un 0621, varios formularios):

- Correr **`decl-diag2` contra un RUC con años de historial** (p.ej. el del
  **CCPL**, u otro del que tengas la **clave SOL** y consentimiento), con el
  worker pausado:
  `railway run -s alertape-worker python run_scraper.py <RUC> decl-diag2`
- Pasarme la carpeta `descargas/decl_diag2_<RUC>_*/` y corro
  `parser_declaraciones.py` sobre ella. **Cero escritura, cero prod.**
- Requisito: que ese RUC tenga **credencial SOL registrada** (o pasarla por
  entorno solo para la corrida local). Ojo con la memoria *unificación identidad*:
  **no tocar datos del CCPL**; aquí es solo lectura de SUNAT, sin BD.

Con un historial largo cierro: rectificatorias reales, régimen general
(IGV+Renta), y confirmo que la vigencia y el `periodo` normalizan bien.

---

## 7) Lo que necesito que decidas

1. **Años de primera carga:** propongo **4**. ¿OK o prefieres 3 / 5?
2. **Grano del `Tributo` en HTML-first:** mostrar a nivel período con
   código/descr. de boleta o formulario (como la tabla de arriba), y dejar el
   desglose IGV vs Renta para la fase del detalle. ¿Confirmas?
3. **RUC de prueba con más historial:** ¿usamos el **CCPL** u otro? ¿Tienes su
   **clave SOL** y consentimiento para la corrida de solo lectura?
4. **Horario de baja carga** para la ingesta en el worker: ¿madrugada 02:00–05:00
   Lima?
5. **Detalle de Tributos Pagados del 1662 (HTML, ya mapeado):** ¿lo poblamos en
   `declaraciones_tributos` desde v1 (es barato, sin PDF) o también fase posterior?
6. **Vínculo pago↔declaración** por período (informativo): ¿lo llenamos (asociar la
   boleta a la DJ vigente del mismo período) o lo dejamos nulo por ahora?
7. **Campos nuevos en `contribuyente`** (`decl_cargado_hasta`, `decl_ultima_sync_at`):
   ¿van en esta misma migración (cuando la apruebes)?

Nada de esto toca prod hasta tu OK: migración y escritura quedan en la rama.
