# visar_whatsapp_agent — API para el agente de WhatsApp (5º módulo)

> Módulo añadido en jul-2026, después de la última revisión general de esta
> carpeta. Es la **mitad Odoo** de un agente de IA por WhatsApp; la otra mitad es
> un servicio externo **FastAPI** (`visar_fastapi/`, fuera de este repo) con su
> propio `.context/`.

## Qué es

Superficie **RPC acotada** que consume el runtime externo del agente. No tiene
interfaz de usuario. Le da al agente acceso al catálogo, al tabulador, al
historial del cliente y —desde ago-2026— a **agendar de verdad**: apartar un
horario, crear la reserva y emitir la liga de pago.

```
Cliente → WhatsApp Cloud API → visar_fastapi (FastAPI + LLM) → RPC → visar.agent.tools
```

> ⚠️ **"Fase 1: solo lectura" TERMINÓ.** Hasta `9e606c9` (17-ago-2026) este módulo no
> escribía nada. Hoy escribe `visar.slot.hold`, `calendar.booking`, `sale.order`,
> `crm.lead` y `visar.wa.booking.message`. El agendado completo por WhatsApp está **en
> producción** desde el 19/20-ago-2026 — ver
> [`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md).
>
> El wizard web (`visar_appointment`) **sí se tocó**: en `c115c21` el cuestionario bajó del
> controlador al modelo (`appointment_wizard_flow.py`) para que los dos canales compartan
> las mismas reglas. El comportamiento del web no cambió; su lugar de residencia, sí.

## Arquitectura híbrida (por qué fuera de Odoo)

El runtime vive fuera de Odoo a propósito: Odoo corre con pocos workers y no está
hecho para esperar la latencia de un LLM; exponer un webhook público sobre el ERP
amplía la superficie de ataque; separar el runtime deja escalarlo/desplegarlo sin
arriesgar el negocio. Odoo se queda con lo que le corresponde: **datos y
configuración**. Corre en el **mismo servidor** que Odoo. Ver `40-decisions.md`
(entrada nueva) y el `.context/40-decisions.md` de `visar_fastapi`.

## `visar_whatsapp_agent` (v19.0.1.6.0)

> **v19.0.1.5.0 (27-ago-2026) — el prompt base se inyecta siempre, y cada ruta
> tiene su memoria.** `visar.agent.prompt` gana un campo `ruta`: el registro sin
> ruta es el **base** (se inyecta desde el primer mensaje, en todas las rutas) y
> los que la llevan son **memorias** que se añaden solo en la suya.
> `agent_runtime_config()` las devuelve en `route_prompts`, aditivo y compatible
> en las dos direcciones. Las cinco memorias se siembran desde `data/` con
> `noupdate="1"`; **el base no se siembra**, para no crear un segundo candidato
> en producción. Ver `34-prompt-agente-informacion.md`.

**Dependencias:** `visar_appointment`, `visar_crm`.

> Depende de `visar_appointment` —no solo de `visar_base`— porque **reutiliza su
> motor de precios** (`_visar_quote_booking`). Ver "La cotización no se
> reimplementa" abajo.

> **v19.0.1.1.0 (28-jul-2026) — Fase 2a:** se añadieron 3 modelos de configuración
> (`visar.agent.prompt`, `visar.llm.config`, `visar.whatsapp.config`), el método RPC
> `agent_runtime_config()`, y vistas + menú "Agente WhatsApp" (grupo
> `base.group_system`). **Sin secretos en la BD.** Ver `28-whatsapp-agent-phase2-design.md`.

### Modelos

| Modelo | Archivo | Para qué |
|---|---|---|
| `visar.agent.tools` | `models/visar_agent_tools.py` | **AbstractModel**. Métodos `@api.model`. Sin tabla; se llama por RPC. |
| `visar.agent.prompt` | `models/visar_agent_prompt.py` | Prompt **base** (`ruta` vacía) + una **memoria por ruta**. Vigente = el primero por `sequence, id` **dentro de su ruta**. |
| `visar.llm.config` | `models/visar_llm_config.py` | Proveedor/modelo/`max_tokens`/`max_tool_iterations` (sin credenciales). |
| `visar.whatsapp.config` | `models/visar_whatsapp_config.py` | Cuenta de WhatsApp (no-secreto). **Display-only en 2a.** |
| `visar.slot.hold` | `models/visar_slot_hold.py` | Apartado temporal de horario (extensión; el modelo vive en `visar_appointment`). |
| `visar.wa.booking.message` | `models/wa_booking_outbox.py` | Buzón de avisos salientes de reserva, con cron. |

### Los métodos RPC — **12**, y seis de ellos escriben

Lectura:

| Método | Entrada | Devuelve |
|---|---|---|
| `agent_catalog_snapshot()` | — | grupos, dimensiones, tramos y zonas (**sin** precios ni CPs) |
| `agent_runtime_config()` | — | `{prompt\|None, route_prompts{}, llm{provider,model,max_tokens,max_tool_iterations}}` — **sin secretos, sin `notes`** (van en el catálogo). `route_prompts` siempre es un dict; una ruta sin registro, archivada o en blanco está **ausente**, no presente con `None` |
| `agent_resolve_zone(cp)` | código postal | zona y cobertura |
| `agent_quote_service(payload)` | `{cp, items:[{service_code, m2}...]}` o `{service_code, cp, m2}` | líneas y total |
| `agent_customer_services(payload)` | `{phone, scope?}` | servicios del cliente: próximos (default), historial o ambos (etapa C) |
| `agent_booking_step(payload)` | estado + respuesta del paso | estado nuevo + paso siguiente + sus opciones. **No escribe.** Es la llamada más importante del sistema: el cuestionario entero pasa por aquí. |
| `agent_available_days(payload)` | `{selections, cp\|zone_id, mode, asked_capacity}` | `{days:[{date, slot_count}], min_hours, message}` |
| `agent_day_slots(payload)` | lo anterior + `{date}` | slots con `start`/`stop` (UTC) **y** `start_local`/`stop_local` (zona de Visar) |

Escritura:

| Método | Escribe | Para qué |
|---|---|---|
| `agent_track_lead(payload)` | `crm.lead` | seguimiento CRM: la cotización del agente crea lead en *Nuevo*. |
| `agent_request_handoff(payload)` | `crm.lead` + `mail.activity` | hand-off humano: nota en el chatter con todo lo recogido + actividad asignada. |
| `agent_hold_slot(payload)` | `visar.slot.hold` | aparta un horario ~10 min a nombre de un teléfono. Acepta `mode` (`wizard`\|`valuation`). |
| `agent_prepare_booking(payload)` | `calendar.booking`, `sale.order` | arma la reserva y devuelve la **liga de pago** (`payment.link.wizard`). |

> **Los dos relojes de `agent_day_slots`.** `start`/`stop` van en **UTC naive** porque es lo
> que `agent_hold_slot` y `agent_prepare_booking` esperan de vuelta. `start_local`/`stop_local`
> van en la zona de Visar (`visar.agent.timezone`) y son los **únicos** que se le pueden
> enseñar a una persona. El runtime no puede convertirlo por su cuenta: la zona es
> configuración de Odoo, y derivarla del otro lado sería otra regla duplicada. Sin esto, un
> servicio de las 4 de la tarde se ofrecía como *"entre 22:00 y 23:00"*.

> **Al cliente se le da una VENTANA, no una hora** (decisión 15 del diseño 33): "3 pm"
> significa *entre 3 y 4*. El bloque de 1 h son **20 min de traslado + 40 de servicio**.

**Endpoint inverso.** Odoo también llama al runtime: `/internal/booking-event`
(`booking_confirmed`, `hold_expired`, `hold_expired_link`), además de
`/internal/send-report` y `/internal/send-notification`. Ver el `.context/30-odoo-contract.md`
de `visar_fastapi`.

> `agent_customer_services` (etapa C, jul-2026) es la ruta **"Servicio existente"**:
> teléfono → `res.partner` (últimos 10 dígitos) → órdenes confirmadas → cita
> (`calendar.event`) y tarea FSM (`project.task`). **Único método con `sudo()`**
> (acotado): cruza datos de cliente que el ACL del usuario share no ve, y devuelve
> un dict tipado y mínimo. No amplía el ACL del share.

`service_code` es un **código de dimensión** (`FUM_INT`, `FUM_EXT`, `MAV_JAR`),
no de grupo. Si se manda un grupo con varias dimensiones, la respuesta trae
`needs_clarification: true` con las opciones, sin total (cada dimensión tiene su
tabulador; no se adivina).

Ningún método acepta nombres de modelo, dominios ni SQL. Es intencional: acota lo
que el LLM puede pedir aunque le metan prompt injection.

### La cotización NO se reimplementa

`agent_quote_service` construye los mismos `items` que arma el wizard
(`_visar_resolve_wizard_items` produce la misma forma) y los pasa a
**`appointment.type._visar_quote_booking(items, zone)`**, el motor de precios que
ya existe (`20-architecture.md` → `visar_appointment`). Por eso el total del
agente es, **por construcción**, idéntico al de la web e incluye:

- la **variante combinada** de fumigación interior+exterior — la rejilla
  **zona × m² interior × m² exterior** (`_visar_combined_variant_for_tiers`,
  `70-tabulador.md`). El precio combinado **no** es la suma de cotizar interior y
  exterior por separado.
- los **descuentos de combo** entre servicios (`visar.combo.rule`);
- los **add-ons obligatorios** (D-06) y los tramos incluidos sin cargo.

Helpers de `visar_base` que también reutiliza: `visar.zone.cp._get_zone_for_cp`,
`product.template._visar_get_service_template_for_dimension` /
`_visar_tier_for_dimension_m2`, `visar.service.tier._visar_get_variant_for_zone`.

### Seguridad (principio de mínimo privilegio)

- Grupo **"Agente WhatsApp / Solo lectura"**
  (`security/visar_whatsapp_agent_groups.xml`) con ACLs de solo lectura
  (`security/ir.model.access.csv`) sobre los modelos de catálogo, producto,
  pricelist, add-ons, moneda, uom y website.
- Usuario `whatsapp_agent` (tipo **share**) en ese grupo.

> ⚠️ **El nombre del grupo se quedó viejo, y la frase que había aquí también.** Decía "los
> métodos no usan `sudo`… esas ACLs son el límite real". **Ya no es cierto.**
>
> El grupo `group_whatsapp_agent_readonly` **sigue** siendo de solo lectura en el CSV —
> ninguna línea de `ir.model.access.csv` le da `perm_write` sobre catálogo o producto, y eso
> está bien. Pero los métodos que escriben (`agent_track_lead`, `agent_request_handoff`,
> `agent_hold_slot`, `agent_prepare_booking`) **escalan con `sudo()` por dentro**: hay 41
> `sudo()` en `visar_agent_tools.py`.
>
> Consecuencia para quien audite esto: **el límite real ya no son las ACLs, es la superficie
> de los métodos.** Ninguno acepta nombres de modelo, dominios ni SQL, y cada uno escribe
> exactamente un tipo de registro con datos validados — eso es lo que acota el daño de un
> prompt injection, no el ACL. Si algún día se añade un método que escriba, el trabajo de
> seguridad está en **su firma**, no en el CSV.
>
> El grupo debería renombrarse a algo como "Agente WhatsApp / RPC" cuando toque un `-u`; no se
> hace solo por eso porque cambiar el `name` no cambia ningún permiso.

> **Superficie de ACL (validada 23-jul-2026).** Reutilizar `_visar_quote_booking`
> arrastra lecturas de varios modelos. En el primer uso real faltaba **una**:
> `res.company` (lo lee la moneda de la compañía). Ya está en el CSV. Si aparece
> otro `AccessError`, el fix es una línea más.

### Gotchas de Odoo 19 encontrados al instalar

- `res.groups.category_id` **ya no existe** (pasó a `privilege_id` →
  `res.groups.privilege`). El grupo técnico se declara sin él.
- `res.users.groups_id` → **`group_ids`**.
- `res.partner.mobile` **ya no existe** (Odoo 19 lo eliminó; sólo queda `phone` +
  `phone_sanitized`). `agent_customer_services` (etapa C) tronaba en el 100% de
  las llamadas por buscar `mobile`; ahora sólo usa `phone`. **Diagnóstico en
  servidor 31-jul-2026 (BD `visar-db`).**
- API keys: el usuario *share* tiene un cap de duración de **1 día**
  (`max(group.api_key_duration) or 1.0`). El grupo fija `api_key_duration` alto
  (~10 años) para poder emitir una key de servicio de larga duración. `_generate`
  solo salta el cap si `env.is_system()`, así que la key se emite con el usuario
  agente y su cap ya subido, no en sudo.

### Validación de paridad (23-jul-2026, BD `visar-db`)

Instalado y validado contra datos reales. `agent_quote_service` (usuario acotado)
= `_visar_quote_booking` (motor del wizard) **al peso** en: interior solo,
exterior solo, interior+exterior juntos (**una línea combinada**, 1150, que **no**
es la suma 690+1150=1840), y el combo triple (línea de corte a −50%). Round-trip
por XML-RPC desde el `OdooRPCClient` de `visar_fastapi` con API key: mismos
totales. Detalle en el `.context/50-status-roadmap.md` de `visar_fastapi`.

## La consola de rutas (28-ago-2026, v19.0.1.6.0)

Desde que el LLM enruta (`a2a6865`), `route` dejó de decidir qué handler corre y
pasa a decidir **qué memoria recibe el modelo**. La ruta ya *era* la unidad de
configuración; la UI seguía mostrando registros de `visar.agent.prompt` en una
lista plana, y eso producía cuatro cosas concretas:

1. Los dos prompts base vivían entre las memorias de ruta — objetos distintos
   (20 000 caracteres que aplican siempre vs. ~700 que aplican en una ruta)
   juntos porque comparten tabla, que es una razón de implementación.
2. «Prompt base» (17 449 car.) es un registro **muerto** —pierde por secuencia—
   visible y editable, con una columna que dice «No» como única señal.
3. La lista no decía qué hace cada ruta: nombre y caracteres.
4. **La ruta `info` no se alcanza** y nada lo indicaba. Nada en el camino del LLM
   asigna ya `Route.INFO`; sólo llegan ahí los botones de mensajes anteriores a
   ago-2026. Editar esos 900 caracteres no cambia ninguna conversación.

### Lo que hay ahora

Dos pantallas sobre el mismo modelo, y **ningún modelo nuevo ni migración**:

- **Rutas** (`visar_agent_route_action`, dominio `ruta != False`) — cinco filas
  con disparador, número de herramientas, caracteres y «en uso». El estado va en
  una columna `estado` pintada como **badge con palabras** (*Viva* /
  *Inalcanzable* / *Eclipsada*).
- **Prompt base** (`visar_agent_base_action`, dominio `ruta = False`) — pantalla
  propia, donde el registro muerto se ve por lo que es.

Las dos suben un nivel en el menú; en «Configuración» quedan LLM y WhatsApp.

El detalle de ruta contesta, en orden, las tres preguntas que alguien se hace
antes de tocar nada — **cómo se llega**, **qué puede hacer el modelo aquí**, **qué
no puede pasar** — y sólo después la memoria, que es el único campo editable.

> **La distinción que sostiene el diseño:** la *capacidad* es código, el
> *comportamiento* es configuración. Ningún texto de Odoo le da al modelo una
> herramienta que no tiene. Cuando una memoria necesita describir una salida, la
> salida tiene que existir primero en el runtime — escribirla sin construirla es
> exactamente cómo se produce una respuesta inventada (§14 del doc 85 del
> runtime).

### `ROUTE_META`, y la prueba que lo sujeta

Los metadatos son una **copia deliberada** de lo que vive en
`visar_fastapi/app/odoo/tools.py`, en un dict junto a `ROUTES`, expuesta con
campos calculados **no almacenados** (son constantes del código, no datos).

Lo único que hace aceptable la duplicación es
`test_route_meta_cubre_todas_las_rutas`: añadir una ruta sin sus metadatos rompe
la suite en vez de dejar la pantalla mintiendo. Verificado dándole una ruta
fantasma: la prueba falla y la nombra. **Si esa prueba se borra, la consola pasa
a poder mentir en silencio.**

Se sustituye por `agent_register_capabilities()` —el runtime registrando su
manifiesto al arrancar— cuando se despliegue esa fase. Eso además delataría un
runtime caído («último registro: hace 3 días»), que es I-16.

### Detalles que costaron una pasada

- **Un calculado no almacenado no se puede filtrar.** `es_vigente` y `alcanzable`
  en el `domain` de un `<filter>` hacen que Odoo **rechace la vista entera** al
  instalar (`Unsearchable field`). Se quitaron los filtros: con cinco filas la
  columna de estado ya lo dice.
- **El color solo no se ve, y encima no siempre se pinta.** La primera versión
  marcaba la ruta muerta con `decoration-danger` sobre un campo
  `column_invisible`, que no pinta nada. Se cambió por una columna `estado` con
  el estado **escrito**: el color es un refuerzo, nunca el mensaje. Un badge que
  dice «Inalcanzable» lo entiende cualquiera; una fila roja hay que saber
  interpretarla.
- **Odoo escribe en `logfile`, no en stderr.** Un `-u` que falla puede salir con
  la consola en blanco y código 0. Verificar siempre en `/var/log/odoo/odoo.log`.

### Lo que NO cambió

`agent_runtime_config()` devuelve exactamente lo mismo
(`{generated_at, prompt, route_prompts, llm}`), con una prueba que lo fija para
que los campos de consola no se filtren al runtime y se paguen en tokens.
**El runtime no se reinicia por esta entrega.**

### Sigue abierto

- **Qué hacer con `info`**: esta entrega la *marca*, no la resuelve. Fusionar su
  memoria en `reception` y retirar el valor del `Selection` toca datos vivos.
- **Permisos**: el menú sigue en `base.group_system`, así que un gerente de Visar
  no lo ve. Decidido a propósito: abrir el acceso antes de que la pantalla deje
  de engañar sería el orden equivocado.

## Puesta en marcha

1. Instalar el módulo (arrastra `visar_appointment`).
2. Crear una **API key** para `whatsapp_agent` (Preferencias → Seguridad de la
   cuenta → Nueva API key). **API key, no contraseña.**
3. Notas de negocio para el prompt (opcional): parámetro del sistema
   `visar.agent.catalog_notes`.
4. Apuntar el servicio FastAPI a Odoo (`ODOO_FAKE=false`, credenciales). Ver el
   `README.md` del módulo y el `.context/` de `visar_fastapi`.

## Verificación de paridad — HECHA (23-jul-2026)

Ver "Validación de paridad" arriba. `agent_quote_service` coincide al peso con el
wizard en todos los escenarios probados, incluida la variante combinada y el
descuento de combo. La lógica es código compartido, así que la única forma de
divergir sería datos o ACL — y las ACLs ya se cerraron.

## Estado de las fases (20-ago-2026)

| Fase | Qué era | Estado |
|---|---|---|
| **1** | Solo lectura: catálogo, zona, cotización | ✅ **terminada y superada** |
| **2a** | Config + prompt editables desde Odoo | ✅ implementada (`63261da`) |
| **2b** | Salientes disparados por template | ⚠️ código listo; **bloqueado por Meta** (plantillas sin aprobar) |
| **2c** | Agendar como flujo determinista, sin LLM | ✅ **implementada y en producción** |

> El diseño de la plataforma de capacidades sigue en
> [`28-whatsapp-agent-phase2-design.md`](./28-whatsapp-agent-phase2-design.md), pero para el
> agendado el documento vivo es
> [`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md).

**El agendado NO pasa por el LLM.** El cuestionario es determinista de punta a punta: Odoo
decide el paso siguiente (`agent_booking_step`) y el runtime solo lo pinta. Las únicas dos
tools expuestas al LLM siguen siendo `resolve_zone` y `quote_service`.

### Lo que sigue abierto

- ⛔ **Rama de valoración** (§10.7 / I-17): `valuation` es terminal, no llega a horarios. Los
  clientes con termitas, chinches o "no sé qué es" **no pueden agendar por WhatsApp**.
- ⛔ **Factibilidad de traslado** (§5, decisiones 7/14): sin construir. Hoy se ofrece cualquier
  horario con capacidad sin mirar si el técnico llega.
- **Equipo CRM de WhatsApp sin líder ni miembros** — `agent_request_handoff` deja la nota y la
  actividad, pero **escala a nadie**. Es dato, no código, y bloquea el hand-off real.
- **Plantillas de Meta sin aprobar** — hasta entonces los avisos salientes están siempre fuera
  de la ventana de 24 h: se encolan, dan 502 y caducan.
- Almacenamiento seguro de credenciales (hoy en el `.env` del servicio).
- **Stripe**: el pago sigue **simulado** (proveedor Demo). Decisión 13 del diseño 33.
