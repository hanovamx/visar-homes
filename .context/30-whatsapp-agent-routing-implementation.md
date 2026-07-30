# WhatsApp agent — Plan de implementación (enrutamiento por menú)

> **Estado: PLAN.** Escrito 2026-07-29. Es el **CÓMO** de las primeras etapas;
> acompaña a [`29-whatsapp-agent-routing-design.md`](./29-whatsapp-agent-routing-design.md)
> (el **PORQUÉ**). Solo se detallan las etapas **claras** (A, B, C). Las posteriores
> (insights, persistencia, scheduling) quedan como **notas** al final y se detallan
> al llegar — construir antes de tener todo resuelto es intencional.
>
> **Convención de repos** (cada archivo dice dónde vive):
> - **RUNTIME** = `visar_fastapi` (repo aparte) — la mayor parte del trabajo temprano.
> - **ODOO** = `visar-homes/visar_whatsapp_agent`.

## Qué se reutiliza (para NO construir de cero)

### pywa (RUNTIME) — la capa interactiva YA existe (pywa 4.3.1, `pywa_async`)

- **`Button`** (≤3 reply buttons) y **`SectionList` / `Section` / `SectionRow`**
  (lista de hasta **10 filas** en secciones, para >3 opciones): **el menú**. Las
  secciones dejan meter respuestas + escapes en la misma lista. Ver diseño §"Tipos de
  mensaje interactivo".
- **`CallbackData`** (payload **tipado** por botón): cada opción lleva su ruta/acción.
  Es el mecanismo exacto de "cada opción mapea a una acción" del diseño — no hay que
  inventar cómo asociar un botón a lo que hace.
- **`on_callback_button` / `on_callback_selection`**: eventos entrantes al tocar un
  botón / elegir una fila. **Aquí vive el dispatcher.**
- **`URLButton`**: botón que abre una URL → el hand-off de scheduling (etapa F) es
  literalmente un `URLButton` a la ruta `seed`. No se construye envío de links a mano.
- Se **extiende** el scaffold existente `register_whatsapp` (`app/handlers.py`):
  se agrega un `@wa.on_callback_button` junto al `@wa.on_message` actual; se conserva
  el patrón `asyncio.create_task(...)` + `mark_as_read` + `reply`.

### RUNTIME propio — puntos de extensión que ya existen

- **`VisarAgent.handle_message`** (`app/agent.py`) es el punto de dispatch. Hoy todo
  va al LLM; se le **antepone** la capa de menú/ruta.
- **`ConversationStore`** (`app/conversation/store.py`) — *seam* ya diseñado para
  persistir. Guarda el estado por teléfono: qué ruta/paso. Hoy en memoria; **basta
  para A–B** (persistirlo real es la etapa E).
- **Loop LLM + providers + tools** (`app/llm/*`, `app/odoo/tools.py`): se reutilizan
  tal cual para la ruta **Información** y para el clasificador ligero del fallback.
- **Sanitizador** (`app/formatting.py`) y **prompt editable** (Fase 2a) intactos.

### ODOO — patrón RPC a reutilizar

- **`visar.agent.tools`**: agregar **un `@api.model` más** siguiendo la convención
  (parámetros tipados, sin nombres de modelo/dominios, helpers `_agent_`, sin sudo por
  defecto). La cadena de datos ya está mapeada: `res.partner` → `sale.order.partner_id`
  → `sale.order.line.calendar_event_id` (cita) y `.task_id` (`project.task` FSM).

### Base Odoo — qué NO reutilizamos y por qué

- Existe el módulo **nativo `whatsapp`**, pero (doc 28) el **webhook lo posee el
  runtime**, no Odoo. **No se usa para el entrante.** (Para salientes/plantillas —
  fase 2b — se revisará entonces.)

---

## Etapa A — Menú + dispatcher (esqueleto de enrutamiento) · RUNTIME

**Objetivo:** al escribir por primera vez, el número responde con un **menú de
botones**; al tocar uno, se **enruta** al handler. Sin tocar Odoo.

- **A1 — Estado de ruta.** En `app/conversation/store.py` (modelo `Conversation`),
  agregar un campo `route` (None = "en el menú"). Es el estado mínimo del dispatcher.
- **A2 — Menú como datos.** Definir las opciones como una lista `{id, label, route}`
  en un módulo nuevo (`app/routing/menu.py`). El menú principal tiene **4 opciones**
  (Información / Servicio existente / Agendar / Otra cosa) → **`SectionList`** (los reply
  buttons topan en 3). Reservar **reply `Button`** para confirmaciones Sí/No. El
  `CallbackData` de cada fila/botón lleva el `route` id. Ver diseño §"Tipos de mensaje
  interactivo" (tope de 10 filas, uso de secciones).
- **A3 — Handler de callback.** En `app/handlers.py`, `@wa.on_callback_button` →
  extrae el `route` del `CallbackData` → despacha (misma background-task que
  `on_message`).
- **A4 — Dispatcher.** En `app/agent.py`: si la conversación **no** tiene ruta activa
  y llega texto → **mostrar menú**; si hay ruta → pasar al handler de esa ruta.
  Rutas iniciales: **Información** (handler actual = el LLM de hoy) y **Otra cosa**
  (mensaje "en seguida te contacta un asesor" + marca para hand-off humano). *Servicio
  existente* y *Agendar* se enchufan en C y F.
- **A5 — Escape (mínimo).** Palabra clave `menú`/`regresar` → vuelve al menú. Es la
  semilla del modelo transversal de escapes; el conjunto completo (Atrás/Cancelar/Asesor
  + sub-menú de escape + confirmación/estacionado) se construye en Etapa B.
- **A6 — Paridad debug.** Extender `/debug` para simular la selección de una opción
  sin WhatsApp (p. ej. `/debug/select` o aceptar un `route` en `/debug/message`).
  Mantener la regla "mismo camino que producción".

**Reutiliza:** pywa (todo lo interactivo), `VisarAgent`, `ConversationStore`, loop LLM.
**Pruebas** (con `ScriptedProvider` + `FakeOdooClient`): sin ruta + texto → menú;
tap de opción → handler correcto; escape limpia ruta; Información sigue end-to-end.

## Etapa B — Interpretación de entrada + escapes (transversal) · RUNTIME

**Objetivo:** en **cualquier** punto de elección (el menú y, después, cada paso de un
flujo) manejar texto libre y salidas de forma uniforme, parametrizado por **el conjunto
de opciones válidas del momento**. Ver diseño §"Interpretación de entrada y escapes".

- **B1 — Mecanismo parametrizado.** `app/routing/interpret.py`: función que recibe
  `(texto, opciones_válidas)` → `{match_opción | escape | none}`. **General desde el
  inicio** para que los flujos (Etapa F) la reutilicen pasando las opciones del paso — no
  una versión solo-menú.
- **B2 — Orden de resolución.** (1) tap válido → avanzar; (2) mapear texto → una opción
  del momento (reglas primero; LLM ligero **acotado** si hace falta); (3) si no mapea →
  **sub-menú de escape** (no se adivina el meta-intent).
- **B3 — Escapes.** {Volver, Atrás, Menú, Cancelar, Asesor} con el modelo del diseño:
  **confirmar** Menú/Asesor/Cancelar; **estacionar** el estado al salir (solo Cancelar lo
  borra). *Atrás*/*Cancelar* como afijos siempre disponibles (fila de lista + palabra clave).
- **B4 — Strike counter bajo** por paso: N intentos inválidos → ofrecer menú/asesor una
  vez → si sigue, **terminar y resetear** (corta gibberish y costo LLM).
- **B5 — Clasificador.** Reglas por palabra clave primero (`app/routing/classify.py`);
  LLM ligero detrás, **acotado al conjunto dado**, nunca inventa. Reutiliza el provider.

**Reutiliza:** pywa (botones/lista + secciones para escapes), provider LLM, estado de
conversación (para estacionar).
**Pruebas:** respuesta obvia se mapea sin confirmar; texto no-mapeable → sub-menú de
escape; Menú/Cancelar confirman; salir **estaciona** (retomable); N strikes → termina;
el clasificador **nunca** devuelve una opción fuera del conjunto.

## Etapa C — Servicio existente (lectura) · RUNTIME + ODOO ✅ HECHO (2026-07-30)

**Objetivo:** *"¿qué servicios tengo agendados?"* → identificar por teléfono → leer de
Odoo → responder.

> **Implementado 2026-07-30.** Decisiones tomadas: **C2 = (b) scoped `sudo()`** en el
> único método (no se amplió el ACL del usuario share); **C3 = plantilla simple, sin
> LLM** (`visar_fastapi/app/existing.py`). Método Odoo `agent_customer_services(payload)`
> en `visar.agent.tools` (sólo Python → basta reiniciar, sin `-u`). Cadena verificada:
> `sale.order.line.calendar_event_id` (`calendar.event.start/stop`, `visar_zone_id`) y
> `.task_id` (`project.task.planned_date_begin` / `stage_id`). Teléfono → partner por los
> **últimos 10 dígitos** (helper `_agent_normalize_phone` en Odoo, `_normalize_phone` en el
> fake del runtime). Runtime: 4º método en protocolo/cliente/fake + ruta EXISTING en
> `agent.py` (`VisarAgent` recibe el cliente Odoo directo). Fechas redactadas en tz/idioma
> local (`visar.agent.timezone`, default `America/Monterrey`). Órdenes filtradas a
> confirmadas; sólo próximos o sin fecha (no vuelca el histórico). Pruebas en
> `tests/test_existing.py`. **Pendiente:** validar la cadena partner→cita/tarea contra
> datos reales de `visar_prod`.

- **C1 — ODOO: método RPC.** `agent_customer_services(payload)` en `visar.agent.tools`.
  Resuelve `res.partner` por teléfono **normalizado**; lee la cadena partner →
  `sale.order` → line → `calendar_event`/`task`; devuelve lista tipada
  `[{servicio, fecha, estado}]`. Helpers `_agent_`. Solo lectura.
- **C2 — DECISIÓN DE ACL** (pendiente, ver diseño §"Servicio existente"): **(a)**
  ampliar ACL de solo lectura del grupo del agente a `res.partner` / `calendar.event`
  / `sale.order(.line)` / `project.task`, **o (b)** `sudo()` acotado solo en ese
  método. **Elegir aquí** antes de codificar C1.
- **C3 — RUNTIME: contrato + handler.** Tocar **los dos lados** (regla del proyecto):
  protocolo `VisarOdooClient` + `OdooRPCClient` + `FakeOdooClient` + tool. Handler de
  la ruta que llama al método y **formatea** la respuesta (LLM con datos inyectados, o
  plantilla simple).
- **C4 — Normalización de teléfono.** Helper compartido (E.164; el `52` + `1` de móvil
  MX; formato libre en Odoo). Es la fuente clásica de "no encontró al cliente".

**Reutiliza:** patrón RPC de `visar.agent.tools`, modelos existentes de Odoo (solo
lectura), loop LLM para redactar. **Futuro (no ahora):** suscripciones ("¿cuándo es mi
próximo servicio?").

---

## Etapas posteriores (NOTAS — se detallan al llegar)

- **Etapa D — Insights del cliente · ODOO + RUNTIME.** Modelo `visar.customer.insight`
  ligado a `res.partner` + método RPC de **ESCRITURA acotado** (= **primer write**
  runtime→Odoo) + **bucle de extracción/fusión** (paso LLM sobre el transcript:
  cuándo correr, dedupe, caducidad) + **privacidad**. Ver diseño "Memoria e insights".
- **Etapa E — Persistencia del `ConversationStore` · RUNTIME.** Backend real
  (SQLite/Postgres/Redis) tras la misma interfaz. **Obligatorio antes de scheduling.**
- **Etapa F — Agendar (nuevo y recurrente) · RUNTIME + ODOO.** Flujo con **estado por
  paso** (`collected`); **rewind-and-replay** para editar + **pantalla de revisión** antes
  del hand-off; estimación de m² como **un solo paso**. Umbral de hand-off = **pago**
  (mantener lo más posible en WhatsApp). Ruta **`seed`** nueva en el wizard (pasar
  **inputs**, re-correr resolvers; la consistencia la da el **replay**, no hace falta
  portar `_VISAR_STEP_CLEARS`) + **`URLButton`** de hand-off. **Recurrente** = flujo
  asistido por LLM + **menús generados constreñidos** + pre-llenado ampliado. Reutiliza el
  mecanismo transversal de la Etapa B pasándole las opciones de cada paso. **Fase propia,
  al final.** Ver diseño.

## Orden y criterio

**A → B → C** primero (runtime, baratos, no dependen de las tripas del wizard).
**D** habilita el flujo recurrente y vale por sí solo. **E** antes de **F**. **F** al
final. Cada etapa entrega algo **probable end-to-end** con `/debug` + `FakeOdooClient`
**antes** de tocar WhatsApp real. Al cambiar el contrato RPC, tocar **los dos lados**
(Odoo + runtime), como manda `60-conventions-testing.md` del `visar_fastapi`.
