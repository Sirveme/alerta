# Declaraciones y Pagos — Diagnóstico + Plan (sin persistir)

Objetivo: completar la rejilla **período × tributo** con la fuente que hoy le
falta —qué se **declaró**, cuándo y qué se **pagó**—. Hoy la rejilla trae el
buzón (los problemas) pero no las declaraciones ni los pagos (lo que está en
regla). Este documento es **diagnóstico + propuesta**: no toca la BD ni define
migraciones todavía.

---

## 1. Cómo correr el diag SIN chocar con el worker

El login SOL se cuelga si el worker automático entra a tu RUC a la vez. Dos
formas seguras (elige una):

- **Recomendada — pausar el worker un rato (sin tocar la BD):** en Railway, para
  el servicio `alertape-worker` (Stop / escalar a 0 réplicas), corre el diag, y
  al terminar lo reanudas. Cero escrituras, cero colisión.
- **Alternativa — sacar tu RUC del ciclo (una escritura mínima, tuya, no del
  diag):** marca `contribuyentes.estado = 'inactivo'` para `20615446565`, corre
  el diag, y lo vuelves a `activo`. El diag está hecho para **encontrar tu RUC
  aunque esté inactivo** (usa un fetch sin filtro de estado), justo para esto.

### Conexión a la BD desde tu PC (URL pública)

La `DATABASE_URL` del worker apunta al host **interno** de Railway
(`*.railway.internal`), que no resuelve desde tu PC (`getaddrinfo failed`). El
modo `decl-diag` ya está hecho para usar **`DATABASE_PUBLIC_URL`** si existe (hace
el swap antes de abrir la conexión; solo este modo, solo si la variable existe).
Falta que esa variable llegue al entorno del worker **por referencia** (sin que
copies ninguna contraseña):

1. **Postgres** → Settings → Networking → activa **Public Networking / TCP Proxy**
   si no está (eso expone `DATABASE_PUBLIC_URL` en el servicio Postgres).
2. **alertape-worker** → Variables → New Variable → nombre `DATABASE_PUBLIC_URL`,
   valor **`${{Postgres.DATABASE_PUBLIC_URL}}`** (usa el selector de referencia;
   reemplaza `Postgres` por el nombre real de tu servicio de BD). Es una
   **referencia**, no tecleas la contraseña.
   - Por CLI equivale a:
     `railway variables -s alertape-worker --set 'DATABASE_PUBLIC_URL=${{Postgres.DATABASE_PUBLIC_URL}}'`

Con eso, `railway run -s alertape-worker` te inyecta **todo** el entorno del
worker: `DATABASE_PUBLIC_URL` (BD pública) **y** `FERNET_KEY_SOL` (la llave con
que se descifran las claves SOL). No copias nada a mano.

Comando (local; **solo lee** la credencial, no escribe nada):

```
railway run -s alertape-worker python run_scraper.py 20615446565 decl-diag
```

Deberías ver primero la línea `[decl-diag] Conectando por DATABASE_PUBLIC_URL…`.
Si en cambio ves el `AVISO: no hay DATABASE_PUBLIC_URL`, falta el paso 2.

Ritmo y variables:
- `DECL_DIAG_PAUSA_SEG` (def. 4): pausa entre pantallas (súbelo si quieres ir más
  lento).
- `DECL_DIAG_SHOTS=1`: activa capturas de pantalla. **Desactivado por defecto a
  propósito**: el banner de SOL muestra tu usuario; las capturas no se pueden
  enmascarar. Úsalo solo si lo necesitas.

---

## 2. Qué vuelca el diag (y qué NO)

Reusa `sunafil-diag` (diag-first): **primero vemos la estructura, luego escribimos
el parser**. Por cada corrida escribe en `./descargas/decl_diag_<RUC>_<fecha>/`:

- `00_menu_catalogo.txt` — **el catálogo del menú SOL** (cada opción con su texto
  y destino). Es el artefacto clave de la 1ª corrida: con él mapeamos la ruta
  exacta de cada pantalla sin adivinar.
- `NN_<pantalla>.html` — el DOM **enmascarado** de cada pantalla (y `_frameN` para
  las que cargan en iframe).
- En el log, por pantalla: URL (como patrón, sin valores), nº de tablas y sus
  **encabezados**, formularios con sus **campos** (nombre/tipo/opciones), iframes,
  y las **peticiones de red** (método + patrón + status + si es HTML o JSON).

**Enmascarado (requisito duro, cumplido):** todo —HTML, URLs, logs— pasa por
`enmascarar` + redacción literal de tu usuario SOL y la clave. Las URLs se
guardan como **patrón** (claves de query sin valores) → nunca se filtra un
`token=`. No hay DNI ni usuario SOL en la salida ni en los volcados.

**No hace:** no escribe en la BD, no descarga comprobantes/PDF, no envía
formularios con datos reales.

Pantallas que intenta (todas en el menú SOL):

| slug | Pantalla | Qué esperamos ver |
|---|---|---|
| `01_consulta_general` | Detalle de declaraciones y pagos → Consulta general | Período, N° Form, N° Orden, Descripción, Fecha Pres., Banco, Código-Tributo, Importe pagado; filtro por rango y paginación |
| `02_consulta_djj_pagos` | Consulta de declaraciones juradas y pagos | Lista por fecha de presentación (rangos ≤6 meses); enlace **Comprobante** por fila |
| `03` (2ª pasada) | Constancia del Formulario **0621** | Detalle por tributo (1011 IGV, 3121 Renta…), Total Deuda / Monto Pago, original/rectificatoria, semana. Se llega **desde** una fila del 02, que exige enviar un rango → lo hacemos cuando el diag nos dé los campos del formulario |
| `04_detalle_tributos_pagados` | Detalle de Tributos Pagados (boletas **1662**) | Tributos pagados por boleta |
| `05_detracciones_recaudacion` | Detracciones — Ingreso como recaudación | Estructura de la consulta |
| `06_detracciones_npd` | Detracciones — Consulta de NPD | Estructura de la consulta |

> El **informe por pantalla** (HTML/JSON, si piden token aparte o basta la sesión,
> límites de rango, paginación, tiempos, pantallas que fallan) lo armo **con tu
> volcado**: pégame el log + el `00_menu_catalogo.txt` y, si puedes, zip de los
> `*.html`. No invento estructura que no haya visto.

---

## 3. Modelo de datos propuesto (solo propuesta, sin migración)

Tablas nuevas, en paralelo a lo existente (hay ya una tabla `tributos` con
`codigo`, y `detalles_valorados.periodo` es texto libre hoy):

**`declaracion`** — una por formulario presentado
- `ruc`, `periodo` (normalizado **AAAAMM**), `formulario` (p. ej. 0621, 0601),
  `num_orden`, `fecha_presentacion`, `tipo` (`original` | `rectificatoria`),
  `fuente` (qué pantalla la trajo), `capturado_at`.

**`declaracion_tributo`** — detalle por tributo de esa declaración (de la
constancia 0621)
- `declaracion_id`, `cod_tributo` (FK a `tributos.codigo`), `total_deuda`,
  `monto_pago` — **tal como los muestra la constancia** (no se recalcula nada).

**`pago`** — pagos (boletas 1662 y otros)
- `ruc`, `periodo` (AAAAMM), `formulario` (1662…), `num_orden`, `banco`,
  `cod_tributo`, `importe`, `fecha`.

**Detracciones** — tablas propias (`detraccion_recaudacion`, `detraccion_npd`),
estructura a definir con el volcado.

**Llave de enlace con la rejilla:** `RUC + periodo(AAAAMM) + cod_tributo`.

**Normalización del período (también para `detalles_valorados`):** hoy es
`String(20)` libre. Propongo una función única `normaliza_periodo(txt) -> 'AAAAMM'`
que entienda los formatos que devuelva SUNAT (`MM/AAAA`, `AAAA-MM`, `Ene-2026`,
etc.) y guardar **siempre** AAAAMM en las tablas nuevas; para
`detalles_valorados` se agrega una columna derivada `periodo_norm` (sin tocar el
texto original) cuando decidamos migrar.

**Rectificatorias:** se **conservan todas**; la última presentada por
`(ruc, periodo, formulario)` queda marcada como **vigente** (`es_vigente=True`),
las anteriores quedan como historial. Nunca se borra una declaración.

---

## 4. Reglas de alerta.pe (no cambian)

- **Informa, no asesora:** se muestran los datos **tal como vienen**. Nunca se
  calcula saldo, diferencia ni "cuánto debía".
- **Tres estados por consulta**, por pantalla y por período:
  `leído con datos` / `leído sin datos` / `no se pudo leer` (este último con
  fecha y hora para reintentar). **Nunca** se asume "en regla" si no se pudo leer.
  El diag ya reporta estos tres estados por pantalla.
- **Primera carga por bloques de 6 meses hacia atrás**, luego solo incremental
  (períodos nuevos). **Propuesta de cuántos años:** 4 años hacia atrás (cubre el
  plazo de prescripción habitual sin cargar de más) = 8 bloques de 6 meses, con
  pausa entre bloques. Dime si prefieres 2, 4 o 5.
- **Pausas entre pantallas** y **no chocar con el worker** (ya resuelto arriba).

---

## 5. Cómo se vería una fila VERDE de la rejilla

Hoy una celda `período × tributo` solo se "enciende" si hay un problema del
buzón. Con declaraciones + pagos, una celda **en regla** se vería así (ejemplo,
datos tal cual, sin cálculo):

```
Período 2026-09 · IGV (1011)
  ✔ Declarado   Form 0621 · N° orden 123456789 · presentado 12/10/2026 · original
  ✔ Pagado      Boleta 1662 · S/ 1,234.00 · BCP · 14/10/2026
  Estado de lectura: leído con datos (14/11/2026 09:12)
```

Y los otros dos estados, honestos:

```
Período 2026-09 · Renta (3121)
  ✔ Declarado   Form 0621 · N° orden 123456790 · presentado 12/10/2026
  —  Pago        leído sin datos (no figura pago en este período)
```
```
Período 2026-08 · IGV (1011)
  ⟳ No se pudo leer (13/11/2026 22:40) — reintentar
```

Regla visual: verde = declarado **y** pagado (ambos leídos con datos); ámbar
neutro = leído pero falta una pieza; gris con reloj = no se pudo leer. **En
ningún caso** decimos "debes S/ X" ni "estás al día de más": solo yuxtaponemos lo
que SUNAT muestra.

---

## 6. Chequeo legal (finalidad / privacidad)

No encontré el texto de términos ni de política de privacidad dentro del repo
(parece vivir fuera). **Acción pendiente tuya o mía con el texto a la vista:**
revisar si la finalidad declarada cubre "consultar y mostrarle al contribuyente
sus declaraciones y pagos ante SUNAT". Mi lectura preliminar: como es el **mismo
titular** viendo **sus propios** datos tributarios vía su clave SOL, encaja con
la finalidad de "mostrar la situación del contribuyente ante SUNAT/SUNAFIL". Aun
así, propongo **agregar una línea explícita** a la finalidad y a la privacidad:

> "alerta.pe consulta y te muestra tus declaraciones y pagos registrados en SUNAT
> (incluyendo detracciones), con el único fin de informarte tu situación. No
> realiza trámites ni modifica información en SUNAT."

Pásame el enlace o el texto vigente y te marco el cambio exacto.

---

## 7. Preguntas para que decidas

1. **Forma de correrlo:** ¿pausas el worker en Railway (recomendado) o prefieres
   el método de marcar tu RUC inactivo?
2. **Profundidad de la 1ª carga:** ¿2, 4 o 5 años hacia atrás (en bloques de 6
   meses)?
3. **Capturas de pantalla:** ¿las dejamos apagadas (recomendado) o las quieres
   para esta 1ª exploración asumiendo que muestran tu usuario SOL?
4. **2ª pasada de la constancia 0621:** una vez tenga los campos del formulario
   del `02`, ¿te parece que el diag envíe un **rango corto de prueba** (p. ej. 2
   meses) para capturar la estructura de la constancia y del enlace *Comprobante*?
5. **Detracciones:** ¿entran en esta fase o las dejamos para una segunda, cuando
   confirmemos su estructura?
6. **Legal:** ¿me pasas el texto de términos/privacidad vigente para proponer la
   línea de finalidad?

---

## Entrega de esta fase

- Herramienta `decl-diag` lista (modo en `run_scraper.py` + módulo
  `scraper_sunat_declaraciones.py`), que corres tú localmente.
- Este plan: modelo de datos, fila verde, reglas, chequeo legal y preguntas.
- **Sin migraciones ni escritura en BD.** El volcado y el informe por pantalla
  salen cuando corras el diag y me pases la salida.
