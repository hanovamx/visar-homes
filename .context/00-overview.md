# Visar — Contexto del proyecto

> Carpeta de contexto para asistentes de código (Cursor / Claude). Léela completa
> antes de desarrollar. Está en español; los identificadores de código en inglés.

## Qué es

Visar es una empresa de **fumigación y jardinería**. El proyecto agrega, sobre una instalación de
**Odoo 19 Enterprise**, la capacidad de **reservar servicios desde el sitio web** con un
flujo a la medida.

> **CAMBIO DE PLAN (vigente).** Ya **no** se elige un servicio nativo para reservas multi-servicio.
> Un **wizard multi-paso** pregunta qué se quiere, **resuelve las variantes** y arma **una sola cita**
> con **varias líneas/productos** cobrados. El flujo 1:1 anterior (D-03) queda **superado** para
> reservas multi-servicio, pero sigue disponible en tipos de cita individuales (legacy).

**Punto de entrada (`visar_appointment` v19.0.2.8.0):**
- Cuadro nativo **`/appointment`** con **solo dos** tipos publicados:
  - **Valoración Técnica** (`visar_flow=valuation`) → prequalify **solo Zona** → horario → cita **$500**.
  - **Cita de Servicios** (`visar_flow=wizard`) → wizard D-05 (`/appointment/visar/booking`).
- Tras wizard **normal** → horario del tipo maestro interno **`Servicios Visar`** (`visar_is_master=True`).
- Tras wizard con tramo **`is_valuation`** → **aviso** → mismo flujo que Valoración Técnica directa (no maestro).
- Flujo legacy D-03: tipos individuales (no publicados) → `prequalify` (Zona + m² numérico).

Flujo del wizard "Cita de Servicios" (D-05):

1. **Paso 1 — Servicios:** grupos configurables (`visar.service.group`, multi-selección).
2. **Sub-pasos — Dimensiones por grupo** (si el grupo tiene >1 dimensión activa).
3. **Paso rangos:** una pregunta por dimensión, opciones = tramos `visar.service.tier`.
4. **Si algún tramo tiene `is_valuation`:** pantalla **aviso** (costo $500) → flujo valoración directa.
5. **Si no:** **Paso calificación** (plaga/preventivo, roedores, tipo de plaga) → **Paso zona** → resuelve items + pools → horario maestro multi-técnico.
6. Cliente elige horario y paga; la cita guarda `visar_zone_id` + `visar_booking_items` (JSON) + respuestas nativas en **Questions & Answers**.

El tabulador completo está en [`70-tabulador.md`](./70-tabulador.md).

## Requerimientos formales

- **D-03** *(legacy 1 servicio)* — Formulario previo + filtrar técnicos.
- **D-04** — Asignación automática de variante y precio según respuestas.
- **D-05** — Wizard multi-servicio: una cita con varios productos/variantes; reglas del tabulador.
- **D-06** — Add-ons configurables (`optional_product_ids` + Obligatorio / Cantidad).
- **D-07** — Generación de servicios externos (FSM) agrupados por proyecto al confirmar pago.

Detalle en [`10-requirements.md`](./10-requirements.md).

## Estado actual (resumen — 31-ago-2026)

- **Agendado completo por WhatsApp: EN PRODUCCIÓN.** El cliente recorre el cuestionario,
  elige día y hora, aparta el horario y recibe una liga de pago **sin salir del chat**. Entró
  entre el 19 y el 20 de agosto de 2026. El módulo `visar_whatsapp_agent` **ya no es de solo
  lectura**: escribe `visar.slot.hold`, `calendar.booking`, `sale.order`, `crm.lead`,
  `calendar.event`, `project.task` y `visar.wa.booking.message`. Diseño y estado detallado en
  [`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md).

  > ⚠️ **Los "dos huecos abiertos" que decía aquí están CERRADOS** (verificado contra el código
  > el 31-ago-2026). Se dejan nombrados porque medio `.context/` sigue citándolos:
  > - ~~⛔ La rama de valoración no llega a horarios (§10.7 / I-17)~~ → **cerrada**. `valuation`
  >   dejó de ser terminal en el chat: es un paso que se acusa y sigue al de dirección
  >   (`valuation_inline`, `_visar_wizard_valuation_items`). Ver §(a) del diario del doc 33.
  > - ~~⛔ La factibilidad de traslado no existe en código (§5, decisiones 7/14)~~ →
  >   **construida**: `visar_appointment/models/visar_travel_feasibility.py` (593 líneas). Es un
  >   **presupuesto entre paradas**, no un radio, y sus minutos son configurables
  >   (`visar.travel.minutes`, por defecto 20).

- **Reagendar por WhatsApp:** **en producción desde el 4-sep-2026**. El cliente mueve una cita ya
  pagada desde el chat, con 24 h de antelación en las dos puntas y 2 cambios por cita
  (`visar.reschedule.*`). Cancelar **no existe** a propósito: el servicio está cobrado y no hay
  flujo de reembolso. Ver `visar_fastapi/.context/87-reagendar-citas.md`.
- **Recontacto de leads fríos:** **en producción desde el 4-sep-2026**. Ver
  `visar_fastapi/.context/86-recontacto-de-leads.md`.
- **Ajustes → Visar** (`res.config.settings` en `visar_base`): apartado, traslado entre
  servicios y las dos reglas de reagendado dejaron de ser parámetros del sistema sin pantalla.

- **Pólizas (suscripciones):** implementado y **desplegado en producción**. El cobro de
  2 meses por adelantado es una **línea real del pedido** (antes era un multiplicador al
  facturar que el sitio web nunca aplicaba), se contrata en un **paso del wizard** tras
  *¿Deseas agregar algo más?*, y la primera visita hereda fecha y técnico de la cita.
  Ver [`35-polizas.md`](./35-polizas.md).
- **D-03 (legacy):** implementado.
- **D-05:** implementado — wizard configurable, multi-técnico, SO multi-línea, entrada `/appointment`.
- **D-04:** implementado junto con D-05 — pricelist por zona, combo, valoración dedupe.
- **D-06:** implementado en `visar_base` — tabla add-ons, inyección obligatoria en checkout.
- **D-07:** parcial en `visar_fsm` (**v19.0.1.2.0**) — tareas agrupadas por proyecto, add-ons como materiales, técnico/fecha desde cita, **orden de venta completa en tarea FSM** (`visar_sale_order_id`); pendiente worksheet, reportes dual, WhatsApp.
- **Calificación wizard:** implementado — paso plaga/roedores/tipo plaga + producto roedores + estaciones obligatorias.
- **Respuestas nativas:** híbrido implementado — zona, m² (rangos) y calificación en Questions & Answers.
- **E2E web:** en curso; validar wizard completo con calificación, add-ons y generación FSM.

> ⚠️ **Este servidor es producción.** `/opt/custom` es el addons path del Odoo que corre
> (BD `visar-db`). Editar aquí es editar producción: el proceso vivo sigue con el Python que
> cargó en memoria, pero **cualquier reinicio levanta lo que haya en disco**. Nunca correr
> tests contra `visar-db` — ver [`60-odoo19-conventions.md`](./60-odoo19-conventions.md).

Ver [`50-status-roadmap.md`](./50-status-roadmap.md).

## Mapa de carpetas

**Siete módulos**, con la cadena de dependencias `visar_base → visar_fsm →
{visar_appointment, visar_field_app}`. Versiones **releídas de los `__manifest__.py` del árbol
de trabajo el 31-ago-2026** — las que había aquí llevaban once días viejas:

```
VISAR/repo/                ← Git: github.com/luisgarza-g/visar-luisg (rama main)
├── .context/              ← esta carpeta (documentación para desarrollar)
├── visar_base/            ← catálogos compartidos + buzón WhatsApp saliente + Ajustes → Visar (v19.0.1.10.0)
├── visar_fsm/             ← FSM: tareas agrupadas por proyecto (v19.0.1.1.0)
├── visar_appointment/     ← wizard web + citas + cuestionario compartido + factibilidad de traslado (v19.0.2.8.0)
│   └── migrations/        ← post-migrate catálogo legacy (¡solo en upgrade!)
├── visar_field_app/       ← app de campo técnicos (PIN/POS) (v19.0.1.26.0) — ver 25-field-app.md
├── visar_subscription/    ← pólizas: cobro adelantado + visitas FSM por periodo (v19.0.1.4.0) — ver 35-polizas.md
├── visar_crm/             ← pipeline de leads del agente (v19.0.1.3.0) — ver 31/32-whatsapp-crm-lead-*.md
└── visar_whatsapp_agent/  ← superficie RPC del agente WhatsApp, lectura Y escritura (v19.0.1.8.0) — ver 27-whatsapp-agent.md
```

> **4º módulo (jul-2026):** `visar_field_app` se añadió después de la última revisión general
> de esta carpeta. Cubre la mitad "UI técnico / worksheet / fotos / firma" de D-07. Documentado
> en [`25-field-app.md`](./25-field-app.md).

> **5º módulo (jul-2026):** `visar_whatsapp_agent` — superficie RPC para un **agente de IA por
> WhatsApp**. La otra mitad es un servicio externo FastAPI (`visar_fastapi/`, fuera de este
> repo, con su propio `.context/`). Documentado en
> [`27-whatsapp-agent.md`](./27-whatsapp-agent.md).
>
> ⚠️ **Ya NO es de solo lectura.** Esa fue la fase 1 y terminó: desde `9e606c9` (17-ago-2026)
> el módulo aparta horarios, crea reservas, pedidos y ligas de pago. Cualquier frase de "solo
> lectura, no agenda citas" que quede en esta carpeta está obsoleta.

> **7º módulo (ago-2026):** `visar_crm` — el pipeline donde aterrizan los leads y el hand-off
> humano del agente. Ver [`31-whatsapp-crm-lead-mapping.md`](./31-whatsapp-crm-lead-mapping.md)
> y [`32-whatsapp-crm-lead-implementation.md`](./32-whatsapp-crm-lead-implementation.md).

> ⚠️ **Setup parcial en install:** `visar_fsm` tiene `post_init_hook` (proyectos FSM). El catálogo
> legacy y tipos de entrada de `visar_appointment` siguen en `migrations/` solamente.
> **Antes del go-live, leer [`80-deploy-prod.md`](./80-deploy-prod.md).**

Odoo 19 core: `<raíz del checkout>/odoo_19_visar/odoo/addons/` en local; en el servidor, los
módulos custom viven en **`/opt/custom`** (no en una ruta `visar-homes/`).

## Lectura recomendada

1. `10-requirements.md` — qué se pide.
2. `70-tabulador.md` — rangos, precios y reglas.
3. `20-architecture.md` — cómo están construidos los módulos.
4. `60-odoo19-conventions.md` — **gotchas Odoo 19** (pricelist, POST, QWeb).
5. `50-status-roadmap.md` — qué falta y fixes recientes.
6. `80-deploy-prod.md` — **leer antes del go-live** (fix install vs upgrade).
7. `91-reunion-2026-06-22.md` — reglas de negocio acordadas con Visar.
   > ⚠️ Acta de junio. Su reparto del bloque de cita (40 min = 20 servicio + 20 traslado)
   > quedó **superado** por la confirmación del 19-ago: 60 min = **20 traslado + 40 servicio**.
8. `25-field-app.md` — **app de campo técnicos** (`visar_field_app`, 4º módulo).
9. `27-whatsapp-agent.md` — **agente de IA por WhatsApp** (`visar_whatsapp_agent`, 5º módulo).
10. `35-polizas.md` — **pólizas / suscripciones** (`visar_subscription`, 6º módulo): cobro
    adelantado, listas (zona × plan) y paso de póliza en el wizard.
11. `33-whatsapp-agendado-design.md` — **el agendado completo por WhatsApp** (todo en el chat
    salvo el pago). **Implementado y en producción** desde el 19/20-ago-2026. Los dos huecos que
    este índice anunciaba —la rama de valoración (§10.7) y la factibilidad de ruta (§5)— están
    **cerrados**; el diario del propio documento (§(a) y siguientes) lo cuenta, pero su cabecera
    todavía los anuncia como abiertos. Es el documento vivo del proyecto y el más largo: si vas
    a tocar agendado, empieza por aquí — y lee el diario antes que la cabecera.
12. `visar_fastapi/.context/87-reagendar-citas.md` — **mover una cita ya pagada desde el chat**
    (y por qué cancelar no existe). En producción desde el 4-sep-2026.
