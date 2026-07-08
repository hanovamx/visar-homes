# Requerimientos D-03, D-04, D-05, D-06 y D-07

Fuente: fichas de desarrollo de Visar (sesión con Majo) + tabulador final (`70-tabulador.md`) +
touchpoint 22-jun-2026 (`91-reunion-2026-06-22.md`).

> **D-03 quedó superado por D-05.** D-03 asumía **un** servicio por reserva (Zona + m² numérico →
> una cita, una variante). El cambio de plan exige un **wizard multi-servicio** que arma **una sola
> cita con varias líneas/variantes**. Se conserva la infraestructura de D-03 (páginas propias +
> sesión + filtrado de técnicos por zona) como cimiento, pero el flujo de cliente lo define D-05.

## D-05 — Wizard multi-servicio: una cita con varios productos/variantes (VIGENTE)

**Módulos afectados:** Citas (`appointment.type`), Productos (`product.product`), Ventas, Website.

**Problema / cambio:** se **conserva el cuadro nativo de citas** (`/appointment`), pero reducido a
**dos tipos de cita** *(implementado v19.0.2.0.3 — ancla `entry-point-d05`)*:
1. **Valoración Técnica** — al elegirlo se pregunta **solo la Zona**, luego horario; agenda la visita
   de **$500** (producto Valoración). Es la entrada directa a una valoración (independiente del wizard).
2. **Cita de Servicios** — al elegirlo se abre el **wizard guiado** que, según las respuestas,
   determina **qué servicios y qué variantes** se necesitan, y genera **una sola cita** con **todos los
   productos** asignados y cobrados.

**Flujo requerido (tipo "Cita de Servicios"):**
1. **Paso 1 — Servicios:** `[ ] Fumigación` `[ ] Corte y poda` (se pueden ambos).
2. **Paso 2 — Tipo de fumigación** (solo si marcó Fumigación): `[ ] Interna` `[ ] Externa` (ambas posibles).
3. **Paso 3 — Dimensiones** (una pregunta por selección, opciones = **rangos del tabulador**):
   - Fumigación interna → *Dimensiones de la propiedad interior* (m² interior).
   - Fumigación externa → *Dimensiones de la propiedad exterior* (m² de jardín).
   - Corte y poda → *m² de poda* (m² de jardín).
4. **Paso 4 — Calificación** (solo flujo normal): plaga/preventivo, roedores, tipo de plaga (opcional).
5. **Paso 5 — Zona geográfica** (catálogo de zonas Visar).
6. Con las respuestas el sistema:
   - resuelve **una variante por servicio** (rango → `visar.service.tier` → variante),
   - aplica reglas del tabulador (exterior aditivo, **combo −50% del corte**, **valoración $500**),
   - filtra **técnicos elegibles** (servicio + zona) y muestra **un solo horario** común,
   - el cliente elige horario y paga **una sola cita** con **todas las líneas de producto** (+ add-ons obligatorios).
7. Al confirmar pago → generación automática de **tareas FSM** agrupadas por proyecto (D-07).

**Reglas de negocio (de `70-tabulador.md`):**
- **Una sola cita, varias líneas:** cada servicio elegido = una línea de producto con su variante.
- **Fumigación exterior es aditiva** a interior (su tramo 0–50 m² es gratis / precio 0), pero
  **se puede agendar sin interior** (servicio independiente válido).
- **Combo:** solo cuando la reserva incluye los **tres** servicios — fumigación **interior + exterior +
  corte de pasto** — la línea de corte se cobra al **50%** (factor `visar.combo_corte_factor = 0.5`).
  Si falta cualquiera de los tres, el corte va a precio completo.
- **Valoración técnica $500** cuando un servicio excede su rango máximo (interior >1000, exterior >500,
  corte >200); abonable; **una sola** línea de valoración aunque varios servicios la requieran (provisional).
- **Zona = pricelist con %** (A +15%, B base, C −10%); valoración $500 plano.
- **Técnicos: varios por cita.** Se asignan los técnicos necesarios para cubrir **todos** los servicios
  elegidos; si un técnico no cubre todos, se agregan los que falten (no es 1 técnico por cita).

**Criterios de aceptación:**
- El wizard muestra solo las preguntas que correspondan a lo seleccionado.
- Las opciones de dimensión son los **rangos del tabulador** (no un m² numérico libre).
- Se genera **una** cita con **N líneas** de producto, cada una con la variante y el precio correctos.
- Combo (los tres servicios) y valoración se aplican correctamente al total.
- Los técnicos asignados cubren todos los servicios elegidos (pueden ser varios en la misma cita).
- Las respuestas quedan guardadas en la cita para referencia del administrativo.

**Puntos resueltos con Visar (22-jun-2026):**
- Exterior **sin** interior: **válido**.
- Combo = **interior + exterior + corte** (los tres juntos).
- Valoración: **una** línea $500 por ahora (provisional, a reconfirmar).
- Varios servicios → **varios técnicos** por cita si hace falta para cubrir todo.

**Puntos aún abiertos:**
- ¿El +15%/−10% por zona y el factor combo 0.5 son reglas estables?
- E2E web manual de ambos flujos desde `/appointment` (checklist en `50-status-roadmap.md`).

## D-06 — Productos add-on configurables sobre `optional_product_ids` (IMPLEMENTADO — `visar_base`)

Fuente: touchpoint 22-jun-2026 (`91-reunion-2026-06-22.md`, §2 Productos add-on).

**Módulos afectados:** Productos (`product.template`), Citas (`appointment.type`), Ventas, Website.

**Problema / motivación:** ciertos servicios (p. ej. fumigación) llevan **productos add-on** que se
cobran *dentro* del mismo servicio (estación antirroedores $100 c/u, tapa de registro, guardapolvos).
El negocio quiere que algunos add-ons se **agreguen automáticamente** con una **cantidad fija** (la regla
acordada: si el cliente declara problema de roedores → **3 estaciones por default**), y otros queden como
**sugerencia opcional** (cross-sell). El campo nativo `optional_product_ids` (M2m plano de `product.template`)
no soporta atributos por relación ni se dispara en el flujo de reserva Visar (el carrito se arma
programáticamente, no por la página de tienda).

**Requerido:**
1. **Extender** la configuración de `optional_product_ids` con una **tabla por producto add-on** con dos
   atributos: **`Obligatorio` (bool)** y **`Cantidad` (int ≥ 1)**.
2. La tabla solo se muestra cuando hay productos cargados en `optional_product_ids`.
3. **Comportamiento:**
   - `Obligatorio = True, Cantidad = N` → el add-on **siempre** se agrega a la cita con **N** unidades
     (igual que el flujo nativo, pero forzado).
   - `Obligatorio = False, Cantidad = N` → sugerencia opcional (cross-sell), N = cantidad por defecto si
     el cliente la acepta.
4. En el flujo de reserva, los add-ons **obligatorios** del/los servicio(s) elegidos se inyectan como
   líneas extra del carrito (con su cantidad), y deben reflejarse tanto en el **checkout** como en el
   **sidebar de cotización** (consistencia de total).
5. El flujo de **valoración técnica** ($500, una línea) **no** agrega add-ons.

**Diseño técnico (resumen — detalle en plan de implementación):**
- **Patrón modelo de unión (*through/junction model*):** nuevo `visar.product.optional.line`
  (`product_tmpl_id`, `optional_product_id`, `is_mandatory`, `quantity`) expuesto como `One2many`
  `visar_optional_line_ids` en `product.template`. Promueve el M2m plano a tabla con atributos.
- Sincronización con el campo nativo `optional_product_ids` (mantiene el cross-sell estándar):
  **DECIDIDO — Opción A.** El M2m nativo es el selector; al cargar productos ahí, la tabla
  `visar_optional_line_ids` se autogenera (una fila por producto, default `Obligatorio=False,
  Cantidad=1`) vía onchange + reconciliación en `create`/`write`. La tabla es `invisible` si el M2m
  está vacío.
- Inyección de obligatorios en `appointment.type._visar_build_sale_lines` (las líneas llevan cantidad
  propia; `_redirect_to_payment` usa `quantity` por línea). `_visar_quote_booking` reusa esas líneas.
- **Duplicados — DECIDIDO: SUMAR.** Si el mismo add-on es obligatorio en varios servicios de la misma
  reserva, las cantidades **se suman** (no se deduplica). Ej.: estación ×3 en interior + ×3 en exterior →
  **6 estaciones** en la cita. (Escenario poco probable por cómo se manejará el catálogo, pero el
  comportamiento definido es sumar.)

**Constraints:** único `(product_tmpl_id, optional_product_id)`; `optional_product_id != product_tmpl_id`;
`quantity >= 1`. Alta en `ir.model.access.csv`. Subir versión + migración idempotente si se siembra config.

**Fase 2 — gating roedores (IMPLEMENTADO jun-2026 en `visar_appointment`):**
- Paso wizard **calificación** pregunta plaga/preventivo, roedores y tipo de plaga.
- Si `roedores=si` → se agrega producto `visar_is_roedores` + add-ons obligatorios de ese producto
  (p. ej. estaciones ×3) vía `_visar_build_sale_lines(include_roedores=True)`.
- Respuestas persistidas en Questions & Answers (`visar_questions_data.xml` + `_visar_enrich_answer_inputs`).
- Pendiente: definir si add-ons son **precio plano** (excluidos del % por zona) o siguen pricelist de zona.

**Criterios de aceptación:**
- Cargar productos en `optional_product_ids` despliega la tabla con columnas Obligatorio / Cantidad.
- Add-on `Obligatorio=True, Cantidad=3` en fumigación → la reserva genera líneas con 3 unidades del add-on;
  total de checkout y sidebar coinciden.
- Add-on `Obligatorio=False` → no se auto-agrega en el flujo de reserva.
- Mismo add-on obligatorio en 2 servicios → cantidades **sumadas** (ej. ×3 + ×3 = 6 unidades).
- Flujo de valoración no agrega add-ons.

## D-07 — Generación de servicios externos / órdenes de trabajo (FSM) (PARCIAL — `visar_fsm`)

Fuente: touchpoint 22-jun-2026 (`91-reunion-2026-06-22.md`, §6 Orden de trabajo / reporte) +
refinamientos posteriores con Visar.

**Módulos afectados:** Field Service (`industry_fsm`, `industry_fsm_sale`, `worksheet`),
Ventas (`sale_project`), Proyecto (`project.task`), y el flujo Visar (`appointment.type`, controlador).

**Problema / motivación:** además de la cita (`calendar.event`) y la venta (`sale.order`), cada reserva
debe generar el **servicio externo / orden de trabajo** que el técnico ejecuta y reporta (hoja de trabajo
con checklist, fotos, firma, materiales) — el "reporte de servicio" del §6 de la reunión. En Odoo un
servicio externo **es una `project.task`** dentro de un proyecto con `is_fsm = True`.

**Mecanismo elegido: puente nativo Venta → Tarea (Opción A).**
- Productos de servicio Visar con `service_tracking = 'task_global_project'` + `project_id = <proyecto FSM>`.
- Al **confirmar el pago** (la SO se confirma en checkout; Visar cobra por adelantado), Odoo dispara
  `sale.order._action_confirm()` → `sale.order.line._timesheet_service_generation()` →
  `_timesheet_create_task()`, creando la(s) tarea(s) FSM.
- `industry_fsm_sale` ya cablea **tarea ↔ orden de venta** (`task.sale_order_id`, `task.sale_line_id`),
  así que los materiales/add-ons consumidos regresan como líneas a la SO (base del Opsel, §7).

**Proyectos FSM (configurables, NO hardcodeados):**
- Habrá (al menos) **dos proyectos FSM**: **Fumigación** y **Corte de pasto / Mantenimiento de áreas
  verdes**. Cada producto de servicio apunta a su proyecto vía `product.template.project_id`.
- **REQUISITO DURO — todo configurable desde la interfaz de Odoo, cero hardcode.** El ruteo
  servicio→proyecto se define **por el campo `project_id` de cada producto** (UI estándar de producto),
  no por nombres ni IDs fijos en el código. La lógica de agrupado debe leer **el proyecto configurado en
  cada línea**, de forma genérica, para que dar de alta un **nuevo caso de negocio** (nuevo servicio y/o
  nuevo proyecto FSM) sea **solo configuración**, sin tocar código. El seeding inicial se hace por
  `hooks.py`/migración idempotente (no XML de productos), pero queda **reconfigurable** desde el backend.

**Regla de agrupado: UNA tarea por proyecto.**
- El generador nativo crea **una tarea por línea de servicio** → habría 3 tareas para
  fumigación interna + externa + corte. Se requiere **consolidar por proyecto**.
- Implementación: **override de `_timesheet_service_generation`** (o wrapper) que:
  1. Filtra las líneas de servicio Visar de la SO.
  2. Las **agrupa por el `project_id` configurado** en cada producto (genérico, sin nombres fijos).
  3. Por cada grupo de proyecto crea **UNA** tarea, fija `sale_line_id` a una línea representante
     (criterio estable: menor `sequence`/`id`) y pone `task_id` = esa tarea en **todas** las demás
     líneas del grupo (incluidos los add-ons D-06).
- Ejemplo (interna + externa + corte): **2 servicios externos** → 1 tarea proyecto Fumigación (líneas
  interna+externa+add-ons), 1 tarea proyecto Corte.

**Modelado tarea ↔ varios servicios (aclaración + cómo verlo en la tarea):**
- **NO** se vuelve multi-valor `project.task.sale_line_id` (es M2o por diseño; el core lo asume único
  para facturación/timesheet). El multi-servicio se logra con el campo **`sale.order.line.task_id`**
  (varias líneas → una misma tarea), que es el mecanismo nativo de agregación.
- **Confirmado en el core (Odoo 19):**
  - `project.task.sale_line_id` → **Many2one** (`sale_project/models/project_task.py:26`). Es la línea
    contra la que se imputa el tiempo/timesheet para facturar. Hacerlo M2m rompería el puente nativo
    FSM↔Venta (`_compute_sale_order_id`, "to invoice", materiales que regresan a la SO).
  - `sale.order.line.task_id` → **Many2one** "Generated Task" (`sale_project/models/sale_order_line.py:14`).
    Varias líneas pueden apuntar a la **misma** tarea. Este es el lado correcto para agrupar N líneas.
- **Ya implementado:** `visar_fsm/models/sale_order_fsm.py::_visar_create_grouped_tasks` agrupa las líneas
  de servicio por `product_id.project_id` y crea **una tarea por proyecto**: la primera línea (menor
  `sequence`/`id`) queda como `task.sale_line_id` (representante) y las demás se cuelgan con
  `task_id = task.id`. Por tanto, **dos podas en la misma cita ya caen en una sola tarea** si ambos
  productos apuntan al mismo proyecto FSM (Mantenimiento de Áreas Verdes). No requiere tocar `sale_line_id`.

### Decisión vigente (2026-06-26): ocultar `sale_line_id` y exponer la orden de venta completa

**Contexto que habilita la decisión:** **todos** los productos vendidos en este proyecto usan política de
facturación **precio fijo** o **prepago** (no "según tiempo/timesheet"). Por eso la granularidad por *línea*
de `sale_line_id` **no aporta** a la operación administrativa: al administrativo le interesa ver "esta tarea
pertenece a la orden **S0xxxx**", no contra qué línea específica se imputaría el tiempo. Sigue siendo cierto
todo lo anterior (no se puede ni conviene volver `sale_line_id` multi-valor ni cambiar su tipo a `sale.order`).

**Qué se hace:**
1. **`sale_line_id` se OCULTA en la vista** de la tarea (label + `div` contenedor), **pero se sigue
   asignando en el código** (`_visar_create_grouped_tasks`). No se elimina ni se deja de poblar.
2. **Se crea un campo nuevo `visar_sale_order_id`** (related a `sale_order_id`) que liga/expone la **orden de
   venta completa**, visible y de solo lectura en la tarea.
3. **Se descarta** la pestaña One2many `visar_sale_line_ids` que se había propuesto antes (no se implementa).

> ⚠️ **NO eliminar ni dejar de asignar `sale_line_id`.** Solo se oculta en la UI. El core lo necesita para
> computar `sale_order_id` (de donde cuelga `visar_sale_order_id`), para el retorno de materiales/add-ons a
> la SO y para la cantidad entregada. Si se deja de poblar, se rompe el puente FSM↔Venta. El agrupado
> existente (`_visar_create_grouped_tasks`) ya lo asigna a la línea representante: **no tocar esa lógica.**

**Módulo destino:** `visar_fsm` (mismo patrón que `calendar_event.visar_fsm_task_ids`).

**1) Modelo — nuevo archivo `visar_fsm/models/project_task.py`:**

```python
# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # Orden de venta completa que originó este servicio externo.
    # Related sobre el nativo sale_order_id (que el core computa desde sale_line_id).
    # Reemplaza en la UI al campo nativo `sale_line_id`, que se conserva OCULTO y se
    # sigue asignando en _visar_create_grouped_tasks (no eliminar esa asignación).
    visar_sale_order_id = fields.Many2one(
        'sale.order',
        string="Orden de venta",
        related='sale_order_id',
        store=True,
        readonly=True,
        help="Orden de venta completa de la que proviene este servicio externo "
             "(incluye las dos podas, fumigaciones y add-ons de la misma cita).")
```

> Nota: el nativo `project.task.sale_order_id` ya ES el enlace a la orden completa; `visar_sale_order_id`
> es un alias propio (related) para mostrarlo con etiqueta Visar y aislarlo de cambios del core. Si se
> prefiere no crear campo nuevo, se puede simplemente des-ocultar `sale_order_id` en la vista del paso 3.

**2) Registrar el modelo — editar `visar_fsm/models/__init__.py`:**

```python
from . import project_task   # añadir esta línea
```

**3) Vista — nuevo archivo `visar_fsm/views/project_task_views.xml`:**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <record id="project_task_view_form_visar_fsm" model="ir.ui.view">
        <field name="name">project.task.form.visar.fsm</field>
        <field name="model">project.task</field>
        <field name="inherit_id" ref="project.view_task_form2"/>
        <field name="priority">200</field>   <!-- carga después de sale_project (priority 100) -->
        <field name="arch" type="xml">

            <!-- Ocultar el campo nativo sale_line_id (label + div contenedor).
                 El campo sigue existiendo y poblándose; solo se oculta en la UI. -->
            <xpath expr="//label[@for='sale_line_id']" position="attributes">
                <attribute name="invisible">1</attribute>
            </xpath>
            <xpath expr="//div[@name='sale_line_div']" position="attributes">
                <attribute name="invisible">1</attribute>
            </xpath>

            <!-- Mostrar la orden de venta completa en su lugar. -->
            <xpath expr="//div[@name='sale_line_div']" position="after">
                <field name="visar_sale_order_id"
                       options="{'no_create': True}"
                       readonly="1"
                       invisible="not visar_sale_order_id"/>
            </xpath>

        </field>
    </record>

</odoo>
```

> El `xpath` apunta a `div[@name='sale_line_div']` y `label[@for='sale_line_id']`, que es donde
> `sale_project/views/project_task_views.xml` (vista `view_sale_project_inherit_form`, hereda
> `project.view_task_form2`) renderiza los dos `sale_line_id` por grupo de seguridad. Ocultar el div
> cubre ambos de una vez. **Verificar las anclas** si se actualiza Odoo.

**4) Manifest — añadir la vista a `visar_fsm/__manifest__.py` (`data`):**

```python
'data': [
    'views/appointment_resource_views.xml',
    'views/calendar_event_views.xml',
    'views/project_task_views.xml',   # añadir esta línea
],
```

**5) Versión:** subir `version` de `visar_fsm` (p. ej. `19.0.1.0.1`) para forzar `-u visar_fsm` en deploy.
El campo es related stored → al actualizar se recomputa solo; no requiere migración manual.

**Criterios de aceptación (de esta decisión):**
- En la ficha de cualquier tarea/servicio externo Visar **no** aparece "Artículo de la orden de venta"
  (`sale_line_id` oculto), y **sí** aparece "Orden de venta" (`visar_sale_order_id`) apuntando a la SO completa.
- `sale_line_id` **sigue asignado** en backend (verificable en debug/ORM); el agrupado de líneas no cambia.
- El botón nativo "Sales Order", el retorno de materiales a la SO y la cita ↔ tarea siguen funcionando.
- **No** existe pestaña/campo `visar_sale_line_ids` (propuesta descartada).

**Implementado (26-jun-2026):**
- Proyectos FSM configurables vía `visar_fsm/hooks.py` (`post_init_hook` + migración 19.0.2.0.14).
- Agrupado **una tarea por proyecto** en `_timesheet_service_generation`.
- Add-ons colgados como materiales de la tarea del servicio que los declara.
- Técnico → `user_ids` y fechas → `planned_date_begin`/`date_deadline` desde `calendar.event`.
- `calendar.event.visar_fsm_task_ids` (computed).
- UI tarea FSM: `visar_sale_order_id` visible; `sale_line_id` oculto (`visar_fsm` v19.0.1.0.1).

**Pendiente:**
- **Worksheet (`worksheet.template`):** checklist/fotos/firma; **versión interna vs cliente** (§6 reunión).
- **Cross-link cita ↔ tarea:** hoy vía SO compartida; falta enlace directo en UI agenda.
- **E2E validado** en app móvil técnico.
- Definir worksheet específico para valoración técnica (proyecto "Valoraciones / Inspecciones" ya existe).

**Criterios de aceptación:**
- Reserva con fumigación interna + externa + corte → se generan **exactamente 2 tareas FSM** (1 por
  proyecto), con las líneas correctas enlazadas por `task_id`.
- El ruteo servicio→proyecto se cambia **solo desde la interfaz** (campo `project_id` del producto);
  agregar un nuevo servicio/proyecto no requiere cambios de código.
- Re-confirmar la SO **no duplica** tareas (idempotencia preservada del generador nativo).
- Los add-ons (D-06) quedan colgados como materiales de la tarea de su servicio.
- La tarea trae técnico, fecha planeada y plantilla de hoja de trabajo correctos.

## D-03 — Formulario dinámico en sitio web con preguntas antes de mostrar horarios *(superado por D-05)*

**Módulo afectado:** Website > Citas (`appointment.type`).

**Problema:** en el flujo nativo de Odoo Appointments el cliente elige horario **antes**
de responder preguntas. Se requiere **invertir** el orden: las preguntas primero, para
poder calcular la variante y filtrar los horarios correctos.

**Flujo requerido:**
1. El cliente selecciona el tipo de servicio (ej. Fumigación interior).
2. Se muestran ÚNICAMENTE dos preguntas: **Zona geográfica** y **Metros cuadrados**.
3. Con esas dos respuestas, el sistema calcula internamente: variante de producto + precio + filtro de técnicos elegibles.
4. Se muestran ÚNICAMENTE los horarios de los técnicos elegibles para ese servicio + zona.
5. El cliente elige horario y procede al pago.

**Preguntas del formulario (aplica a todos los servicios):**
- Zona geográfica: catálogo de zonas definido por Visar.
- Metros cuadrados del inmueble: campo numérico.

**Nota de alcance:** solo se preguntan Zona y m². Tipo de inmueble, pisos, nivel de
infestación y m² de jardín quedan fuera de esta fase.

**Criterios de aceptación:**
- Las preguntas aparecen ANTES de la vista de selección de horario.
- Los horarios mostrados corresponden únicamente a técnicos elegibles (servicio + zona).
- Si no hay técnicos disponibles, se muestra mensaje adecuado (sin romper el flujo).
- Las respuestas quedan guardadas en la cita agendada para referencia del administrativo.

## D-04 — Asignación automática de variante de producto y precio según respuestas

**Módulos afectados:** Citas (`appointment.type`), Productos (`product.product`), Ventas.

**Descripción:** con base en las respuestas, el sistema debe identificar automáticamente
la variante correcta del producto (según servicio, m², zona) y asignar el precio correcto
del tabulador. Es el mayor reto técnico del proyecto.

**Ejemplo:** Cliente elige Fumigación Interior, responde 200 m², Zona B → el sistema asigna
la variante "Fumigación Interior 1-250 m²" con precio $600.

### Tabulador de precios (de la ficha)

| Input (servicio + m²)   | Variante asignada              | Zona A | Zona B | Zona C |
|-------------------------|--------------------------------|-------:|-------:|-------:|
| Fum. int. 200 m²        | Fumigación Interior 1-250 m²   |   690  |   600  |   540  |
| Fum. int. 400 m²        | Fumigación Interior 251-500 m² |   920  |   800  |   720  |
| Corte pasto 80 m²       | Corte de Pasto 51-100 m²       | 1,150  | 1,000  |   900  |
| > 1.000 m²              | Visita de Valoración Técnica   |   500  |   500  |   500  |

> **HALLAZGO CLAVE:** la Zona NO es un precio arbitrario por celda. Es un **porcentaje constante**:
> **Zona A = base × 1.15 (+15%)**, **Zona B = base (100%)**, **Zona C = base × 0.90 (−10%)**.
> Verificado en todas las filas (600→690/540, 800→920/720, 1000→1150/900).
> Excepción: "Visita de Valoración Técnica" es precio plano $500 en las 3 zonas.
> Esto permite modelar la zona como **pricelist con %** en vez de meterla como atributo de variante.

**Criterios de aceptación:**
- Al confirmar el formulario, la cita tiene asociada la variante correcta del producto.
- El precio mostrado en checkout corresponde al tabulador (servicio + rango m² + zona).
- La Orden de Venta generada refleja el producto correcto (no uno genérico).
- Si el rango requiere valoración técnica, el sistema agenda una visita de $500 en lugar del servicio directo.
  *(Implementado jun-2026: aviso en wizard + flujo `visar/valoracion`; cita tipo Valoración Técnica, no maestro.)*

**Decisión de diseño (resuelta):** se usan **variantes nativas de Odoo** para servicio×rango +
**tabla de mapeo** `visar.service.tier` + **pricelist por zona**. Ver `40-decisions.md`.
