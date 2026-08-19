# Encargo: el flujo del wizard bajó al modelo + `agent_booking_step`

> Contexto: [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md)
> §10.5. Esto **no** añade funcionalidad al cliente final: mueve reglas de sitio
> y las expone por RPC. Por eso lo que más importa verificar es que **el wizard
> web sigue comportándose exactamente igual**.

Commit nuevo en `main` de `/opt/custom`. **Módulos a reiniciar (NO hace falta
`-u`): `visar_appointment`, `visar_whatsapp_agent`.** Es Python puro — ningún
campo, modelo, ACL, vista ni dato nuevo. Si algo pide `-u`, avísame: significa
que se me coló algo que no debía.

Reglas de siempre: escrituras solo sobre la copia, nunca `commit()` en shell, no
modifiques código.

## Qué cambió

El controlador web tenía cuatro reglas del cuestionario atadas a
`request.session`, y el agente de WhatsApp necesita las mismas por RPC. Bajaron a
`appointment.type` (`visar_appointment/models/appointment_wizard_flow.py`, nuevo):

1. **Podar** — qué selecciones quedan inválidas al cambiar un paso
   (`_VISAR_STEP_CLEARS` + la regla de prefijo `tier_*`).
2. **Secuenciar** — qué paso viene después (`_visar_wizard_next_step`), y la
   cadena posterior a la dirección (`_visar_wizard_step_after`).
3. **Normalizar** — cómo se convierte la respuesta del cliente en `selections`
   ("protección general" activa las tres categorías, "termitas" corta a
   valoración solo en la rama correctiva, la banda de exterior resuelve tramos).
4. **Ofrecer** — las opciones válidas de cada paso
   (`_visar_wizard_step_options`), ya serializadas.

El controlador ahora **delega**: sigue dueño de la sesión HTTP, los formularios y
las URLs, y nada más. Bajó de 1961 a 1664 líneas.

Encima de eso, un RPC nuevo de **lectura**: `agent_booking_step(payload)`. Recibe
el estado y la respuesta, devuelve el estado nuevo + el paso siguiente + sus
opciones. No escribe nada en Odoo.

## Qué verificar

- **V1 — el web, intacto.** Es lo único que puede romper clientes hoy. Una
  reserva web **completa** de punta a punta, con fumigación interior+exterior:
  que el total sea el mismo de siempre (1,400 = `_visar_quote_booking`, UNA línea
  combinada), que se cree la dirección de servicio y los anticipos de póliza.
  Compara contra lo que ya sabes de las rondas anteriores.

- **V2 — el indicador "Paso X de Y" y el botón Volver.** Es lo que más manoseó el
  cambio. Recorre el wizard hacia adelante y hacia atrás, edita un paso de en
  medio (cambia la cobertura después de haber elegido tramos) y comprueba que los
  tramos se sueltan y se vuelven a preguntar.
  > Aviso: en la página de **error del paso 1** (enviar sin elegir servicio) el
  > contador puede mostrar un total distinto al de antes. Es cosmético y conocido
  > —esa ruta no le pasaba las selecciones al contador—, no lo reportes como
  > fallo salvo que veas un `back_url` mal.

- **V3 — la rama de valoración.** Elige correctivo → termitas. Debe cortar a
  valoración, **saltarse las mediciones** y no ofrecer póliza. Después, la misma
  opción en la rama **preventiva**: ahí termitas NO debe cortar (y como no queda
  ninguna categoría, debe pedir que elijas otra vez).

- **V4 — pruebas.** `visar_appointment`: `test_wizard_flow` (nuevo, ~18 casos),
  más `test_booking_partner`, `test_slot_hold` y `test_poliza` que ya estaban
  verdes. `visar_whatsapp_agent`: `test_agent_booking_step` (nuevo) y los cuatro
  de siempre. Los 2 fallos de `test_partner_dedupe` siguen siendo preexistentes y
  ajenos (`assertLogs`): no los investigues.
  > Ninguna de estas pruebas se ha ejecutado todavía contra una BD. Si alguna
  > sale roja, mira **primero** si llegó a ejecutar su aserción: en la ronda 2 una
  > prueba que reventaba antes del `assert` pareció culpa del código.

- **V5 — el RPC, con el cuestionario entero.** Desde `odoo shell`, recorre
  `agent_booking_step` de principio a fin (servicios → motivo → plagas →
  cobertura → tramos → dirección → extras → póliza) pasando en cada llamada el
  `booking` que devolvió la anterior. Lo que hay que confirmar:
  1. `step` avanza y **nunca repite** un paso ya contestado — en particular,
     contestar `extras` no vuelve a preguntar `extras` (era un bug que encontré
     y corregí escribiéndolo; quiero saber si quedó bien cerrado de verdad).
  2. `options` nunca viene vacío en un paso que sí tiene opciones.
  3. Con una respuesta inválida, `error` viene lleno y `step` **no se mueve**.
  4. Todo el payload es serializable por JSON-RPC. Pruébalo **por JSON-RPC de
     verdad**, no solo en shell: un recordset colado en `options` revienta ahí y
     no en el shell.

- **V6 — paridad web ↔ agente.** El mismo cuestionario por los dos caminos
  (mismas respuestas) tiene que dar el **mismo `selections`**. Si divergen, eso
  es exactamente lo que este cambio venía a impedir.

- **V7 — costo.** `_visar_wizard_step_sequence` llama a `_visar_wizard_poliza_context`,
  que cotiza de verdad (`_visar_build_sale_lines`). En una respuesta de
  `agent_booking_step` posterior a la dirección eso puede correr **hasta 3 veces**
  (secuencia + opciones + cadena). ¿Cuánto tarda una llamada en esa fase? Si es
  caro, la salida es cachear el contexto de póliza por llamada, pero no quiero
  optimizar sin número. En el web ya pasaba algo parecido por render, así que
  compara contra una carga del paso de póliza web.

## Lo que NO es de este encargo

- Poner líder/miembros al equipo de WhatsApp (sigue pendiente, es dato).
- I-11 (el web cobra 2,400 donde la cotización dice 1,900).
- El runtime: persistencia SQLite y el motor de flujos de `visar_fastapi` van
  después, y dependen de que esto quede verificado.
