# comp-diag — Comprobantes de compras y ventas CON ÍTEMS (diag-first)

Lee, **del lado del servidor y en solo lectura**, los comprobantes de un período
(compras RCE + ventas RVIE) con la **descripción de los ítems**. Reusa el login SOL
de decl-diag (mismo enmascarado, `sin_acceso`, pausas). **No** escribe en la BD,
**no** descarga PDFs/ZIP, **no** hace merge ni migraciones.

## Cómo corre
```
railway run -s alertape-worker python run_scraper.py <RUC> comp-diag --periodo AAAAMM [--n 5]
```
(Worker pausado, igual que decl-diag.) Vuelca a `descargas/comp_diag_<RUC>_<ts>/`.

## Método (validado antes en `asistente-sol`, ramas diagnostico-01/02 y lote-03)
1. Tras el login, la SPA de SIRE/Comprobantes llama a servicios REST con un
   **Bearer** (JWT por sesión) que la propia página porta. Lo **capturamos** de sus
   requests (igual que el "gancho": nunca se guarda, solo se reusa en memoria para
   pedir las mismas rutas que pediría la pantalla).
2. **Lista del período** desde SIRE:
   - Compras (confirmado): `GET api-sire …/migeigv/libros/rce/propuesta/web/propuesta/{AAAAMM}/busqueda?codTipoOpe=…&page=1&perPage=…`
   - Ventas (RVIE): el endpoint análogo **se captura** al abrir la pantalla (no se
     adivina; si la página no lo llama, se reporta como pendiente).
3. **Detalle con ítems** desde api-cpe (confirmado):
   `GET api-cpe …/v1/contribuyente/consultacpe/comprobantes/{ruc}-{tipo}-{serie}-{numero}-{ind}`
   con **indicador final `2`=Recibido (compras)** y **`1`=Emitido (ventas)**.
4. Se consultan los **primeros `--n`** comprobantes **consultables** de cada libro
   (electrónicos: tipos 01/03/07/08, serie `^[FBE][A-Z0-9]{3}$`, emisor con RUC).

## Campos del ítem (api-cpe, confirmados)
`desItem` (descripción) · `cntItems` (cantidad) · `codUnidadMedida`/`desUnidadMedida`
(unidad) · `mtoValUnitario` (valor unitario) · `mtoDesc` (descuento) · `mtoImpTotal`
(importe del ítem) · `mtoICBPER`. El **IGV y el total son del comprobante** (cabecera
`procedenciaMasiva`: `mtoSumIGV`, `mtoImporteTotal`), **no por línea** — la consulta
no trae IGV por ítem. El Excel lo marca así en el encabezado.

**Montos TAL CUAL de SUNAT (sin interpretar):** las columnas de ítem se llaman
`valor_unitario_sin_imp` (`mtoValUnitario`) e `importe_linea_emisor` (`mtoImpTotal`).
En varios emisores **`cantidad × valor_unitario_sin_imp ≠ importe_linea_emisor`**
(p.ej. CERVECERIA SAN JUAN 12 × 47.40 ≠ 811.20): suele deberse a impuestos/descuentos
que el emisor incluye en la línea. alerta.pe **no calcula ni corrige** esos montos.
**Descripción**: si el emisor empaqueta datos con `@#@`, la 1ª parte es la descripción
y el resto va a `detalle_emisor` sin interpretar; si la descripción viene repetida dos
veces idéntica, se deja una sola vez.

## Ritmo y límites (de `asistente-sol/motor.js`)
- Pausa **3–5 s** entre comprobantes, nada en paralelo contra SOL.
- Timeout 30 s por petición. 401/403 = sesión/token vencido (corta el libro).
- 422/404 = comprobante no encontrado. SUNAT tiene **tope diario de consultas por
  RUC** (a confirmar su valor en la corrida real).

## Salida del volcado
- `00_menu.json` — catálogo de menú (para la guía de perfil mínimo).
- `red_api.json` — mapa de red api-sire/api-cpe (patrón + estado, sin token).
- `lista_<libro>_<AAAAMM>.json` — respuesta cruda de la propuesta (para endurecer el
  parser; el diag detecta los campos por forma y reporta las claves reales).
- `detalle_<libro>_<clave>.json` — respuesta cruda de cada consultacpe (con ítems).
- `muestra_items.xlsx` — **una fila por ítem**: libro, fecha, serie-número, RUC y
  razón del emisor, descripción, cantidad, unidad, valor unitario, importe del ítem,
  IGV y total del comprobante, moneda.
- `resumen.json` — conteos, tiempos y avisos por libro.

## Pendiente de la 1ª corrida real (se llena con el volcado)
- Confirmar los **nombres de campo** de la propuesta SIRE (compras y ventas) y el
  **endpoint de ventas** (RVIE).
- `codTipoOpe` real (hoy por defecto `1`, configurable con `COMP_CODTIPOOPE`).
- **Perfil mínimo del usuario secundario**: qué opciones de menú SOL necesita
  (SIRE – Registro de Compras/Ventas Electrónico, Consulta de Comprobante de Pago).
  El diag lista las opciones detectadas en `res["opciones_sire"]`.
- Tope diario de consultas y comportamiento de rate-limit de SUNAT.
