# alerta.pe — notas para Claude

- Responder en español peruano con tuteo, nunca voseo.

## REGLA DURA de SOL/SUNAT (seguridad)
alerta.pe **solo navega y consulta** en SOL/SUNAT. **Nunca** marca casillas, acepta,
continúa, registra ni presenta nada a nombre del contribuyente. Si una pantalla
contiene "Declaro", "acepto", "Generador", "condiciones" o un **checkbox de
aceptación** (p.ej. "Declaro tener conocimiento y acepto las condiciones para ser
Generador Electrónico" — condición PERMANENTE), se **aborta esa pantalla de
inmediato, sin hacer clic en nada**, y se reporta `pantalla_de_aceptacion (NO se
tocó)`. Vale para TODOS los modos (diag y worker). Guardián: `guardian_aceptacion.py`
(`detectar_aceptacion(page)`).

**Única excepción — interstitial "VALIDA TUS DATOS DE CONTACTO"** (correo/celular no
validados): se permite SOLO **cerrar el aviso ("Finalizar")** y **"Continuar sin
confirmar"** (ninguno cambia datos), para poder seguir navegando. **NUNCA** el enlace
"Aquí"/actualizar, **nunca** enviar/validar códigos, **nunca** escribir en campos de
contacto. Se reporta como `contacto_sin_validar` (alerta útil al contador) y se
enmascaran correo y celular en volcados/capturas.
