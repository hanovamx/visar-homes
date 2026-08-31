# Arquitectura — módulos Visar

Odoo 19 Enterprise. El proyecto son **siete módulos**. La dependencia **no** es una cadena
lineal: `visar_base` y `visar_fsm` son la raíz común, y de ahí cuelgan dos ramas hermanas que
**no se conocen entre sí**.

```
visar_base
   └─ visar_fsm
        ├─ visar_appointment ──► visar_whatsapp_agent
        │     └─ (depende también de visar_subscription)
        └─ visar_field_app
                                  visar_crm ──► visar_whatsapp_agent
```

| Módulo | Versión (**31-ago-2026**) | Depende de |
|---|---|---|
| `visar_base` | 19.0.1.10.0 | `sale`, `product`, `appointment` |
| `visar_fsm` | 19.0.1.1.0 | `visar_base`, `appointment`, `hr`, `industry_fsm`, `industry_fsm_sale` |
| `visar_appointment` | 19.0.2.8.0 | `visar_base`, `visar_fsm`, `visar_subscription`, `website_appointment*`, `website_sale`, `hr`, `worksheet` |
| `visar_field_app` | 19.0.1.26.0 | `visar_fsm`, `website`, `industry_fsm_report`, `base_geolocalize`, `account_payment` |
| `visar_subscription` | 19.0.1.4.0 | — ver [`35-polizas.md`](./35-polizas.md) |
| `visar_crm` | 19.0.1.3.0 | — ver [`31-`](./31-whatsapp-crm-lead-mapping.md) / [`32-whatsapp-crm-lead-implementation.md`](./32-whatsapp-crm-lead-implementation.md) |
| `visar_whatsapp_agent` | 19.0.1.8.0 | `visar_appointment`, `visar_crm` — ver [`27-whatsapp-agent.md`](./27-whatsapp-agent.md) |

> Esta tabla iba fechada el 20-ago y llevaba **cuatro** versiones atrasadas en `visar_base`
> (1.6.0), dos en `visar_whatsapp_agent` (1.4.0) y una en `visar_appointment`. Releída de los
> `__manifest__.py` el 31-ago-2026.

> ⚠️ **`visar_field_app` y `visar_appointment` son hermanos, no parientes.** Ninguno depende
> del otro. Su ancestro común es **`visar_base`**. Importa al decidir dónde vive código
> compartido: por ejemplo la integración con **Mapbox** vive hoy en `visar_field_app`
> (geocodificación y ETA de traslado), así que `visar_appointment` **no puede** usarla sin
> moverla antes a `visar_base`. Ver el §5 de
> [`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md).

> **Estado 20-ago-2026:** D-05/D-06/D-07 implementados; pólizas en producción; app de campo
> con worksheet, fotos, firma y avisos; **agendado completo por WhatsApp en producción**.

---

## `visar_base` (v19.0.1.10.0)

**Dependencias:** `sale`, `product`, `appointment`.

Catálogos y lógica de negocio compartida (tabulador, precios, add-ons) **y el transporte de
avisos salientes por WhatsApp**.

### Modelos propios

| Modelo | Archivo | Para qué |
|---|---|---|
| `visar.zone` | `models/visar_zone.py` | Zonas A/B/C. Campos: `name`, `code`, `sequence`, `active`, **`pricelist_id`**. |
| `visar.service.group` | `models/visar_service_group.py` | Grupos del wizard (paso 1). `dimension_ids`, `show_in_wizard`, `wizard_label`. |
| `visar.service.dimension` | `models/visar_service_dimension.py` | Sub-servicio / dimensión. Enlaza `product_tmpl_id`, tier field name. |
| `visar.service.tier` | `models/visar_service_tier.py` | Tramo → variante. `name`, `m2_min`, `m2_max`, `product_id`, **`is_valuation`**, `is_free`, `combo_discount_eligible`. |
| `visar.combo.rule` | `models/visar_combo_rule.py` | Reglas de descuento combo configurables. |
| `visar.product.optional.line` | `models/visar_product_optional_line.py` | Add-ons con **`is_mandatory`** y **`quantity`** (D-06). |
| `visar.zone.cp` | `models/visar_zone_cp.py` | CP → zona (`_get_zone_for_cp`). ⚠️ **Es métrica de PRECIO, no de distancia** — ver aviso abajo. |
| `visar.measure.band` | `models/visar_measure_band.py` | Bandas de medición (exterior unificado). |
| `visar.estimator.factor` | `models/visar_estimator_factor.py` | Factores del estimador por proxy. |
| **`visar.wa.outbox.mixin`** | `models/wa_outbox_mixin.py` | **AbstractModel.** Transporte compartido de avisos salientes por WhatsApp: encolar, cron, reintento, caducidad y nota en el chatter si no se entregó. |

> **Por qué el mixin.** Nació como `visar.wa.message` en `visar_field_app` (avisos "voy en
> camino" / "llegué"). Al necesitar los mismos avisos para el agendado había que copiar ~150
> líneas de transporte o subirlo a un mixin; se eligió lo segundo, porque dos copias de una
> regla divergen en cuanto alguien toca una. Cada módulo concreto pone su ancla, su catálogo
> **cerrado** de claves, el TTL por clave, su cron y dónde dejar la nota.
> Implementaciones: `visar.wa.message` (`visar_field_app`) y `visar.wa.booking.message`
> (`visar_whatsapp_agent`).

> ⚠️ **`visar.zone` NO aproxima distancia.** Las zonas de Visar son una **métrica de precio**;
> no están trazadas por cercanía. Dos direcciones de la misma zona pueden estar a 45 min. Sirve
> para saber qué técnicos atienden y qué lista de precios aplica, **nunca** para estimar
> traslados. La primera versión del diseño 33 §5.3 asumió lo contrario y estaba mal.

### Extensiones

| Modelo | Archivo | Campos / helpers |
|---|---|---|
| `product.template` | `models/product_template.py` | `visar_is_service`, `visar_is_valuation`, **`visar_is_roedores`**, `visar_dimension_id`, `visar_tier_ids`, **`visar_optional_line_ids`**. Helpers: `_visar_get_mandatory_addon_map`, `_visar_get_valuation_template`, `_visar_get_roedores_template`, `_visar_variant_for_zone`. |
| `sale.order` | `models/sale_order.py` | `_visar_apply_mandatory_addons`. |
| `sale.order.line` | `models/sale_order_line.py` | Auto-inyección add-ons obligatorios en backend (onchange/create/write). |
| `res.config.settings` | `models/res_config_settings.py` | Producto valoración, factor combo legacy. |

### Vistas / menús

Backend bajo **Citas → Configuración**: Zonas, Grupos de servicio, Tabulador, Reglas de combo.
Pestaña Visar en producto + tabla add-ons junto a `optional_product_ids`.

### Datos

`data/visar_tabulador_data.xml` — placeholder vacío; catálogo se configura en backend (no XML de productos).

---

## `visar_fsm` (v19.0.1.0.1)

**Dependencias:** `visar_base`, `appointment`, `hr`, `industry_fsm`, `industry_fsm_sale`.

Generación de tareas FSM al confirmar pedidos Visar (D-07).

### Extensiones

| Modelo | Archivo | Qué hace |
|---|---|---|
| `sale.order.line` | `models/sale_order_fsm.py` | Override `_timesheet_service_generation`: agrupa líneas Visar por `project_id` → **una tarea por proyecto**; asigna add-ons como materiales; enriquece tareas. |
| `sale.order` | `models/sale_order_fsm.py` | `_visar_enrich_fsm_tasks` — copia `planned_date_begin`/`date_deadline` y `user_ids` desde `calendar.event`. |
| `project.task` | `models/project_task.py` | **`visar_sale_order_id`** (related stored a `sale_order_id`) — expone la orden completa en la tarea. |
| `calendar.event` | `models/calendar_event.py` | `visar_fsm_task_ids` (computed M2m). |
| `appointment.resource` | `models/appointment_resource.py` | (vista backend) |

### Vistas

| Archivo | Qué hace |
|---|---|
| `views/project_task_views.xml` | Oculta `sale_line_id` nativo; muestra `visar_sale_order_id` (solo lectura). |

### Setup — `hooks.py`

`post_init_hook` → `_visar_setup_fsm_projects(env)`:
- Crea/busca proyectos FSM: **Fumigación**, **Mantenimiento Áreas Verdes**, **Valoraciones / Inspecciones**.
- Asigna `service_tracking='task_global_project'` + `project_id` a productos según dimensión (`code` prefix) o `visar_is_valuation`.
- IDs guardados en `ir.config_parameter` (`visar.fsm_project_*_id`).

> Este hook **sí corre en `-i`**. Re-ejecutado también en migración `visar_appointment` 19.0.2.0.14.

---

## `visar_appointment` (v19.0.2.7.0)

**Dependencias:** `visar_base`, `visar_fsm`, `visar_subscription`, `website_appointment`, `website_appointment_sale`, `website_sale`, `hr`, `worksheet`.

Wizard web, controlador de citas, tipos de entrada, plantillas frontend, **el cuestionario
compartido entre canales** y **el apartado de horario**.

### `appointment_wizard_flow.py` — el cuestionario, fuera del controlador

**La pieza de lógica más grande del módulo (~1,405 líneas).** Hasta `c115c21` (19-ago-2026) las
reglas del cuestionario vivían en el controlador web atadas a `request.session`; el agente de
WhatsApp necesitaba **las mismas** por RPC. Bajaron a `appointment.type`, y el controlador pasó
de 1,961 a 1,664 líneas y ahora **delega**: sigue dueño de la sesión HTTP, los formularios y
las URLs, y nada más.

Cuatro responsabilidades:

| Qué | Métodos clave |
|---|---|
| **Podar** — qué respuestas quedan inválidas al cambiar un paso | `_visar_wizard_clear_downstream`, `_VISAR_STEP_CLEARS`, `_VISAR_CUT_KEYS`, regla de prefijo `tier_*` |
| **Secuenciar** — qué paso viene después | `_visar_wizard_next_step`, `_visar_wizard_step_after`, `_visar_wizard_next_pending_step`, `_visar_wizard_step_sequence` |
| **Normalizar** — respuesta del cliente → `selections` | `_visar_wizard_apply_answer`, los `_visar_wizard_answer_*` |
| **Ofrecer** — opciones válidas de cada paso, serializadas | `_visar_wizard_step_options` |

**Los pasos** (`VISAR_STEP_*`): `services` → `motivo` → `plagas` → `cobertura` → `group_<id>` /
`interior` / `exterior` / `dimensiones` → **`address`** → `nombre` → `extras` → `poliza` →
`schedule`. Más `valuation`, el corte.

Tres cosas que no son evidentes y que se pagaron caro:

1. **`_visar_wizard_next_step` termina en `address` a propósito.** Extras y póliza solo se
   pueden decidir con zona e items resueltos, y eso pasa **al enviar la dirección**.
2. **`_visar_wizard_step_after` es "sigue la cadena DESDE el paso que acabas de contestar"** —
   útil hacia adelante, inservible al corregir.
3. **`_visar_wizard_next_pending_step` es "el primer paso sin contestar de TODO el
   cuestionario"** — la que hace falta al corregir un paso o al retomar una conversación
   estacionada. Las otras dos no contestan esa pregunta.

> **Semántica de presencia, no de valor.** `extras_ids` y `poliza_plan_id` marcan "este paso ya
> se contestó" por la **presencia de la clave**, aunque el valor sea vacío o `False`. Sin eso,
> *"no quiero ningún extra"* y *"todavía no le he preguntado"* son el mismo estado, y al
> corregir cualquier cosa se le volvía a preguntar todo.

> ✅ **`valuation` ya NO es terminal** (I-17 cerrado; verificado contra el código el
> 31-ago-2026). Este párrafo decía lo contrario —*"es un paso TERMINAL, y es un bug conocido"*—
> y llevaba así desde antes de que se arreglara.
>
> En el chat el corte a valoración es **un paso que se acusa** (precio + motivo, una sola
> opción) y de ahí sigue al paso de dirección que ya existía. Lo habilita `valuation_inline`,
> bandera que pone **solo** `agent_booking_step`: en el web no cambia nada. Sus items salen de
> `_visar_wizard_valuation_items()` —uno, precio fijo, `is_valuation: True`— porque
> `_visar_resolve_wizard_items` no emite nada para un corte que nunca elige tramo. Quien
> reporta termitas, chinches o "no sé qué es" **sí puede agendar por WhatsApp**. Detalle
> completo en §(a) del diario del diseño 33.

### `visar_slot_hold.py` — apartado de horario (butaca de cine)

Una reserva pendiente de pago **no consume capacidad** en Odoo: `_get_resources_remaining_capacity`
solo cuenta `appointment.booking.line`, que cuelgan de citas ya confirmadas. Las
`calendar.booking.line` de una reserva sin pagar son invisibles → dos clientes pueden llegar al
pago del mismo horario. En el web el hueco es estrecho; por WhatsApp se ensancha a minutos.

- `HOLD_MINUTES_PARAM = 'visar.slot_hold_minutes'`, default **10**.
- Se descuenta en **`_get_resources_remaining_capacity`**, que es el **único punto por el que
  pasan todos los caminos** (generación de slots, validación final del formulario, lecturas del
  agente). Filtrar solo en `_visar_filter_slots_multi_service` **no habría bastado**: la rama de
  valoración no pasa por ahí.
- **El dueño del apartado no se bloquea a sí mismo** (`visar_hold_owner` en el contexto).
- **Un pago en vuelo congela el reloj** (`is_frozen`) — hoy inerte porque el pago es simulado.

### Extensiones

| Modelo | Archivo | Campos / helpers |
|---|---|---|
| `appointment.type` | `models/appointment_type.py` | `visar_product_tmpl_ids`, `visar_is_master`, **`visar_flow`**. Helpers: `_visar_get_master_appointment_type`, `_visar_get_valuation_appointment_type`, `_visar_resolve_wizard_items`, **`_visar_build_sale_lines`** (incluye add-ons + roedores), `_visar_service_resource_pools`, `_visar_filter_slots_multi_service`, `_visar_quote_booking`, **`_visar_build_native_answer_inputs`**. |
| `appointment.resource` | `models/appointment_resource.py` | `visar_zone_ids`, `visar_service_ids`. |
| `calendar.event` | `models/calendar_event.py` | `visar_zone_id`, `visar_m2` (legacy), **`visar_booking_items`**. |
| `product.template` | `models/product_template.py` | `visar_appointment_type_id` (enlace 1:1 tipo cita). Vista: reordena `optional_product_ids` + tabla add-ons en pestaña Ventas. |
| `sale.order` | `models/sale_order.py` | `_visar_apply_zone_pricelist`. |
| `calendar.booking` | `models/calendar_booking.py` | Reserva previa al pago (de donde sale la liga). |
| `payment.transaction` | `models/payment_transaction.py` | Guardia de la **decisión 8**: la liga de pago vive y muere con el apartado. Al caducar el hold, la liga deja de cobrar; si el slot ya es de otro, se rechaza **antes** de que haya dinero de por medio. |

### Los tres métodos de disponibilidad, y cuál usa cada canal

| Método | Qué hace | Quién lo usa |
|---|---|---|
| `_get_resources_remaining_capacity` (override) | Resta los apartados vivos | **Todos** los caminos |
| `_visar_filter_slots_multi_service` | Exige que los técnicos de **todos** los servicios estén libres a la vez | Web + agente en `mode='wizard'` |
| `_visar_eligible_resources(zone)` | Técnicos de la zona, sin cruce de pools | Rama `mode='valuation'` (no pasa por el filtro multi-servicio) |

⚠️ **La carga por técnico solo es fiable por `appointment.booking.line → appointment.resource`**,
**nunca** por `project.task.user_ids`: 83 tareas activas están asignadas a *admin*, 4 a
`__system__` y 61 a nadie.

### Datos — `data/visar_questions_data.xml`

Preguntas reutilizables para Questions & Answers: Zona, m², plaga, roedores, tipo de plaga.
**No** se muestran en el formulario nativo de cita (desvinculadas de tipos entrada vía migración 19.0.2.0.12).

### Migraciones — catálogo legacy

`migrations/19.0.2.0.7/post-migrate.py` → `_visar_migrate_legacy_catalog`: crea grupos/dimensiones desde campos legacy del producto, enlaza tramos, regla combo. **Solo corre en `-u`.**

`migrations/19.0.2.0.15/post-migrate.py` → marcador de versión tras split en 3 módulos (sin lógica adicional).

### Controlador — `controllers/appointment.py`

Hereda **`WebsiteAppointmentSale`**. Sesión: `SESSION_KEY = 'visar_booking'`.

| Método / ruta | Qué hace |
|---|---|
| **`GET /appointment`** | Cuadro nativo; dominio `visar_flow` ∈ {valuation, wizard}. |
| **`GET /appointment/visar/booking`** | Inicia wizard (paso 1 grupos). |
| **POST …/wizard/services** | Paso 1 → substeps o dimensiones. |
| **GET/POST …/wizard/group/<id>** | Sub-paso dimensiones de un grupo. |
| **GET/POST …/wizard/dimensiones** | Paso rangos; si `is_valuation` → aviso; si no → calificación. |
| **GET/POST …/wizard/calificacion** | Plaga/preventivo, roedores, tipo de plaga. |
| **GET …/wizard/valoracion-aviso** | Aviso: requiere visita valoración $500. |
| **POST …/wizard/valoracion-aviso/continuar** | → `…/visar/valoracion?from_wizard=1`. |
| **POST …/wizard/zona** | Zona → items + pools → redirect maestro (solo flujo normal). |
| `appointment_type_page` | Enruta por `visar_flow` / wizard completo / legacy prequalify. |
| **GET/POST …/visar/valoracion** | Valoración directa o post-wizard: solo Zona → `mode=valuation`. |
| `_visar_enrich_answer_inputs` | Inyecta respuestas Visar en `appointment_answer_input_ids`. |
| `_visar_appointment_quote_context` | Cotización sidebar (wizard, valoración). |
| `_get_slots_from_filter` | Post-filtro multi-técnico (wizard normal). |
| `_redirect_to_payment` | Wizard: N líneas SO (+ add-ons + roedores); valoración: una línea $500. |

### Sesión `visar_booking`

Modo wizard (servicios normales):

```python
{
  'mode': 'wizard',
  'master_appointment_type_id': <id maestro Servicios Visar>,
  'zone_id': 5,
  'selections': {
    'group_ids': [...], 'dimension_ids': [...], 'tier_<dim_id>': <tier_id>,
    'plaga': 'preventivo|plaga', 'roedores': 'si|no', 'tipo_plaga': [...],
  },
  'items': [{'dimension_id', 'tier_id', 'variant_id', 'is_valuation': False, ...}],
  'service_pools': {'<dimension_id>': [resource_ids]},
}
```

### Reglas de precio (D-04/D-05/D-06)

- Combo: reglas `visar.combo.rule` + factor legacy `visar.combo_corte_factor`.
- Valoración: **una** línea $500 si cualquier item `is_valuation`.
- Add-ons obligatorios: sumados por servicio (y por producto roedores si `roedores=si`).
- Zona: pricelist en SO; precio web vía `_get_and_cache_current_pricelist()` (Odoo 19).

## Vistas frontend (`visar_appointment`)

| Archivo | Contenido |
|---|---|
| `views/wizard_templates.xml` | Wizard + **`visar_wizard_calificacion`** + aviso valoración + zona |
| `views/valoracion_templates.xml` | Valoración (`from_wizard`) |
| `views/appointment_templates_appointments.xml` | Sidebar precio multi-línea (`visar_appointment_info_price`) |

---

## Los otros cuatro módulos

Cada uno tiene su documento; aquí solo lo justo para saber qué es y dónde encaja.

| Módulo | Qué añade | Documento |
|---|---|---|
| `visar_subscription` (19.0.1.4.0) | **Pólizas.** Cobro adelantado como línea real del pedido, listas (zona × plan), visitas incluidas por plan independientes del cobro. | [`35-polizas.md`](./35-polizas.md) |
| `visar_field_app` (19.0.1.26.0) | **App de campo.** PIN, worksheet, fotos solo por cámara, firma, reporte PDF al cliente, avisos por WhatsApp. **Aquí vive Mapbox**: geocodificación (`_visar_geo_localize_mapbox`) y ETA de traslado (`_visar_enroute_eta_minutes`, Directions `driving-traffic` con fijo de respaldo). | [`25-field-app.md`](./25-field-app.md) |
| `visar_crm` (19.0.1.3.0) | **Pipeline de leads** del agente: `agent_track_lead` crea el lead en *Nuevo*; avance de etapa, *won* y cron de caducidad. Es donde aterriza el hand-off humano. | [`31-`](./31-whatsapp-crm-lead-mapping.md) / [`32-`](./32-whatsapp-crm-lead-implementation.md) |
| `visar_whatsapp_agent` (19.0.1.8.0) | **Superficie RPC del agente**: **17** métodos, **siete** de ellos escriben. Cuestionario por RPC, días y horarios, apartado, reserva, liga de pago, **reagendado**, recontacto de leads, hand-off y buzón de avisos. | [`27-`](./27-whatsapp-agent.md) / [`33-`](./33-whatsapp-agendado-design.md) / [`87-`](../../../visar_fastapi/.context/87-reagendar-citas.md) |

## Diagrama de flujos

**Dos canales, un cuestionario.** Desde `c115c21` el web y WhatsApp comparten las reglas
(`appointment_wizard_flow.py`); lo que cambia es quién las pinta.

```
                    appointment_wizard_flow.py
                   (podar · secuenciar · normalizar · ofrecer)
                        ▲                        ▲
        controllers/appointment.py        agent_booking_step (RPC)
        (sesión HTTP, formularios)        (estado + respuesta → paso siguiente)
                        ▲                        ▲
                     WEB                    visar_fastapi → WhatsApp

GET /appointment
   ├─ Valoración Técnica (visar_flow=valuation)
   │     └─ …/visar/valoracion → horario → SO $500 (cita tipo Valoración)
   └─ Cita de Servicios (visar_flow=wizard)
         └─ GET /appointment/visar/booking
                ├─ rangos con is_valuation
                │     └─ …/wizard/valoracion-aviso → …/visar/valoracion → horario Valoración
                └─ rangos normales
                      └─ calificación → wizard/zona → maestro Servicios Visar
                            → horario multi-técnico → SO N líneas (+ add-ons)
                                  → confirmar pago → tareas FSM (1 por proyecto)

WhatsApp (agendado, en producción desde 19/20-ago-2026)
   cuestionario → dirección → nombre → extras → póliza
        → días (agent_available_days) → horarios (agent_day_slots)
        → APARTADO 10 min (agent_hold_slot)
        → revisión → liga de pago (agent_prepare_booking)
        → /internal/booking-event ──► el chat avisa y queda listo
   rama de valoración: `valuation` se acusa y sigue a dirección (I-17 CERRADO)
   los horarios ya pasan por el filtro de traslado (visar_travel_feasibility)

WhatsApp (reagendar — implementado, SIN DESPLEGAR)
   my_services → el cliente dice "mueve la 2"
        → días (agent_reschedule_days) → horarios (agent_reschedule_slots)
        → agent_reschedule_confirm ──► calendar.event + booking.line + project.task
   no aparta ni cobra; cancelar NO existe (servicio ya cobrado, sin reembolso)

visar_base: product.template ── visar_tier_ids ──► visar.service.tier ──► product.product
visar_base: visar.zone ── pricelist_id ──► product.pricelist (% zona; Valoración $500 fijo)
visar_fsm:  sale.order.line ── task_id ──► project.task (FSM, agrupado por project_id)
```
