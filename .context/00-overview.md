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

**Punto de entrada (v19.0.2.0.15):**
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

## Estado actual (resumen — 26-jun-2026)

- **D-03 (legacy):** implementado.
- **D-05:** implementado — wizard configurable, multi-técnico, SO multi-línea, entrada `/appointment`.
- **D-04:** implementado junto con D-05 — pricelist por zona, combo, valoración dedupe.
- **D-06:** implementado en `visar_base` — tabla add-ons, inyección obligatoria en checkout.
- **D-07:** parcial en `visar_fsm` (**v19.0.1.0.1**) — tareas agrupadas por proyecto, add-ons como materiales, técnico/fecha desde cita, **orden de venta completa en tarea FSM** (`visar_sale_order_id`); pendiente worksheet, reportes dual, WhatsApp.
- **Calificación wizard:** implementado — paso plaga/roedores/tipo plaga + producto roedores + estaciones obligatorias.
- **Respuestas nativas:** híbrido implementado — zona, m² (rangos) y calificación en Questions & Answers.
- **E2E web:** en curso; validar wizard completo con calificación, add-ons y generación FSM.

Ver [`50-status-roadmap.md`](./50-status-roadmap.md).

## Mapa de carpetas

```
VISAR/repo/                ← Git: github.com/luisgarza-g/visar-luisg (rama main)
├── .context/              ← esta carpeta (documentación para desarrollar)
├── visar_base/            ← catálogos compartidos (v19.0.1.0.0)
├── visar_fsm/             ← FSM: tareas agrupadas por proyecto (v19.0.1.0.1)
├── visar_appointment/     ← wizard web + citas (v19.0.2.0.15)
│   └── migrations/        ← post-migrate catálogo legacy (¡solo en upgrade!)
└── visar_field_app/       ← app de campo técnicos (PIN/POS) (v19.0.1.4.0) — ver 25-field-app.md
```

> **4º módulo (jul-2026):** `visar_field_app` se añadió después de la última revisión general
> de esta carpeta. Cubre la mitad "UI técnico / worksheet / fotos / firma" de D-07. Documentado
> en [`25-field-app.md`](./25-field-app.md).

> ⚠️ **Setup parcial en install:** `visar_fsm` tiene `post_init_hook` (proyectos FSM). El catálogo
> legacy y tipos de entrada de `visar_appointment` siguen en `migrations/` solamente.
> **Antes del go-live, leer [`80-deploy-prod.md`](./80-deploy-prod.md).**

Odoo 19 core: `/Users/luisgarza27/Documents/HANOVA/odoo_19_visar/odoo/addons/`

## Lectura recomendada

1. `10-requirements.md` — qué se pide.
2. `70-tabulador.md` — rangos, precios y reglas.
3. `20-architecture.md` — cómo están construidos los tres módulos.
4. `60-odoo19-conventions.md` — **gotchas Odoo 19** (pricelist, POST, QWeb).
5. `50-status-roadmap.md` — qué falta y fixes recientes.
6. `80-deploy-prod.md` — **leer antes del go-live** (fix install vs upgrade).
7. `91-reunion-2026-06-22.md` — reglas de negocio acordadas con Visar.
8. `25-field-app.md` — **app de campo técnicos** (`visar_field_app`, 4º módulo).
