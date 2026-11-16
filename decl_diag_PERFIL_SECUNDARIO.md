# Perfil mínimo de usuario secundario SOL — Declaraciones y Pagos

Guía corta para el contador. alerta.pe **solo lee** tu buzón y tus consultas de
declaraciones y pagos; **nunca** presenta ni paga nada. Para que funcione con un
**usuario secundario** (recomendado, en vez de tu Clave SOL principal), ese
usuario necesita unas pocas opciones habilitadas. Abajo está el mínimo.

## Qué opciones habilitar (lo esencial)
En SOL (Clave SOL del **titular/principal**) →
**Empresas › Administración de Usuarios Secundarios** → crea o edita el usuario
secundario → **Asignar opciones / perfiles**, y marca:

| Para | Menú SOL (ruta) | Sirve para |
|---|---|---|
| **Consulta general de declaraciones y pagos** | *Mis declaraciones y pagos › Consulto mis declaraciones y pagos › Consulta general* | Rejilla período × tributo: qué se declaró y **cuánto se pagó** (HTML) |
| **Consulta de declaraciones juradas y pagos** | *Mis declaraciones y pagos › Consulto mis declaraciones y pagos › Consulta de declaraciones y pagos* | Lista de presentaciones por fecha + constancias |

Con esas dos, alerta.pe arma lo "en regla" (declarado y pagado).

## Opcional (solo si quieres que también vea detracciones)
| Para | Menú SOL (ruta) |
|---|---|
| **Ingreso como recaudación** | *Mis trámites y consultas › Detracciones › Consulta de ingreso como recaudación* |
| **Consulta de NPD** | *Mis trámites y consultas › Detracciones › Consulta de NPD* |

## Lo que NO necesita (déjalo sin marcar)
- **Presentar** declaraciones (PDT / Mis declaraciones y pagos › *Presento…*).
- **Pagar** / generar NPS / boletas.
- Comprobantes de pago electrónicos, GRE, libros, RUC, etc.

Cuanto menos tenga, mejor: alerta.pe no lo usa y así el acceso queda al mínimo.

## Cómo saber si quedó bien
- Si el usuario secundario entra a SOL y **ve** "Consulta general" y "Consulta de
  declaraciones y pagos" sin aviso de permisos → está listo.
- Si al abrir una de esas opciones SOL responde **"no cuenta con los permisos"**
  o similar, falta marcar esa opción en su perfil. alerta.pe lo reporta como
  **"sin acceso"** (no como "sin datos"), para que sepas que hay que ampliar el
  perfil, no que no haya movimiento.

## Datos técnicos (por si tu TI los pide)
Las opciones corresponden a estos códigos de menú SOL:

| Opción | Código | Backend |
|---|---|---|
| Consulta general | `12.8.1.1.2` | `/cl-ti-itconsdecdetinf/consultaS01Alias?accion=consgeneral` |
| Consulta de DDJJ y pagos | `12.1.1.1.4` | `/cl-ti-itdeclpagcon-mepeco/cdpS01Alias` |
| Ingreso como recaudación (opc.) | `12.4.1.1.3` | `/cl-ti-itconsimput/imputS01Alias` |
| Consulta de NPD (opc.) | `12.4.1.1.6` | `/cl-ti-itnumpagdetr/consultaIndividual.htm` |

> Nota: los nombres exactos de los menús pueden variar levemente entre perfiles de
> SUNAT. Si no encuentras una opción por su nombre, búscala por el agrupador
> *"Mis declaraciones y pagos"* (consultas) y *"Detracciones"*.
