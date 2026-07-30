# WhatsApp agent — Enrutamiento por menú + handlers (DISEÑO)

> **Estado: DISEÑO, no implementado.** Escrito 2026-07-28. Continúa la visión de
> [`28-whatsapp-agent-phase2-design.md`](./28-whatsapp-agent-phase2-design.md),
> pero **reemplaza** la idea de "dispatcher que infiere la intención del mensaje"
> por **enrutamiento dirigido por el usuario** (menú de botones). Decisiones
> tomadas en conversación con el consultor. El runtime vive en el repo aparte
> `visar_fastapi`; su plan de implementación irá en un doc propio.
>
> **Revisión 29-jul-2026 (reunión con dirección + diseño de menús).** Se añaden:
> (1) **Agendar difiere para cliente nuevo vs recurrente**; (2) **menús generados por
> el LLM** (pendiente de definir); (3) **interpretación de entrada + escapes** como
> capacidad **transversal** (menú y cada paso), con sub-menú de escape, modelo de
> **confirmación/recuperación** y **rewind-and-replay** para editar; (4) **tipos de
> mensaje interactivo** (reply buttons ≤3 vs list ≤10 filas); (5) **umbral de hand-off
> = "último momento responsable" (pago)**; (6) **la lista de insights del cliente vuelve
> al alcance y se guarda EN Odoo** → **primer write runtime→Odoo**. Ver secciones
> respectivas.

## Cambio de enfoque respecto al diseño 28

El doc 28 planteaba un dispatcher que **adivina** a qué trabajo pertenece cada
mensaje entrante. Eso es lo más difícil de todo (detección de intención sobre
lenguaje libre, ambigua y cambiante).

**Decisión: no adivinar.** El número presenta un **menú de opciones** (botones
interactivos de WhatsApp) y el cliente **elige** qué quiere hacer. El
enrutamiento lo hace el usuario, no un clasificador. Esto elimina el problema más
difícil casi por completo — igual que un árbol telefónico de banco ("1 para X, 2
para Y"), pero con botones tocables.

## Modelo mental: canal → menú → handler

```
Mensaje entrante
   ↓  menú interactivo (botones)
   ├─ Información         → LLM + prompt editable          (HECHO — Fase 2a)
   ├─ Servicio existente → LLM + lecturas en vivo de Odoo  (a construir — lectura)
   ├─ Agendar            → NUEVO:     flujo determinista ──┐
   │                       RECURRENTE: flujo asistido LLM ─┴► deep link al wizard web
   │                       (según identidad + historial)
   └─ Otra cosa          → hand-off a un asesor humano      (catch-all)

   Si el cliente ignora el menú y escribe texto libre →
       fallback "¿quisiste decir…?" (infiere ruta + confirma) → enruta
```

El número sigue siendo un **canal** con un **dueño** (el runtime FastAPI) y
varios **handlers** detrás. Lo nuevo es que el enrutado a handler lo decide el
cliente tocando un botón.

## Taxonomía de handlers — un caso de uso NO es siempre un prompt

Regla mental clave (heredada del doc 28, sigue vigente): modelar todo como
"prompts" es un error. Cada trabajo usa un **mecanismo distinto**:

| Handler | Qué es | Mecanismo | Estado |
|---|---|---|---|
| **Información** | LLM improvisando dentro de reglas | **Prompt** (editable en Odoo) | Hecho (Fase 2a) |
| **Servicio existente** | Responder con datos reales del cliente | **LLM + inyección de datos leídos de Odoo** | A construir (lectura) |
| **Agendar (nuevo)** | Cuestionario fijo → hand-off al web | **Flujo determinista** — sin LLM | A construir (fase propia) |
| **Agendar (recurrente)** | Preguntas a la medida del historial → hand-off (más pre-llenado) | **Flujo asistido por LLM** (payload estructurado) | A construir (fase propia) |
| **Otra cosa** | Lo no listado | **Hand-off humano** (o catch-all) | A construir (simple) |
| *(futuro)* Salientes | "Tu técnico va en camino" | **Template disparado por evento Odoo** — sin LLM | Fase 2b (doc 28) |

## El menú y sus opciones

- El número presenta un **menú**; el cliente **elige**. No es "escribe 1, 2, 3": son
  elementos interactivos nativos de WhatsApp (ver la siguiente sección).
- **Opción "Otra cosa / Hablar con un asesor"** para todo lo no listado. Al
  *anticipar* el caso "ninguna de estas", se **previene** el texto libre y se da salida
  a lo no cubierto (→ hand-off humano).
- Las **salidas** de un flujo (atrás, menú, cancelar…) y el manejo de texto libre son
  una **capacidad transversal**, no del menú principal: ver "Interpretación de entrada
  y escapes".

## Tipos de mensaje interactivo (botones vs lista)

Dos formatos nativos (confirmado en pywa 4.3.1 / WhatsApp Cloud API):

| | **Reply buttons** (`Button`) | **List message** (`SectionList`) |
|---|---|---|
| Cuántas opciones | **≤ 3** | **≤ 10 filas** en total, en hasta 10 secciones |
| Cómo se ve | Botones inline bajo el mensaje | Un botón que **abre** una lista; se elige una fila |
| Taps | 1 (tocar el botón) | 2 (abrir lista → elegir) |
| Título | ≤ 20 chars | fila ≤ 24 chars |
| Descripción por opción | ❌ | ✅ (≤ 72 chars) |
| Agrupar | ❌ | ✅ (secciones con título) |
| Payload oculto | `callback_data` | `callback_data` por fila |

**Cuándo cada uno:**
- **Reply buttons:** confirmaciones **Sí/No** y pasos de ≤3 opciones (interior/exterior/ambos).
- **List message:** **4–10 opciones**, o cuando ayuda una descripción, o para **agrupar**.
  La mayoría de nuestros menús (principal = 4, escapes = 5, tramos) son **listas**.

**Dos cosas que esto habilita / limita:**
- **Las secciones dejan convivir respuestas + escapes en la misma lista** (dentro del
  tope de 10). Ej.: sección "Servicios" con las respuestas + sección "Opciones" con
  *Atrás* / *Cancelar*. Así, en pasos que usan lista, los escapes van incluidos; en pasos
  de ≤3 reply-buttons (sin espacio) los escapes quedan por palabra clave + sub-menú de escape.
- **Tope duro de 10 filas.** Un paso con >10 opciones (muchas dimensiones, muchos rangos)
  **no** cabe: hay que **agrupar en secciones** o **paginar** con una fila "Ver más…".

## Interpretación de entrada y escapes (capacidad transversal)

**No es una función del menú principal: es una propiedad de CADA punto donde se espera
que el usuario elija** — el menú y **cada paso** de un flujo determinista. En todos, el
cliente puede tocar un botón… o escribir texto libre igual. Se maneja igual en todos,
parametrizado por **el conjunto de opciones válidas del momento**.

**Orden de resolución en cualquier punto:**
1. **Tap de botón válido** → avanzar. (Camino feliz.)
2. **Texto libre → primero, intentar mapearlo a una respuesta del paso actual** (reglas
   por palabra clave; LLM ligero solo si hace falta, **acotado** a las opciones del paso).
   Si mapea → proceder. *(Así "es una bodega con patio" se lee como respuesta, no como
   intento de salir.)*
3. **Si NO mapea a una respuesta → mostrar el sub-menú de escape** (no se adivina el
   meta-intent: se presenta como botones/lista). Esto además absorbe el **gibberish**: el
   troll recibe botones.
4. **Contador de strikes bajo:** si tras N intentos en el mismo paso no hay entrada válida
   → ofrecer menú/asesor una vez y, si sigue, **terminar y resetear** con cortesía. N bajo
   a propósito: evita quemar tokens y abuso.

**Los meta-intents (escapes) son varios, con comportamientos distintos:**

| Escape | ¿Confirma? | Efecto |
|---|---|---|
| **Volver a la pregunta** | No | Re-muestra la pregunta actual (costo cero) |
| **Atrás** (un paso) | No | Retrocede un paso; reversible (avanzar de nuevo) |
| **Menú** (salir del flujo) | **Sí** | Deja el flujo; un tap accidental borraría avance |
| **Asesor** (hand-off humano) | **Sí** | Cambia con quién habla |
| **Cancelar** (terminar) | **Sí** | Termina la interacción |

**Principio de recuperación (más importante que confirmar):** salir de un flujo **NO
destruye el avance** — el estado (objeto estructurado) se **estaciona** y es **retomable**
(*"tenías una cita a medio agendar, ¿continuar?"*). Solo **Cancelar** lo borra (por eso
confirma). Así, aun un tap equivocado en *Menú* es recuperable. Regla combinada:
**confirmar todo lo que descarte avance o termine la sesión, y además conservar el estado**
(cinturón y tirantes). Asumir que la gente se equivoca.

**Afijos siempre disponibles:** *Atrás* (si hay paso previo) y *Cancelar* — como filas de
lista donde el paso use lista, y **siempre** por palabra clave ("atrás", "cancelar") con
una línea de ayuda. El resto (Menú, Asesor, Volver) aparece en el sub-menú de escape.

**El LLM siempre acotado:** clasifique o interprete, mapea **al conjunto dado** (las
opciones del paso, o las rutas), **nunca inventa** una opción/ruta nueva. Misma disciplina
que la "generación constreñida" de menús.

**Intensidad de confirmación = costo de equivocarse:** interpretar una respuesta obvia del
paso → proceder sin confirmar; cambiar de ruta / abortar → confirmar. En el menú principal,
donde un mal enrutamiento es caro, confirmar el resultado del clasificador.

## Volver atrás y editar respuestas (rewind-and-replay)

En el web el usuario navega y cambia respuestas; WhatsApp es lineal (no se edita una
burbuja enviada). Se resuelve **sin** replicar la navegación libre del web:

- **Estado por paso, estructurado** (`collected = {service, m2, cp, ...}`). Editar =
  volver a *ese* paso.
- **Rewind-and-replay, no acceso aleatorio.** Cambiar un campo **independiente** (nada
  depende de él) = cambiar solo eso y listo. Cambiar un campo del que **dependen** pasos
  posteriores (p. ej. `service` afecta qué preguntas de medida aplican) = se **re-caminan**
  los pasos posteriores. Naturalmente consistente en un chat lineal — **no** hace falta
  portar el grafo `_VISAR_STEP_CLEARS` del web.
- **Pantalla de revisión antes del hand-off** = el punto de entrada de acceso aleatorio:
  una lista con cada respuesta guardada ("Servicio: Fumigación", "Medida: 120 m²", …);
  tocar una **rebobina** a ese paso. Es el lugar natural para cazar taps equivocados.
- **El sub-flujo de estimación de m²** (cuando el cliente no sabe la medida: niveles/
  bandas/proxy) se trata como **un solo paso** para atrás/editar: se **re-corre completo**,
  no se editan sus preguntas internas. Simplifica.

## Handler: Información (HECHO — Fase 2a)

LLM + prompt del sistema editable desde Odoo (`visar.agent.prompt`). Poco estado
relevante: cada pregunta es bastante autocontenida. Ya funciona (cotizaciones,
cobertura, rechazo de temas fuera de alcance). No requiere trabajo nuevo aquí.

## Handler: Servicio existente (a construir — SOLO LECTURA)

**Qué hace:** el cliente pregunta *"¿qué servicios tengo agendados y para cuándo?"*
El agente identifica al cliente por su teléfono, **lee de Odoo** sus servicios
pendientes/programados (qué servicio, fecha y hora) y responde.

**Expansión futura** (dejar la puerta abierta, no construir aún):
- Suscripciones: *"¿cuándo es mi próximo servicio?"* si se agenda automáticamente.
- Sugerencias: *"¿cuándo conviene tu próximo servicio?"*.

**Requiere:**
- Resolución **teléfono → `res.partner`** (ver "Identidad" abajo).
- Un **método RPC nuevo de solo lectura** en `visar.agent.tools` (p. ej.
  `agent_customer_services(payload)`) que devuelva los servicios del cliente desde
  `calendar.event` / `sale.order` / `project.task`. Acotado, tipado, sin nombres de
  modelo ni dominios — mismo principio que los métodos actuales.
- **Cadena de datos (verificada):** `res.partner` → `sale.order.partner_id` →
  `sale.order.line.calendar_event_id` (cita) y `.task_id` (`project.task` FSM).
  El teléfono vive en los campos estándar `res.partner.phone` / `mobile`.

> **⚠️ Decisión pendiente — cruza el límite de "sin datos de clientes".** Hoy el
> grupo del agente (`group_whatsapp_agent_readonly`) solo tiene ACL de **catálogo y
> precios**; a propósito **no** ve ventas, citas ni clientes. "Servicio existente"
> necesita leer `res.partner`, `calendar.event`, `sale.order(.line)` y `project.task`.
> Dos caminos: **(a)** ampliar las ACL de solo lectura a esos modelos (amplía la
> superficie del usuario share), o **(b)** que *ese* método use `sudo()` con una
> respuesta tipada y mínima (rompe la convención actual de "sin sudo", pero no expone
> los modelos por ACL). Resolver al implementar este handler.

## Identidad: teléfono → cliente

- **Clave:** el número de teléfono del remitente que entrega WhatsApp.
- **Búsqueda:** `res.partner` cuyo teléfono **normalizado** coincida.
- **Gotcha real — normalización.** WhatsApp entrega algo como `5218112345678`
  (código de país, el "1" de móvil MX tras 52, sin `+`); los campos de teléfono en
  Odoo son texto libre e inconsistente. Hay que normalizar **ambos lados** (quitar
  formato, manejar código de país y el "1"). Es la fuente clásica de "¿por qué no
  encontró al cliente?".
- **Principio:** el historial estructurado **vive en Odoo**; se **lee en vivo**,
  nunca se copia. Copiarlo crea el problema de datos rancios / actualización manual.

## Handler: Agendar (a construir — flujo + hand-off web)

**Fase propia; la más grande y la más arriesgada. No se construye primero.**

Patrón común a ambos sub-flujos: WhatsApp conduce la conversación y hace **hand-off al
wizard web** de `visar_appointment` (variante combinada, precio, combos, add-ons, pago).
Lo que **cambia** entre nuevo y recurrente es *cuántas preguntas* se hacen y *cómo* se eligen.

**Umbral de hand-off — "el último momento responsable".** El objetivo del canal es
**simplificar**: mantener **lo más posible dentro de WhatsApp** (servicio, medida —incl.
estimación—, zona, precio, y de ser viable el horario) y **saltar al web lo más tarde
posible**. En la práctica ese límite es el **pago** (no se cobra una tarjeta en el chat).
No se salta al web temprano solo para ahorrar desarrollo: eso des-simplifica. La única
excepción es un paso que resulte **desproporcionado** de replicar bien en chat — se evalúa
**por paso** en la etapa F, no de entrada. El mecanismo de hand-off (deep link) es el mismo
sin importar dónde caiga el umbral; solo cambia cuánto viaja pre-llenado.

### Cliente nuevo — flujo determinista

- No sabemos nada de él. WhatsApp conduce el cuestionario (opción múltiple, sin LLM):
  servicio, dimensión, medida (incl. el sub-flujo de estimación si no la sabe), CP, y
  hasta donde llegue el umbral (precio, y de ser viable el horario).
- Es el flujo "genérico que junta la mayor información posible" — correcto cuando no hay
  historial.
- Hand-off al wizard para lo que quede (como mínimo **pago**; según el umbral, también
  dirección/horario).

### Cliente recurrente — flujo asistido por LLM

El cliente ya existe en Odoo: no queremos que repita lo que ya sabemos (dirección,
m² de casa/jardín) ni tratarlo con el mismo cuestionario genérico. Dos ideas:

- **No re-preguntar lo conocido.** Se leen sus datos de Odoo (dirección, tamaños,
  zona) y se **omiten** esos pasos. El hand-off al wizard queda **casi completo** —
  idealmente solo confirmar y pagar. (Refuerza la opción A: pre-llenamos más.)
- **Preguntas conscientes del historial.** Con sus servicios previos + insights se
  formulan preguntas a la medida en vez de arrancar de cero. Ej.: si tuvo fumigación
  por roedores con estaciones instaladas → *"¿La plaga regresó, es una plaga nueva, o
  solo quieres mantenimiento preventivo?"* en lugar de "¿qué servicio quieres?".

**Por qué "asistido por LLM" y no determinista puro:** las preguntas dependen del
historial de cada cliente, que es demasiado variado para un árbol fijo. Pero —**matiz
crítico**— *asistido por LLM ≠ LLM libre*:

- El LLM **elige y redacta** preguntas/opciones a partir de un **conjunto acotado** de
  servicios/acciones que el sistema le entrega (vía tool con el catálogo + historial).
- Cada opción que ofrece **debe mapear a una acción concreta** (un `service_code` /
  una rama del wizard). Si el LLM inventa una opción libre, el sistema no sabría qué
  hacer al tocarla.
- La **salida sigue siendo el mismo payload estructurado** que el flujo determinista
  (`{service, m2, cp, ...}`), para que el hand-off funcione igual. El LLM cambia
  *cómo se conversa*, no *qué se entrega*.

> Este sub-flujo depende de tener **insights del cliente** (ver "Memoria e insights")
> y se cruza con la idea de **menús generados por el LLM** (siguiente sección).

## Menús generados por el LLM (IDEA — pendiente de definir)

Dirección preguntó si el LLM puede **generar los menús/opciones interactivos desde
cero**. Encaja justo con el caso recurrente (opciones a la medida del historial).
**Requiere pensarse más antes de escribirlo como diseño cerrado.** Lo que ya está
claro y hay que respetar:

- **Generación CONSTREÑIDA, no libre.** El sistema le da al LLM el conjunto válido de
  acciones (servicios, ramas); el LLM decide **cuáles ofrecer y cómo redactarlas** dado
  el historial. No inventa acciones que el sistema no pueda ejecutar.
- **Cada opción mapea a una acción.** Al generar un botón, tiene que venir con su
  `id`/acción asociada, no solo texto, o al tocarlo no sabremos qué hacer.
- **Límites de WhatsApp:** máx. 3 reply buttons (o list message para más); títulos
  cortos. El LLM debe generar dentro de esos límites.
- **Costo/latencia:** generar menús por mensaje = llamadas LLM extra. Aceptable en el
  flujo recurrente (alto valor), a vigilar en rutas de alto volumen.
- **Abierto:** ¿el LLM arma el menú completo, o solo re-ordena/re-redacta opciones de
  una plantilla? ¿Cómo se validan las opciones generadas antes de enviarlas? Definir
  al llegar a esta fase.

### Hand-off: deep link con parámetros — **opción A (ELEGIDA)**

- El runtime construye una **URL** con lo recogido; el **navegador del cliente** la
  abre; una **ruta nueva** del wizard **pre-siembra** la sesión desde los parámetros
  y el cliente termina en el web (dirección, horario, pago).
- **Preserva la regla de solo-lectura:** el runtime **no escribe** en Odoo — solo
  arma un link. Quien "escribe" es el navegador del propio cliente al abrirlo.
- **Manipular los parámetros no es riesgo:** Odoo **re-valida** precio y lógica del
  lado servidor; alterar el link solo cambia lo que el cliente ve, no el cobro real.

**Verificado (28-jul-2026) — NO existe pre-llenado por URL hoy.** El wizard vive en
`visar_appointment/controllers/appointment.py` (una clase grande). La sesión
`request.session['visar_booking']` se arma **solo** con POSTs secuenciales, paso a
paso; ninguna ruta acepta parámetros para sembrarla. Hay que **construirla**. Forma
limpia (confirmada contra el código):

- **Ruta GET nueva** tipo `/appointment/visar/booking/seed?...` que recibe los mismos
  **primitivos** que el agente ya maneja (códigos de servicio, m², CP, respuestas de
  calificación), los **valida con los resolvers existentes** y redirige al paso que
  toque (`_visar_wizard_next` / `_visar_step_url`).
- **Pasar INPUTS, no valores computados.** `items`, `zone_id` y `service_pools` son
  **server-computed** (IDs de `appointment.resource`, pools re-derivados de zona+items
  en vivo) — no se pueden mandar como valores opacos en la URL. Se mandan los inputs
  (servicio + m² + CP) y se **re-corren los resolvers** — exactamente lo que ya hace
  `visar.agent.tools._agent_build_items`. Buena señal: el agente y el wizard
  compartirían el mismo camino de resolución.
- **Respetar el grafo de dependencias** (`_VISAR_STEP_CLEARS`, en el controlador):
  `motivo → plagas → cobertura → interior/exterior → tramos`. Un pre-llenado parcial
  o inconsistente lo **poda** silenciosamente `_visar_clear_downstream`. La siembra
  debe dejar un estado internamente consistente (o parar en el primer paso incompleto).
- **Ojo con la bifurcación de valoración** (`requiere_valoracion`): reencamina a otro
  `mode`/sesión. La siembra debe decidir en qué rama cae.
- El hand-off transporta **todo lo recogido en WhatsApp**; lo que quede se termina en el
  web (según el umbral, como mínimo el **pago**; posiblemente dirección/horario). La
  máquina nativa de partner/slot/pago vive en el web.

### Opción B (FALLBACK — documentado, NO implementar aún)

- El runtime hace `POST` a un **endpoint dedicado** de Odoo que crea la sesión
  pre-llenada y devuelve un token/URL. Es el **primer WRITE runtime→Odoo**, así que
  **rompe la regla de solo-lectura** deliberadamente.
- **Solo** si la opción A da problemas en pruebas (payload que no cabe en el link,
  estado que no se reconstruye desde params, etc.). Se documenta como salida; **no
  se construye** hasta tener evidencia de que A no alcanza.

### Estado del flujo = payload del hand-off (idea clave)

El estado que se persiste para **sobrevivir reinicios** *es el mismo objeto* que se
serializa al hand-off: `{service: FUM_INT, m2: 120, cp: 64000, ...}`. Persistir el
estado del flujo y armar el payload del web **son el mismo trabajo**. Hacer el flujo
*stateful* no es sobrecosto por robustez: produce directamente lo que se entrega al
wizard.

## Memoria e insights — decisiones

- **Insights del cliente: EN ALCANCE, guardados EN Odoo** *(revierte 29-jul la
  decisión previa de descartarlos)*. Dirección los pidió explícitamente.
  - **Qué son:** una capa **blanda** de conocimiento sobre el cliente que **no vive
    en ningún campo estructurado** — "preocupado por roedores recurrentes", "prefiere
    plan preventivo", "tiene mascotas". Es lo que alimenta las **preguntas
    conscientes del historial** del flujo recurrente.
  - **Distinción que se mantiene:** el **historial estructurado** (qué servicios, qué
    fechas, qué productos/estaciones instaladas) **NO** se copia — ya vive en Odoo y
    se **lee en vivo**. Los insights son solo lo *blando* que no cabe en un campo. No
    duplicar datos estructurados como "insight".
  - **Dónde:** modelo nuevo en Odoo (p. ej. `visar.customer.insight`) ligado a
    `res.partner`. Visible para el staff y respaldado — de ahí que dirección lo
    quiera en Odoo, no escondido en el runtime.
  - **⚠️ Consecuencia — PRIMER write runtime→Odoo.** Guardar insights en Odoo obliga a
    **escribir** en Odoo. Camino probable: el runtime **extrae** insights (paso LLM
    sobre el transcript) y los **escribe** por un **método RPC de escritura acotado**,
    exclusivo del modelo de insights (no abre escritura general). Es el primer cruce
    **deliberado y acotado** de la regla de solo-lectura. (Nota: el hand-off de
    scheduling por opción A **sigue** sin escribir; este write es solo para insights.)
  - **El trabajo real no es guardarlos, es el bucle de extracción/fusión:** cuándo
    correrlo (¿al cerrar conversación?), cómo **fusionar** con los previos sin duplicar
    ni contradecir, cómo **caducar** los viejos, y **privacidad** (son notas sobre
    personas: backups, visibilidad de staff, borrado/consentimiento). Diseñar con cuidado.
- **Notas operativas** (perro, código de portón, horario preferido): **FUTURO, no
  crucial ahora.** Son tema del **técnico en el momento de la ejecución**, no del
  agente de reserva. Cuando se hagan, encajan como insights estructurados o campo en
  el partner — no como memoria improvisada del LLM.
- **Historial de conversación** (los turnos del chat actual): hoy en **memoria**
  (`InMemoryConversationStore`), se pierde al reiniciar el runtime. **Diferible**
  mientras sea solo Q&A (bajo riesgo, cada pregunta es autocontenida). El
  `ConversationStore` ya es un *seam* para persistir (SQLite/Postgres/Redis).
- **Estado de flujo (Agendar):** **pequeño y estructurado.** Persistirlo se vuelve
  **obligatorio cuando llegue scheduling** (= el payload del hand-off). No hace
  falta persistir el transcript completo: basta el estado (`route`, `step`,
  `collected`). Llega **con** scheduling, no antes.

## Techo de lo configurable por no-técnicos

- **SÍ (UI de Odoo):** las **etiquetas** de las opciones del menú y los **prompts**
  de las rutas que usan LLM (Información y, más adelante, Servicio existente).
- **NO (código):** la **lógica de los flujos** deterministas (Agendar). Los flujos
  los construye un desarrollador y solo se **exponen** como opción de menú. Los
  consultores moldean el texto y el comportamiento de las rutas de IA; no arman
  flujos sin código.

## Decisiones tomadas (resumen)

1. Enrutamiento **dirigido por menú**, no por inferencia de intención.
2. Opción **"Otra cosa"** explícita → hand-off humano; previene el texto libre.
3. **Interpretación de entrada es transversal** (menú y cada paso de flujo): mapear el
   texto libre a la opción del momento; si no mapea → **sub-menú de escape** (no adivinar).
4. **Escapes** = {Volver, Atrás, Menú, Cancelar, Asesor}. **Confirmar** lo que descarta
   avance o termina (Menú/Asesor/Cancelar); **estacionar** el estado al salir (solo
   Cancelar lo borra). **Strike counter bajo** → terminar con gibberish/abuso.
5. **Editar respuestas = rewind-and-replay** (no acceso aleatorio); **pantalla de
   revisión** antes del hand-off como entrada; estimación de m² = **un solo paso**.
6. **Tipos interactivos:** reply buttons (≤3; confirmaciones/pasos chicos) vs list (≤10
   filas, secciones, descripciones). Secciones dejan convivir respuestas+escapes.
   **Tope de 10 filas** → agrupar/paginar.
7. Agendar **difiere nuevo vs recurrente**: nuevo = determinista; recurrente = asistido
   por LLM (no re-pregunta lo conocido, usa historial), **mismo payload**.
8. **Menús generados por el LLM:** aprobado para explorar, **pendiente de definir**;
   regla firme = **generación constreñida** (opciones que mapean a acciones).
9. **Umbral de hand-off = "último momento responsable" (pago).** Mantener lo más posible
   en WhatsApp; saltar al web al final, o antes solo si un paso es desproporcionado.
10. Hand-off por **deep link (A)**; **B** documentado como fallback, **sin construir**.
11. **Insights del cliente: EN alcance, guardados en Odoo** → **primer write
    runtime→Odoo**, acotado al modelo de insights. Notas operativas: futuras.
12. Persistir **estado de flujo** (= payload), no el transcript completo.
13. Identidad e historial **estructurado**: **lectura en vivo** de Odoo por teléfono;
    solo los **insights blandos** se almacenan (en Odoo).
14. **LLM siempre acotado:** interpreta/clasifica dentro del conjunto dado, nunca inventa.

## Orden sugerido (el plan detallado irá en doc aparte)

1. **Menú + handler de menú + "Otra cosa"** (hand-off humano). Esqueleto de rutas.
2. **Dispatcher mínimo:** elige handler según el botón elegido.
3. **Interpretación de entrada + escapes** (capacidad transversal): mapear texto libre
   a la opción del momento; si no, sub-menú de escape + confirmación/estacionado. Se
   construye general para reusarla en los flujos.
4. **Servicio existente** (ruta de lectura): teléfono→partner + método RPC nuevo
   (aquí se decide el cruce de ACL de datos de cliente).
5. **Insights del cliente:** modelo `visar.customer.insight` en Odoo + método RPC de
   **escritura acotado** + bucle de extracción/fusión en el runtime. *(Primer write.)*
6. **Persistencia del `ConversationStore`** (cuando haga falta / antes de scheduling).
7. **Agendar — nuevo:** flujo determinista pre-calificador + ruta `seed` + deep link.
8. **Agendar — recurrente:** flujo asistido por LLM (usa insights + historial) +
   pre-llenado ampliado. Cruza con "menús generados por LLM". **Fase propia, al final.**

> Recomendación de alcance: 1–4 primero (baratos, no dependen de las tripas del
> wizard). Insights (5) habilitan el flujo recurrente y son valiosos por sí solos.
> Tratar **scheduling (7–8) como fase separada**, tras verificar el pre-llenado por
> URL y con el esqueleto de enrutamiento ya funcionando.
