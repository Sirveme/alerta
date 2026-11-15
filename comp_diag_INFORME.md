# Informe — Diagnóstico de Comprobantes con ítems (comp-diag)

**Fecha:** 2026-11-15 · **Rama:** `feat/comp-diag` · **Sin BD, sin PDFs, sin merge, sin migraciones.**
Modo diag-first: solo navega y consulta en SOL; reusa el login de decl-diag.

## Qué FUNCIONA (confirmado en RUCs reales)
- **Login + guardián de aceptación + interstitial de contacto.** alerta.pe solo navega
  y consulta; nunca marca casillas/acepta/registra. El aviso "Valida tus datos de
  contacto" se cierra solo con "Finalizar"/"Continuar sin confirmar" (no cambia datos)
  y se reporta como `contacto_sin_validar`. Correo/celular enmascarados; PNG omitido en
  pantallas con datos de contacto; el Bearer nunca se escribe a disco.
- **Navegación a las SPA nuevas por CÓDIGO de menú** (pestaña nueva, `action=execute`),
  con captura del Bearer a nivel de contexto y espera hasta 30 s:
  - Comprobantes (api-cpe): `11.38.1.1.1` "Nueva Consulta de comprobantes de pago".
  - SIRE compras (api-sire): `60.2.2.1.1` "Gestion de Compras".
- **Lista del período (compras/RCE)** por `api-sire …/rce/propuesta/web/propuesta/
  {AAAAMM}/busqueda` con **paginación completa** (`perPage=20`), reportando el total
  real y la cantidad de páginas. Parser **confirmado** (`parse_propuesta_rce`): tipo
  `codTipoCDP`, serie `numSerieCDP`, número `numCDP`, **emisor = proveedor**
  `numDocIdentidadProveedor`, total `montos.mtoTotalCp`.
- **Detalle con ítems** por `api-cpe …/consultacpe/comprobantes/{ruc}-{tipo}-{serie}-
  {número}-2` (Recibido). Campos por ítem: descripción, cantidad, unidad,
  `valor_unitario_sin_imp`, `importe_linea_emisor`, ICBPER. IGV y total son del
  comprobante (no por línea).
- **Casos de emisor, sin interpretar:** `@#@` → 1ª parte = descripción, resto a
  `detalle_emisor`; descripción repetida 2× → una vez; montos **tal cual** de SUNAT
  (en varios emisores `cantidad × valor_unitario_sin_imp ≠ importe_linea_emisor` por
  impuestos/descuentos; no se calcula nada).
- **Salida:** `muestra_items.xlsx` (una fila por ítem) + `lista_*.json`, `detalle_*.json`,
  `red_api.json`, `00_menu_page.html`, `resumen.json`.
- **Validado:** OFICINAS & DEPAS `20600488121` (3 compras) y MULTISERVICIOS SHEVALCHE
  `20615643735` (24 compras / **112 ítems**, 202610).

## Rendimiento (medido en Shevalche, 202610)
- 24 comprobantes → 112 ítems. API `consultacpe` ≈ **0,31–0,43 s/comprobante**.
- Pausa prudente **3–5 s** entre comprobantes (ritmo gentil heredado de asistente-sol,
  para no gatillar el rate-limit de SUNAT) → **efectivo ≈ 4,4 s/comprobante**.

**Estimación (consulta + listado paginado + ~1,5 min fijo de login/pantallas):**

| Comprobantes/mes | Tiempo aprox. |
|---|---|
| 100  | ~9–10 min |
| 300  | ~23–24 min |
| 1000 | ~75–78 min |

- **Palanca = la pausa.** A ~1 s/comprobante, 1000 caería a ~22 min, pero sube el riesgo
  de corte. Recomendado mantener 3–5 s y, para volúmenes altos, **repartir en varios
  días** (SUNAT tiene tope diario de consultas por RUC).

## Qué FALTA
- **Ventas (RVIE): EN PAUSA (prioridad baja).** El endpoint candidato
  `…/rvie/propuesta/web/propuesta/{AAAAMM}/busqueda` responde **500** (no confirmado);
  su fallo NO marca error del run (`endpoint_no_confirmado`). Pendiente: capturar el
  request real de la SPA de RVIE (o confirmar el endpoint oficial) y escribir su parser.
- **Sara `10467016283` (persona natural): PENDIENTE de correr.** Verificar si un RUC no
  obligado al SIRE trae propuesta o sale vacía.
- **Perfil mínimo del usuario secundario:** documentado en `comp_diag_PERFIL_SECUNDARIO.md`
  (opciones SOL de Comprobantes + SIRE). El RUC debe tener correo/celular validados.
- **API SIRE oficial** (client_id/secret, OAuth2 en `api-seguridad.sunat.gob.pe`): más
  estable que sniffear la sesión; a evaluar si esto va a producción.

## Próximos pasos (cuando se retome, con tu OK)
1. Confirmar el endpoint RVIE y reactivar ventas.
2. Correr Sara y documentar el caso de RUC no obligado.
3. Diseñar la persistencia (modelo comprobante/ítem) y la ingesta — recién después,
   igual que en decl-diag (sin migrar sin aprobación).
