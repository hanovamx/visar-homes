# WhatsApp agent → CRM: plan de implementación

> ## ✅ Estado real (20-ago-2026): **EJECUTADO**
>
> Este plan se llevó a cabo. `visar_crm` v19.0.1.3.0 y `agent_track_lead`
> (`visar_agent_tools.py:736`) están en producción. Se conserva por sus "Hechos verificados"
> (gotchas de Odoo 19), que siguen siendo útiles.

> **Estado original: PLAN.** Escrito 2026-08-05. Es el **CÓMO** del diseño
> [`31-whatsapp-crm-lead-mapping.md`](./31-whatsapp-crm-lead-mapping.md) (el **QUÉ/PORQUÉ**,
> decisiones cerradas con dirección). No cambia ninguna decisión del doc 31; la aterriza
> contra el código real. Verificado por exploración del repo (ver "Hechos verificados").
>
> Repos: **ODOO** = `visar-homes` (nuevo módulo `visar_crm` + `visar_whatsapp_agent`);
> **RUNTIME** = `visar_fastapi`. Convención del proyecto: al tocar el contrato RPC se
> tocan **los dos lados** (método Odoo + protocolo/cliente/fake del runtime).

## Hechos verificados (exploración, 2026-08-05)

Cosas que cambian la implementación respecto a lo que uno supondría:

1. **`crm.stage` NO tiene `team_id`; tiene `team_ids` (Many2many).** Un stage con
   `team_ids` vacío es **global**; con `team_ids` = equipo, queda acotado. Sembrar las
   etapas con `team_ids = [(4, ref(equipo))]`. (Gotcha #1.)
2. **No existe `_get_default_stage_id` en Odoo 19.** La etapa por defecto la pone el
   campo computado `stage_id` (`_compute_stage_id` → `_stage_find`), que **reasigna** si
   el equipo del lead no está en `stage.team_ids`. ⇒ al crear el lead hay que fijar
   `stage_id = Nuevo` **explícitamente** y con `team_id = equipo WhatsApp` (Nuevo lleva
   ese equipo en `team_ids`), para que el compute no lo mueva.
3. **won/lost nativos:** `action_set_lost()` **archiva** el lead (`active=False`) +
   `probability=0`; won se maneja por `stage.is_won` vía `action_set_won()`. No hay flag
   won en el lead.
4. **Pago:** no hay confirmación custom del `sale.order`; el checkout nativo lo lleva a
   `state == 'sale'`. El **`calendar.event` solo se materializa tras el pago** (antes es
   un `calendar.booking`) ⇒ *crear un `calendar.event` es señal fiable de "pagado y real"*.
5. **FSM done:** `project.task.state == '1_done'` (lo escribe el cierre del técnico,
   `visar_field_app`), **no** la etapa sola. `'1_canceled'` NO cuenta. Reabrible ⇒ el won
   debe ser idempotente.
6. **Cadena grupo:** `line.product_id.product_tmpl_id.visar_dimension_id.group_id`
   (filtrar `visar_is_service` + dimensión no vacía). Ya se usa en `visar_subscription`.
7. **Valoración:** producto con `visar_is_valuation=True` / `appointment.type.visar_flow
   == 'valuation'`. Servicio normal: tipo maestro `visar_is_master` / líneas
   `visar_is_service`.
8. **Sin precedente de `base.automation`** en el repo. El avance de etapa se hace con
   **overrides de Python** (estilo de la casa), no con acciones automatizadas XML.
9. Reutilizables ya existentes en `visar.agent.tools`: `_agent_normalize_phone`
   (últimos 10), `_agent_find_partner` (teléfono→partner, sudo, no crea partner),
   `_agent_resolve_dimension(code) -> (dimension, options)` (¡guardar el caso `options`
   no vacío = grupo ambiguo → sin dimensión!).

## Layout de módulos (decisión)

- **Nuevo `visar_crm`** (depende de `crm`, `visar_base`): pipeline (`crm.team` +
  `crm.stage`), campos de `crm.lead`, helper de avance forward-only, automatizaciones de
  avance (§C/§D) y cron de caducidad. Es la mitad "CRM" y no sabe nada de WhatsApp.
- **`visar_whatsapp_agent`** (pasa a depender de `visar_crm`): gana el método RPC
  `agent_track_lead`. Vive aquí (no en `visar_crm`) por coherencia con los demás
  `agent_*` y porque reutiliza `_agent_normalize_phone/_find_partner/_resolve_dimension`.
- **Nombres de campo:** el doc 31 los llama `x_visar_*` (estilo Studio). En módulo de
  código se usa el prefijo `visar_*` (convención del repo). Mapeo:
  `x_visar_service_group_id → visar_service_group_id`, `x_visar_wa_phone_norm →
  visar_wa_phone_norm`, `x_visar_source → visar_source`.

---

## Fase A — módulo `visar_crm`: pipeline + campos (ODOO)

**Objetivo:** el pipeline "WhatsApp" con sus 5 etapas y los campos de `crm.lead`. Base de
todo. Sin lógica de runtime.

**A1 — esqueleto** (copiar de `visar_subscription`): `__manifest__.py`
(`version 19.0.1.0.0`, `depends: ['crm','visar_base']`, `license LGPL-3`,
`category 'Sales/CRM'`), `__init__.py` (`from . import models`), `models/__init__.py`.

**A2 — `models/crm_lead.py`** (`_inherit = 'crm.lead'`):
- `visar_service_group_id` = m2o `visar.service.group`, `index=True`. Grupo que acota el
  lead (clave de dedupe).
- `visar_wa_phone_norm` = Char, `index=True`, `copy=False`. Teléfono normalizado (10).
- `visar_source` = Selection `[('whatsapp','WhatsApp')]`, `copy=False`. (Alternativa
  nativa `utm.source` descartada por simplicidad; reevaluable.)
- Helper **`_visar_advance_stage(target_stage)`** (forward-only, por `sequence`): mueve
  solo si `target.sequence > stage_id.sequence`; devuelve bool. Reutilizado por A/C/D.

**A3 — `data/crm_pipeline_data.xml`** (`<data noupdate="1">` — config re-tuneable que
sobrevive upgrades):
- `crm.team` **`crm_team_whatsapp`** (`use_opportunities=True`).
- 5 `crm.stage`, cada una con `team_ids` **eval** `[(4, ref('crm_team_whatsapp'))]`
  (¡Many2many, Gotcha #1!):
  1. `crm_stage_wa_nuevo` — "Nuevo" — seq 1
  2. `crm_stage_wa_valoracion` — "Visita de valoración agendada" — seq 2
  3. `crm_stage_wa_cotizacion` — "Cotización enviada" — seq 3
  4. `crm_stage_wa_programado` — "Servicio programado" — seq 4
  5. `crm_stage_wa_cerrado` — "Cerrado" — seq 5, `is_won=True`
- `crm.lost.reason` **`crm_lost_reason_wa_inactivo`** ("WhatsApp - Sin respuesta") para
  el cron (§D). Registrar el XML en `data` del manifest.

**A4 — sin `ir.model.access.csv`:** no hay modelo nuevo (solo `_inherit` + data). Las
ACL de `crm.lead`/`crm.stage` ya vienen de `crm`.

---

## Fase B — `agent_track_lead` + runtime (ODOO + RUNTIME) · entrega la etapa **Nuevo**

**Objetivo:** que una **cotización del agente** cree/refresque un lead en **Nuevo**
(único write del runtime). El agente **nunca** avanza etapa (decisión doc 31 §3).

### B1 — ODOO: `agent_track_lead(payload)` en `visar.agent.tools`

```
payload = {
  "phone": "5218112345678",
  "service_code": "FUM_INT",     # DIMENSIÓN; Odoo resuelve el grupo
  "quote": {"cp","m2","total","currency"} | None,   # enriquecimiento §5.1
  "source": "whatsapp"           # opcional
}
returns {
  "lead_id": int | None,
  "created": bool,
  "stage": "Nuevo" | None,
  "skipped_reason": "invalid_phone" | "no_group" | "existing_customer" | None
}
```

Lógica (todo en Odoo, `sudo()` **acotado a este método** — cruza partner/órdenes/CRM que
el usuario share no ve por ACL; misma excepción que `agent_customer_services`):

1. `nat = _agent_normalize_phone(phone)`; si `len(nat) != 10` → `invalid_phone`.
2. `dimension, options = _agent_resolve_dimension(service_code)`; si `not dimension`
   (incluye grupo ambiguo con `options`) → `no_group`. `group = dimension.group_id`.
3. `partner = _agent_find_partner(phone)` (puede venir vacío; no crea partner).
4. **Exclusión de cliente existente por grupo:** si `partner` y tiene una `sale.order`
   confirmada (`state in ('sale','done')`) con una **línea de servicio** cuyo
   `product_id.product_tmpl_id.visar_dimension_id.group_id == group` → `existing_customer`
   (no crea/toca lead). Helper `_agent_partner_has_service_in_group(partner, group)`
   (filtra `visar_is_service` + dimensión no vacía). *(Una póliza activa es una orden
   confirmada en el grupo, así que queda cubierta sin leer `subscription_state`.)*
5. Buscar lead **abierto**: `crm.lead.sudo().search([('visar_wa_phone_norm','=',nat),
   ('visar_service_group_id','=',group.id),('team_id','=',team.id),
   ('stage_id','!=',cerrado.id)], limit=1)` (el search por defecto ya excluye archivados).
   - Existe → **refrescar**: nota de chatter + `expected_revenue` (si `quote`); enlazar
     `partner_id` si ahora se conoce.
   - No existe → **crear** en `Nuevo` (`type='opportunity'`, `team_id`,
     `stage_id=Nuevo`, `visar_service_group_id`, `visar_wa_phone_norm`, `visar_source`,
     `phone`, `partner_id`, `name = partner.name or "WhatsApp <nat>"`).
6. **Nunca avanza más allá de Nuevo.** El avance es §C/§D.

Nota de chatter (enriquecimiento, §5.1 del diseño): *"Cotización del agente: <grupo /
dimensión>, <m2> m², CP <cp> → $<total>"*. `expected_revenue = quote.total`.

Refs de xmlid: `visar_crm.crm_team_whatsapp`, `visar_crm.crm_stage_wa_nuevo`,
`visar_crm.crm_stage_wa_cerrado` (vía `env.ref(..., raise_if_not_found=False)`; si falta
el pipeline → `skipped_reason='pipeline_missing'` + warning, no romper).

Manifest `visar_whatsapp_agent`: `depends` gana `visar_crm`; **bump de versión**
(nueva dependencia + módulo nuevo ⇒ requiere `-u`).

### B2 — RUNTIME: contrato + hook de cotización

- `app/odoo/client.py`: `track_lead(payload)` en el protocolo `VisarOdooClient` +
  `OdooRPCClient` (`agent_track_lead`).
- `app/odoo/fake.py`: `track_lead` en memoria — dedupe por `(nat, grupo)` usando
  `_DIM_INDEX` (dimensión→grupo), **solo Nuevo**, forward-only trivial (siempre Nuevo);
  soporta `no_group`/`invalid_phone`. (La exclusión de cliente existente se omite en el
  fake — es lógica de negocio de Odoo; el fake prueba el **cableado**.)
- `app/agent.py`:
  - `_extract_quote(new_turns)` → `{cp, currency, total, items:[{service_code, m2}]}` o
    `None`. Escanea **solo** los turnos nuevos del run (correlaciona `tool_call`↔
    `tool_result` de `quote_service` por id; ignora `needs_clarification`/errores/total
    None). No toca el loop del LLM.
  - En `_info_handler`, tras `run()` + `save`: si hay quote, **por cada item** (distinta
    dimensión) → `_track_lead(phone, item.service_code, {cp, m2, total, currency})`.
    Multi-grupo (p. ej. FUM + MAV) ⇒ Odoo crea **un lead por grupo**; interior+exterior
    (mismo grupo FUM) ⇒ un solo lead refrescado.
  - `_track_lead(...)` **best-effort**: `try/except OdooError` + genérico, loguea, nunca
    tumba la respuesta. Único camino de **escritura** del runtime.
  - **NO** se dispara en entrada de menú (Información/Agendar) ni al mandar la liga: el
    lead nace de la **cotización** (grupo conocido), no del menú (decisión doc 31 §4).

### B3 — Pruebas
- **Runtime** (`tests/test_lead_tracking.py`, reescrito para el contrato nuevo):
  cotización → `track_lead` por item; dedupe por grupo en el fake; multi-grupo → 2 leads;
  interior+exterior → 1; sin cotización → 0; `no_group`/`invalid_phone`; fallo de CRM no
  rompe la respuesta.
- **Odoo** (`visar_whatsapp_agent/tests/test_agent_track_lead.py`): crea en Nuevo; dedupe
  `(teléfono, grupo)`; multi-grupo separado; exclusión de cliente existente; enriquecimiento
  (chatter + `expected_revenue`); nunca pasa de Nuevo. *(Corre en CI de Odoo, no aquí.)*

> **Fin de Fase B = valor entregable:** leads en Nuevo con enriquecimiento, paridad de
> identidad por teléfono+grupo. Es el corte que reemplaza mi implementación previa errónea.

---

## Fase C — avance automático: valoración + servicio programado (ODOO)

**Objetivo:** que Odoo mueva los leads a etapas posteriores por **eventos reales**, no el
runtime. Implementado como **overrides de Python** en `visar_crm` (no `base.automation`).

Señal elegida = **creación de `calendar.event`** (solo existe post-pago; Hecho #4). Un
helper común `_visar_crm_advance_from_order(order, target_stage)`:
- deriva teléfono: `order.partner_id` → `_visar_phone_nat10_value`;
- deriva grupo(s): `order.order_line` filtradas (`visar_is_service` + dimensión) →
  `mapped(...group_id)` (combo → varios grupos, **fan-out**);
- por cada grupo, busca el lead abierto `(nat, grupo)` y `_visar_advance_stage(target)`
  (forward-only).

**C1 — → "Visita de valoración agendada":** override `calendar.event.create`; si
`event.appointment_type_id.visar_flow == 'valuation'` → resolver la orden
(`sale.order.line.calendar_event_id == event`) y avanzar el/los lead(s) del grupo a
`crm_stage_wa_valoracion`. (Captura también el corte a valoración del wizard: cae en el
mismo tipo de cita.)

**C2 — → "Servicio programado":** en el mismo override, si el evento es de servicio
normal (tipo maestro `visar_is_master`, o líneas `visar_is_service`) → avanzar a
`crm_stage_wa_programado`. **Fan-out** por grupo (combo).

**C3 — → "Cotización enviada" (rama manual):** **botón/acción de staff** en el lead
("Marcar cotización enviada"), no automático — la cotización formal la arma finanzas tras
la valoración. Server action + botón (precedente `visar_field_app/views/geolocalize_action.xml`).

> Riesgos (de la exploración): una orden puede traer valoración **y** servicio → gatear
> por el flag de producto, no solo por `state`. Si "pagado" debe significar dinero
> recibido, combinar con el test de transacción/factura de
> `visar_field_app/models/sale_order.py:28`.

---

## Fase D — cerrado (won) + caducidad (lost) (ODOO)

**D1 — → "Cerrado" (won):** override `project.task.write`; cuando `state` pasa a
`'1_done'` (Hecho #5; **no** la etapa; `'1_canceled'` no cuenta; **idempotente** por
reapertura) → resolver la orden de la tarea (`task.visar_sale_order_id`) → teléfono+grupo(s)
→ `action_set_won()` en el/los lead(s). (Puede empezar manual si el puente tarea→lead
resulta caro.)

**D2 — cron de caducidad (lost):** `ir.cron` en `<data noupdate="1">` (plantilla
`visar_field_app`: `state=code`, `base.user_root`, intervalos) que llama
`model._visar_crm_expire_stale_leads()` en `crm.lead`. Marca lost
(`action_set_lost(lost_reason_id=crm_lost_reason_wa_inactivo)`) los leads abiertos
inactivos según **ventanas por etapa** en `ir.config_parameter` (editables sin deploy):
`visar.crm.lost_days_nuevo`, `visar.crm.lost_days_cotizacion`. Base: `write_date` / fecha
de entrada a la etapa. (Valores concretos: pendientes con el equipo — doc 31 §13.)

---

## Estrategia de pruebas

- **Runtime:** `pytest` (fake), sin Odoo ni WhatsApp — reescribir `test_lead_tracking.py`.
- **Odoo:** tests de `TransactionCase` para `agent_track_lead` (Fase B) y para los
  overrides de avance (Fase C/D) con datos sembrados. Corren en CI de Odoo. **No** correr
  contra `visar-db`/`visar_prod` (regla del repo).
- **E2E `visar_prod`** (doc 31 §13): crear los eventos reales (cotización, valoración,
  servicio pagado, tarea done) y verificar creación por grupo, exclusión de cliente
  existente, fan-out de combo y caducidad. Como se hizo con la paridad de cotización.

## Orden de ejecución

1. **Fase A** (`visar_crm`: pipeline + campos) — base de todo.
2. **Fase B** (`agent_track_lead` + runtime + pruebas) — entrega **Nuevo** + enriquecimiento.
3. **Fase C** (avance: valoración + servicio programado; botón de cotización enviada).
4. **Fase D** (won por tarea FSM; cron de caducidad).

A+B se implementan ya (reemplazan el corte previo). C+D son fases Odoo-internas
posteriores; varias "pueden empezar manuales" (doc 31 §8/§14).

## Estado de implementación (2026-08-05)

**Fase A + B: implementadas y verificadas** (16 tests Odoo + 140 pytest en
`visar-db`). Commits: `visar-homes b6ed4c3`, `visar_fastapi d552110`.

**Fase C + D: implementadas** (`visar-homes a236ba9`), con estas **desviaciones
respecto al plan de arriba**, tomadas al aterrizarlo contra el código:

- **Servicio programado NO se dispara en `calendar.event`, sino en
  `sale.order.write` cuando `state -> 'sale'`.** El grupo se deriva de las líneas
  de servicio (producto→dimensión→grupo), que están disponibles al confirmar, sin
  la carrera de timing de cuándo se enlaza `calendar_event_id`. Fan-out por grupo.
- **Valoración agendada y Cotización enviada quedan como BOTONES DE STAFF**, no
  automáticas. Motivo: la orden de una valoración trae el **producto de
  valoración, que no tiene grupo de servicio**, así que no se puede atribuir por
  `(teléfono, grupo)` de forma fiable. Automatizable luego vía
  `calendar.event.visar_booking_items` (los `dimension_id` del wizard) tras
  verificar el timing/contenido en `visar-db`.
- **Won = `project.task.write` con `state == '1_done'`** (cierre del técnico; no
  la etapa; `'1_canceled'` no cuenta), idempotente ante reapertura.
- **Forward-only por POSICIÓN en el pipeline (xmlids), no por
  `crm.stage.sequence`.** Robustece contra las etapas stock globales de Odoo
  (`team_ids` vacío) que se muestran en todos los pipelines y colisionan en
  `sequence` con las nuestras.
- **`visar_crm` depende de `visar_appointment`** (no solo `visar_base`): necesita
  la normalización canónica `res.partner._visar_phone_nat10_value`, la cadena
  producto→dimensión→grupo y `project.task.visar_sale_order_id`.

**Higiene de datos en `visar-db`** (no es código): las 4 etapas stock de `crm`
son globales (`team_ids` vacío) y aparecen como columnas en el pipeline WhatsApp;
se acotan al equipo Sales para sacarlas. Un `crm.team` id=4 "Sales" sin dueño es
residuo de prototipado, a borrar.

**Pendiente de verificar E2E en `visar-db`:** disparar una reserva real pagada y
un cierre de tarea FSM, y confirmar el avance de etapa + fan-out de combo con
datos reales (como se hizo con la paridad de cotización).

## Decisiones/pendientes heredados del doc 31 (§13)

- Valores de `lost_days_*` (con el equipo).
- Puente tarea FSM → lead para won automático vs manual al inicio.
- `visar_source` propio (elegido) vs `utm.source`.
- Lead token para atribución exacta: **descartado por ahora** (no existe `seed` por URL;
  ver doc 29/30). Atribución por `(teléfono, grupo)`.
