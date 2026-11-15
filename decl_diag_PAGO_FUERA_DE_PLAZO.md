# Diseño — estado "pagó fuera de plazo" (SOLO diseño, sin migrar)

alerta.pe **informa**, no asesora. Este estado solo **yuxtapone hechos de la
fuente**: "declaraste el DD/MM, pagaste el DD/MM, el vencimiento de este período
fue el DD/MM". **Nunca** dice "debes", **nunca** calcula intereses ni multas.

---

## 1) Cronograma de vencimientos SUNAT (fuente oficial, NO hardcodeado)

### Qué es
Cada año SUNAT publica, por **Resolución de Superintendencia**, el **Cronograma de
Obligaciones Mensuales**: para cada **período** (mes) y cada **último dígito del
RUC** (0–9), la **fecha de vencimiento**. Hay además fechas distintas para
**Buenos Contribuyentes y UESP** (vencen un día o dos después).

### De dónde se obtiene
- Fuente primaria: la página oficial de SUNAT de *"Cronograma de vencimientos"*
  (la tabla del año) y/o el PDF/HTML de la Resolución de Superintendencia que lo
  aprueba (se publica en **diciembre** para el año siguiente).
- **No hay API oficial.** Por eso el cargador hace **lectura (scrape/parseo) de esa
  tabla oficial una vez al año** y la persiste. Mismo principio *diag-first* que el
  resto: primero se **vuelca y se confirma** la estructura, luego se carga.

### Cómo se carga y se actualiza (propuesta)
- Tabla **`cronograma_vencimientos`** (ya en `migrar_declaraciones.py`, sin correr):
  `(anio, periodo AAAAMM, digito_ruc 0–9, grupo, fecha_vencimiento, num_resolucion,
  fuente, validado_pdf_at, cargado_at)`, única por `(periodo, digito_ruc, grupo)`.
  **Cada fila guarda el N° de resolución y su URL** (trazabilidad legal).
- **Cargador anual** `cargar_cronograma_sunat(anio)` (a escribir cuando se apruebe):
  baja la **tabla HTML** oficial del año, la parsea y hace **upsert** por la llave
  única, grabando `num_resolucion` + `fuente`. **Validación contra el PDF** de la
  Resolución **al menos una vez al año** → sella `validado_pdf_at`.
- **Disparo (ambos)**: tarea del **worker en diciembre/enero** + **botón en
  `/interno`**. El botón hace **preview → confirmar**: muestra la vista previa del
  año (períodos × dígitos × grupos) y **solo guarda cuando Duilio confirma**.
  Idempotente y re-ejecutable.
- Si SUNAT cambia el formato, el *diag-first* lo detecta (se vuelca, se confirma,
  recién se carga).
- **Fallback manual**: si el scrape falla un año, el soporte puede cargar/confirmar
  el año a mano (mismos campos). El estado "fuera de plazo" **solo se evalúa si el
  período tiene cronograma cargado**; si no, se muestra "sin dato de vencimiento"
  (honesto), nunca se adivina.

---

## 2) Lógica del estado (diseño)

Para un **período** con declaración y pago, usando el **último dígito del RUC**:

```
filas = cronograma[periodo, digito=ultimo_digito(ruc)]   # general + BC/UESP
venc  = vencimiento_aplicable(filas, es_buen_contribuyente)
       # es_buen_contribuyente None (no sabemos) → MÁS TARDÍA (conservador)
si no hay 'venc' (período sin cronograma cargado):
    estado_pago = SIN_VENCIMIENTO         # "sin dato de vencimiento"; no se evalúa
elif hay pago (fecha_pago):
    estado_pago = PAGO_DENTRO   si fecha_pago <= venc
                  PAGO_FUERA    si fecha_pago >  venc
else:
    estado_pago = SIN_PAGO                # "declaró; sin pago en la fuente" — NUNCA "debe"
```
(Implementado en `cronograma.py`: `clasificar_pago(...)`.)

- El **último dígito** del RUC sale del propio RUC (público). Se consideran todas
  las filas del período (grupo `general` + `buenos_contribuyentes`/`uesp`). Si **no
  sabemos** si el RUC es Buen Contribuyente → se usa la fecha **más tardía**
  (conservador). Cuando tengamos el indicador en la ficha del contribuyente, se
  pasa `es_buen_contribuyente=True/False` y usa la fila exacta.
- El estado es **por período**; el desglose fino por tributo es fase posterior.
- Solo compara **fechas**. No toca importes de deuda, no suma intereses.

### Ejemplo real (RUC 10467016283, último dígito **3**)
- Período **202605**: **declaró 17/06/2026**, **pagó 25/09/2026** (boleta 1662).
- `venc` = vencimiento de 202605 para dígito 3 (de la Resolución 2026, aún por
  cargar). El pago del **25/09/2026** es **meses después** de ese vencimiento
  (que para mayo-2026 cae en junio-2026) → **"pagó fuera de plazo"**.

### Copy honesto (ejemplos)
- ✅ "Pagaste este período el **25/09/2026**. El vencimiento (RUC terminado en 3)
  fue el **DD/06/2026**." → etiqueta **"Pagó fuera de plazo"**.
- ✅ "Pagaste **dentro del plazo**."
- ✅ "Declaraste; **no vemos un pago** de este período en tu consulta SUNAT."
- ❌ Prohibido: "Debes S/…", "Tienes una multa", "Paga tus intereses", calcular TIM.

---

## 3) Tablas y flujo de carga (task 3 — en archivo, sin ejecutar)

`migrar_declaraciones.py` (aditiva, idempotente, **NO ejecutada**) crea:
- `declaraciones_juradas`  — DJ (0621…) por presentación. Llave `(contribuyente, num_orden)`.
- `pagos_tributarios`      — boletas/pagos (1662/1663…): banco, cod_tributo, importe.
- `declaraciones_tributos` — desglose por tributo (fase PDF posterior).
- `cronograma_vencimientos` — vencimientos por período × dígito (este diseño).

Modelos en `models.py` (raíz): `DeclaracionJurada`, `PagoTributario`,
`DeclaracionTributo`, `CronogramaVencimiento`.

### Carga inicial (4 años) e incremental
- **Inicial (4 años):** `01` por período en **bloques de ≤6 meses** (grilla
  período×tributo×importe) + `02` por **fecha de presentación** en bloques de ≤6
  meses (para no perder presentaciones/pagos **tardíos** de períodos viejos, como
  el 1662 de 202605 que 01 no vio por ventana). Upsert por `num_orden`.
  > La **01 valida en JS un tope de 6 meses** (`validarFormCGeneral` → alert
  > *"El rango de consulta es mayor a 6 meses…"*). Por eso cada bloque de 01 es de
  > **≤6 meses** (con `--periodo-desde`, el diag pone `hasta = desde + 5 meses`).
  > Si el formulario **no avanza** a resultados, el diag lo reporta como
  > **`error_filtro`** (con el texto del aviso de SUNAT), **nunca** como
  > `sin_datos`: un fallo de navegación visto como "no hay declaraciones" sería
  > grave.
- **Incremental:** `02` por **fecha de presentación** de los **últimos ~40 días**
  → detecta `num_orden` nuevos (incluye tardíos de períodos viejos); por cada
  período tocado, refrescar `01` de ese período (importe/banco del pago).
- El cronograma se carga **aparte, una vez al año** (no por RUC).
- Tiempos reales (log de Duilio, 6 meses, RUC poco cargado): login 16 s, 01 17 s,
  02 15 s, 05 9 s, 06 10 s (~67 s). Para **4 años** ≈ 8 bloques → estimado
  **~2.5–4 min/RUC** la primera carga; **~30–45 s** el incremental. (Para cerrar el
  1662 de 202605: `decl-diag2 --periodo-desde 202605`.)

---

## 4) Decisiones TOMADAS (Duilio, 15/11/2026)
1. **Fuente**: la **Resolución de Superintendencia** es la fuente **legal**. Se
   carga de la **tabla HTML** (más fácil), pero **cada fila guarda el N° de
   resolución (`num_resolucion`) y su URL (`fuente`)**, y se **valida contra el
   PDF al menos una vez al año** (`validado_pdf_at`). Campos ya en el modelo/tabla.
2. **Disparo**: **ambos**. Tarea del **worker en dic/ene** + **botón en
   `/interno`**. El botón **primero muestra una vista previa** (períodos × dígitos
   × grupos) y **recién guarda cuando Duilio confirma** (flujo preview→confirmar,
   nunca guarda directo).
3. **Buenos Contribuyentes/UESP**: **sí, desde ya**. Si **no sabemos** si el RUC es
   Buen Contribuyente → se usa la fecha **MÁS TARDÍA** (criterio **conservador**):
   un pago es "fuera de plazo" solo si cae **después del último vencimiento
   posible**. Regla de oro: **prefiero no alertar a alertar en falso** (una sola
   alerta falsa y el contador deja de confiar). Implementado en `cronograma.py`
   (`vencimiento_aplicable(..., es_buen_contribuyente=None) → max(fechas)`), con
   autotest.
4. **Sin cronograma del período**: **confirmado** → se muestra **"sin dato de
   vencimiento"** (no se evalúa plazo, nunca se asume). Es el estado
   `SIN_VENCIMIENTO` de `cronograma.py`.

### Estado de implementación (rama, sin migrar / sin merge)
Decisión 16/11/2026: **diag-first** para el cronograma Y el padrón BC.

Hecho:
- `cronograma.py`: lógica pura + regla conservadora + estados
  (pago_dentro/pago_fuera/sin_pago/sin_vencimiento) + `es_buen_contribuyente(ruc,
  padrón)` (None sin padrón → conservador) + autotest. ✔
- `models.CronogramaVencimiento` (+ `num_resolucion`/`fuente`/`validado_pdf_at`) y
  `models.PadronBuenContribuyente`; tablas en `migrar_declaraciones.py`. ✔ (sin correr)
- `diag_publico.py`: **volcador diag-first de páginas públicas** (sin login, sin
  BD). Vuelca estructura (HTML, tablas con encabezados+n_filas+muestra, forms,
  enlaces útiles, red) a `descargas/<slug>_<ts>/`. Es el paso que desbloquea el
  parser. ✔

Pendiente (tras ver los volcados reales):
1. **Parser + cargador del cronograma** `cargar_cronograma_sunat(anio)`: parsea la
   tabla real (del volcado), hace upsert por `(periodo, digito, grupo)`, graba
   `num_resolucion`+`fuente`, valida vs PDF 1×/año (`validado_pdf_at`).
2. **Parser + cargador del padrón BC**: upsert por `ruc`.
3. **Vista `/interno/cronograma`** (SOPORTE_GLOBAL, como `/interno/mapa`), con
   **preview→confirmar**: GET muestra la vista previa parseada (períodos × dígitos
   × grupos) SIN guardar; un botón **Confirmar** recién hace el upsert. Muestra
   también lo ya cargado por año. Disparo también por tarea del worker (dic/ene),
   usando el mismo cargador con un flag de confirmación automática solo si la
   validación estructural pasa (si no, queda en estado "por confirmar").

Cómo volcar (local; públicas, sin railway/SOL):
```
python diag_publico.py cronograma_2026 "<URL oficial del cronograma del año>"
python diag_publico.py bc_padron       "<URL del padrón de Buenos Contribuyentes>"
```
