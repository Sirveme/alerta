<!--
  MAPA GLOBAL DE alerta.pe — contenido editable.
  Edita SOLO este archivo para cambiar la página /interno/mapa.
  Convención simple (Markdown):
    #  Título          → título de la página (una sola vez, arriba)
    >  Texto           → bajada/lede (debajo del título o de un panel)
    ## Nombre          → abre un PANEL nuevo (tarjeta)
    ### Subtítulo      → subtítulo dentro del panel
    -  Ítem            → viñeta (usa "  - " con 2 espacios para anidar)
    **negrita**  `código`
  El color de acento de cada panel se elige por palabra clave del título
  (sistema/contable/tributario/contador/asistente/empresario).
-->

# Mapa Global de alerta.pe

> Vigilamos el buzón de SUNAT y SUNAFIL de cada RUC y avisamos a tiempo, en lenguaje claro. **Informamos, no asesoramos**: mostramos hechos, el contador decide. Página interna de referencia para soporte.

## Pilar · Sistema

> La plataforma técnica que sostiene todo.

- **PWA** instalable (móvil y escritorio) con **push por persona** — cada quien recibe su aviso, no se re-notifica al que ya vio.
- **Worker** de scraping: lectores de **SUNAT** (notificaciones, valorados/PDF) y **SUNAFIL** (buzón JSF/DataTables, diag-first).
- **Identidad por DNI** (`Persona`/`Acceso`), sesión firmada HMAC con revocación server-side (`sesion_version`).
- **Multi-tenant**: cada estudio ve solo lo suyo; `SOPORTE_GLOBAL` ve todos los buzones (solo lectura, auditado).

## Pilar · Contable

> El espacio de trabajo del estudio.

- **Cartera**: todos los clientes del estudio con conteos por tipo y su asistente responsable.
- **Vista de cliente** (`/cliente/{id}`): rejilla por **período × tributo**, deuda notificada y pagado por celda — **sin semáforo, a propósito**.
- **Capa de gestión**: instrucciones al equipo (pendiente/terminado), con fecha límite que pone el contador.
- **Exportar a Excel**: buzón de un cliente y cartera del estudio, respetando el alcance de cada persona.

## Pilar · Tributario

> Lo que se vigila en SUNAT / SUNAFIL.

- **Cobranza coactiva**, **órdenes de pago**, **multas**, **esquelas**, resoluciones y pagos.
- **Deuda notificada** vs **pagado**, mostrados por separado — nunca el neto.
- **Urgencia** de la notificación: informativa → importante → urgente → **crítica** (coactiva/plazo corriendo).
- **Períodos tranquilos** marcados en verde: "sin novedad en el buzón".

## Funcionalidades centrales

> Lo que la app hace todos los días.

- **Buzón vigilado** por RUC, con copy del push = razón social.
- **Grupos** de clientes (naturaleza del negocio) — asignables en vivo.
- **Invitaciones y accesos** (Capa 1): el contador invita clientes y crea asistentes; el dueño/socio invita co-dueños.
- **Asignaciones**: qué clientes ve cada asistente (por grupo en vivo o por RUC suelto).
- **Auditoría** append-only de accesos y asignaciones.

## Rol · Contador (dueño / supervisor)

> Autoridad plena sobre su estudio.

- Ve **toda su cartera**; entra a cada cliente y gestiona instrucciones.
- **Invita clientes** y **crea asistentes**; asigna clientes a su equipo.
- **Exporta** su cartera y el buzón de cualquier cliente.
- Carga y administra las **claves SOL** de los RUCs vigilados.

## Rol · Asistente

> Solo lectura, acotado a lo asignado.

- Ve **solo los clientes que le asignaron** (por grupo o por RUC).
- Entra al detalle de esos clientes y **marca instrucciones como terminadas**.
- **Exporta solo su propio alcance** — nunca la cartera completa.
- **No** invita ni administra accesos.

## Rol · Empresario

> El dueño del negocio, sobre su propio RUC.

- Ve **su propio buzón** (`/resumen`) con deuda y urgencia, en lenguaje claro.
- Puede **invitar a su contador** y a un **socio** co-dueño.
- Solo lectura del buzón; **no** opera la cartera de un estudio.
