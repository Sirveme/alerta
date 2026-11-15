# Perfil mínimo del usuario secundario SOL — Comprobantes (comp-diag)

Para que alerta.pe lea compras/ventas con ítems de un RUC, el usuario SOL que el
contador registra (principal o secundario) necesita, en **Administración de usuarios
secundarios**, estas opciones habilitadas (solo lectura; alerta.pe solo navega y
consulta):

## Comprobantes de pago (api-cpe — el detalle con ítems)
- **Comprobantes de pago → Comprobantes de Pago → Consulta de Comprobantes de Pago →
  "Nueva Consulta de comprobantes de pago"**  (código de menú `11.38.1.1.1`).
  Abre la SPA "Consulta de Comprobante de Pago" (pestaña Individual: Emitido/Recibido,
  RUC Emisor, Tipo, Serie-Número). Es la que devuelve el JSON con la descripción de
  cada ítem.
  (La opción vieja "Consultas Facturas y Notas" `11.5.3.1.2` es la JSP antigua, NO
  sirve: no trae los ítems en JSON.)

## SIRE — la lista del período
- **Registros Electronicos → Registro de Compras Electronico → "Gestion de Compras"**
  (código `60.2.2.1.1`). Da la propuesta RCE (lista de comprobantes de compras del
  período).
- **Registros Electronicos → "Gestión de Ventas e Ingresos Electrónicos"**
  (código `60.2.1.1.1`). Propuesta RVIE (ventas). *(En pausa: endpoint aún no
  confirmado.)*

## Notas
- **Datos de contacto validados**: SUNAT puede bloquear con el aviso "Valida tus
  datos de contacto" (correo/celular). El contador debe validarlos en SOL; si no, las
  pantallas pueden no cargar. alerta.pe solo cierra ese aviso ("Finalizar" / "Continuar
  sin confirmar"), nunca actualiza ni envía códigos.
- **NO se requiere** ninguna opción de emisión, registro, presentación ni aceptación.
  Si el usuario secundario no tiene estas opciones, el diag reporta `sin_acceso` y el
  contador debe asignarle el perfil.
- El código de la SPA de comprobantes puede aparecer 2 veces según el perfil; el diag
  vuelca `00_menu_page.html` para confirmar el código real del RUC.
