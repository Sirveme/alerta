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
  `(anio, periodo AAAAMM, digito_ruc 0–9, grupo, fecha_vencimiento, fuente, cargado_at)`,
  única por `(periodo, digito_ruc, grupo)`.
- **Cargador anual** `cargar_cronograma_sunat(anio)` (a escribir cuando se apruebe):
  baja la tabla oficial del año, la parsea y hace **upsert** por la llave única.
  `fuente` guarda la URL/Resolución; `cargado_at`, cuándo.
- **Disparo**: una vez al año (diciembre/enero), desde el worker (tarea programada)
  **o** un botón en `/interno` para el soporte. Idempotente y re-ejecutable.
- **Verificación antes de usar**: una vista en `/interno` muestra el año cargado
  (12 períodos × 10 dígitos + grupos) para revisarlo de un vistazo; si SUNAT cambia
  el formato, el *diag-first* lo detecta (se vuelca, se confirma, recién se carga).
- **Fallback manual**: si el scrape falla un año, el soporte puede cargar/confirmar
  el año a mano (mismos campos). El estado "fuera de plazo" **solo se evalúa si el
  período tiene cronograma cargado**; si no, se muestra "sin dato de vencimiento"
  (honesto), nunca se adivina.

---

## 2) Lógica del estado (diseño)

Para un **período** con declaración y pago, usando el **último dígito del RUC**:

```
venc = cronograma[periodo, digito=ultimo_digito(ruc), grupo='general'].fecha_vencimiento
si no hay 'venc' (período sin cronograma cargado):
    estado_pago = "sin dato de vencimiento"      # no se evalúa plazo
elif hay pago (fecha_pago):
    estado_pago = "pagó dentro de plazo"   si fecha_pago <= venc
                  "pagó fuera de plazo"    si fecha_pago >  venc
else:
    estado_pago = "declaró; sin pago en la fuente"   # NUNCA "debe"
```

- El **último dígito** del RUC sale del propio RUC (público). El grupo por defecto
  es `general`; *Buenos Contribuyentes/UESP* usan su fila `grupo` (se activa cuando
  tengamos ese indicador en la ficha del contribuyente; mientras tanto, `general`).
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
- **Incremental:** `02` por **fecha de presentación** de los **últimos ~40 días**
  → detecta `num_orden` nuevos (incluye tardíos de períodos viejos); por cada
  período tocado, refrescar `01` de ese período (importe/banco del pago).
- El cronograma se carga **aparte, una vez al año** (no por RUC).
- Tiempos reales (log de Duilio, 6 meses, RUC poco cargado): login 16 s, 01 17 s,
  02 15 s, 05 9 s, 06 10 s (~67 s). Para **4 años** ≈ 8 bloques → estimado
  **~2.5–4 min/RUC** la primera carga; **~30–45 s** el incremental. (Para cerrar el
  1662 de 202605: `decl-diag2 --periodo-desde 202605`.)

---

## 4) Qué decido / confirmo con Duilio
1. **Fuente del cronograma**: ¿scrapeo de la tabla oficial de SUNAT del año, o
   parseo del PDF de la Resolución? (propongo la tabla HTML; PDF como respaldo).
2. **Disparo anual**: ¿tarea del worker en diciembre/enero, botón en `/interno`, o
   ambos?
3. **Buenos Contribuyentes/UESP**: ¿incorporamos ya el indicador (otra fecha) o
   arrancamos solo `general`?
4. **Sin cronograma del período**: confirmar que se muestra "sin dato de
   vencimiento" (no se evalúa plazo) — nunca se asume.
