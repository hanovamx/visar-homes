# Estado y roadmap

> Última actualización: **20-ago-2026** — **agendado completo por WhatsApp en producción**.
> Versiones al día de hoy: **visar_base 19.0.1.6.0**, **visar_fsm 19.0.1.1.0**,
> **visar_appointment 19.0.2.7.0**, **visar_field_app 19.0.1.26.0**,
> **visar_subscription 19.0.1.4.0**, **visar_crm 19.0.1.3.0**,
> **visar_whatsapp_agent 19.0.1.4.0**.
> Entradas anteriores: 3-ago-2026 (pólizas en producción) · 26-jun-2026 (split en módulos + D-06
> + D-07 parcial + calificación wizard).
> Productos/variantes **no se crean en XML** — se configuran/enlazan en backend + migraciones legacy.

## Hecho — Agendado completo por WhatsApp (19/20-ago-2026) — **EN PRODUCCIÓN**

Diseño, estado detallado y las 15 decisiones en
[`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md). El lado runtime está en el
`.context/` de `visar_fastapi` (`85-motor-de-flujos-agendado.md`).

**Hoy un cliente puede reservar escribiendo por WhatsApp**, de punta a punta y sin salir del chat
salvo para pagar. 17 de los últimos 22 commits del repo son esto.

- [x] **Apartado de horario** (`visar.slot.hold`, 10 min configurables) descontado en
      `_get_resources_remaining_capacity` — protege **los dos canales** con un solo cambio.
- [x] **El cuestionario bajó al modelo** (`appointment_wizard_flow.py`, ~1,405 líneas): podar,
      secuenciar, normalizar y ofrecer. El controlador web pasó de 1,961 a 1,664 líneas y
      **delega**. Su comportamiento no cambió.
- [x] **`agent_booking_step`** — el cuestionario entero por RPC, sin escribir nada.
- [x] Días y horarios (`agent_available_days`, `agent_day_slots`) con **hora local**, no UTC.
- [x] Reserva, pedido y **liga de pago** (`agent_prepare_booking`, `payment.link.wizard`).
- [x] **La liga vive y muere con el apartado** — al caducar, deja de cobrar.
- [x] **Corregir UN paso** desde la revisión, re-preguntando solo lo que dependía de él.
- [x] Retomar una conversación estacionada **sin volver a pedir la dirección**.
- [x] Multi-selección contestando por escrito; menú de "¿qué quieres cambiar?".
- [x] Avisos salientes de reserva por buzón (`visar.wa.booking.message`) sobre el mixin
      compartido `visar.wa.outbox.mixin` de `visar_base`.
- [x] **Hand-off humano** con lead + nota en el chatter + actividad asignada.
- [x] Verificado en servidor en **cuatro rondas** (ver §10.2–§10.4 del doc 33) y corregido con
      tres fallos salidos del **primer uso real como cliente** (§10.6).

### Lo que NO cierra todavía

- [ ] ⛔ **La rama de valoración no llega a horarios** (§10.7 / I-17). `valuation` es terminal:
      nunca se pregunta la dirección → sin zona → sin técnicos → cero días → el runtime escala a
      un humano. **Los clientes con termitas, chinches o "no sé qué es" no pueden agendar por
      WhatsApp.** Contradice la decisión 3 del §12.
- [ ] ⛔ **Factibilidad de traslado sin construir** (§5, decisiones 7/14). Ni una línea. Hoy se
      ofrece cualquier horario con capacidad sin mirar si el técnico llega. Sostenible **solo**
      porque hay un técnico usable con mediana de 2.5 paradas/día.
- [ ] **CP temprano** (§4.0): decidido, sin construir. Es la salida probable para I-17.
- [ ] **Equipo CRM de WhatsApp sin líder ni miembros** → el hand-off escala **a nadie**. Es
      dato, no código, y es lo que separa "prometemos que le contactan" de que le contacten.
- [ ] **Plantillas de Meta sin aprobar** → los avisos salientes están siempre fuera de la ventana
      de 24 h: se encolan, dan 502 y caducan.
- [ ] **Stripe**: el pago sigue simulado (proveedor Demo).
- [ ] **I-15** — cuatro planes de póliza se llaman igual ("Póliza Mensual" ×4 + "Monthly"). Por
      RPC da igual; en WhatsApp serían **cuatro botones idénticos en el paso de mayor valor del
      flujo**. Es dato, no código.
- [ ] **I-16** — nadie se entera de que un servicio se cayó: `visar-fastapi` estuvo ~2 días
      muerto sin que nadie lo notara.

## Hecho — Pólizas: cobro adelantado + paso en el wizard (ago-2026)

Detalle completo en [`35-polizas.md`](./35-polizas.md).

- [x] El cobro de 2 meses por adelantado es una **línea real del pedido**, no un
      multiplicador al facturar. Antes el carrito cobraba 1 mes, la factura salía por 2 y,
      al no quedar pagada, **no se generaba ninguna visita**.
- [x] 6 listas (zona × plan) con 2 reglas globales sustituyen a las 78 por variante.
      Los precios siguen viviendo solo en las listas de zona.
- [x] Paso de póliza en el wizard tras *¿Deseas agregar algo más?*; se retira la
      contratación desde `/shop/...` (productos 30 y 31 siguen sin publicar).
- [x] La **primera visita hereda fecha y técnico** de la cita reservada; las demás nacen
      sin agendar.
- [x] El precio anunciado es **solo el servicio recurrente**; los add-ons son cargo único.
- [x] Migración: 14 pedidos con anticipo, 41 omitidos por tener factura posteada; planes
      anuales bajados a 1 periodo (cobraban dos años de entrada).
- [x] 15 tests en verde contra copia de la BD real.
- [x] Desplegado en `visar-db` el 3-ago-2026 (backup previo en `/var/lib/odoo/backups/`).

### Bugs de producción encontrados de paso

1. **Carrito de compra única se confirmaba como suscripción** —
   `_visar_clear_previous_booking_lines` no limpiaba `order.plan_id`. **Corregido.**
2. **S00087/S00088 con dos facturas de 2 meses** y **S00084/S00085 con una de 1 mes** —
   causa eliminada, pero las facturas ya están posteadas: **decisión de finanzas pendiente**.

### Pendiente de esta tanda

- [ ] Repuntar el botón *Contratar póliza mensual* de la portada (editor web, no código).
- [ ] Definir si la Póliza Bimestral se queda a precio de paridad o lleva descuento.
- [ ] Cerrar los 4 pedidos con factura mal emitida con finanzas.

## Hecho — D-03 (inversión de flujo + filtrado de técnicos)

- [x] Modelos `visar.zone`, `visar.service.tier` (ahora en `visar_base`).
- [x] Extensiones `product.template`, `appointment.type`, `appointment.resource`, `calendar.event`.
- [x] Controlador: intercepción del flujo, página `prequalify`, cálculo de elegibles, persistencia de respuestas.
- [x] Vistas backend (producto, tabulador global, zona, recurso, cita) y frontend.
- [x] Seguridad (`ir.model.access.csv` en `visar_base`).
- [x] Validado end-to-end en BD `visar_local` (flujo legacy 1 servicio).

### Bugs encontrados y corregidos durante la instalación

1. **`__manifest__.py` — dependencias faltantes:** `product` y `hr`.
2. **`is_auto_assign = False`** en tipos de cita → pantalla intermedia no deseada. Fix: `is_auto_assign=True`, `assignment_method='auto'`.
3. **`TypeError: extra_calendar_event_params`** — firma del override alineada al core Odoo 19.

## Hecho — D-04 + D-05 (wizard multi-servicio + precio)

### Modelos / datos (`visar_base` + `visar_appointment`)

- [x] `visar.service.group` + `visar.service.dimension` — wizard configurable desde backend.
- [x] `visar.combo.rule` — reglas de combo configurables.
- [x] `product.template`: `visar_is_service`, `visar_is_valuation`, `visar_dimension_id`, campos legacy.
- [x] `visar.service.tier`: `name`, `is_valuation`, `is_free`, `combo_discount_eligible`.
- [x] `visar.zone`: `pricelist_id`.
- [x] `calendar.event`: `visar_booking_items` (JSON); conservado `visar_m2` (legacy D-03).
- [x] `appointment.type`: `visar_is_master`, **`visar_flow`**, helpers maestro/valoración.
- [x] Catálogo legacy enlazado por `migrations/19.0.2.0.7/post-migrate.py`.
- [x] Pricelists por zona + ítem fijo $500 Valoración.
- [x] Dependencia `website_appointment_sale` para SO multi-línea en checkout.

### Wizard + controlador (`visar_appointment`)

- [x] `views/wizard_templates.xml` — servicios / substeps / dimensiones / **calificación** / zona + aviso valoración.
- [x] Rutas wizard completas incl. **`…/wizard/calificacion`**.
- [x] Bifurcación valoración desde wizard (aviso → flujo valoración directa).
- [x] Sesión `visar_booking` con `mode=wizard|valuation`, `items`, `service_pools`, selecciones calificación.
- [x] Resolución multi-variante, pools, agenda multi-técnico.
- [x] Punto de entrada `/appointment` con dos tipos (`visar_flow` valuation/wizard).

### Cita multi-línea + pago

- [x] `_redirect_to_payment` — N `_cart_add` (wizard) o una línea $500 (valoración).
- [x] Valoración → **una** línea $500 (dedupe).
- [x] Pricelist de zona en SO.
- [x] Sidebar precio multi-línea (`visar_quote`).

## Hecho — D-06 (add-ons obligatorios) — `visar_base`

- [x] Modelo `visar.product.optional.line` (`is_mandatory`, `quantity`).
- [x] Sincronización Opción A con `optional_product_ids` (onchange + reconcile en create/write).
- [x] Vista tabla en producto (invisible si M2m vacío).
- [x] Inyección en `_visar_build_sale_lines` (checkout web) con **suma** de cantidades si mismo add-on en varios servicios.
- [x] Auto-inyección en backend (`sale.order` / `sale.order.line`) para pedidos manuales.
- [x] Flujo valoración **no** agrega add-ons.

## Hecho — Calificación wizard + roedores — `visar_appointment`

- [x] Paso wizard **calificación**: plaga/preventivo, roedores, tipo de plaga (opcional).
- [x] Producto `visar_is_roedores` + parámetro `visar.roedores_product_tmpl_id`.
- [x] Si `roedores=si` → línea producto roedores + add-ons obligatorios del producto roedores (p. ej. 3 estaciones).
- [x] Preguntas nativas en `data/visar_questions_data.xml`.
- [x] Respuestas inyectadas en Questions & Answers vía `_visar_enrich_answer_inputs` (zona, m²/rangos, calificación).
- [x] Preguntas **desvinculadas** del formulario nativo de cita (migración 19.0.2.0.12).

## Parcial — D-07 (FSM) — `visar_fsm` (v19.0.1.0.1)

- [x] `post_init_hook` — proyectos FSM + `service_tracking` + `project_id` en productos.
- [x] Override `_timesheet_service_generation` — **una tarea por proyecto**.
- [x] Add-ons asignados a tarea del servicio que los declara (`task_id` en línea SO).
- [x] Enriquecimiento: técnico (`appointment.resource` → `user_ids`) y fechas desde `calendar.event`.
- [x] `calendar.event.visar_fsm_task_ids` (computed).
- [x] **UI tarea FSM:** ocultar `sale_line_id` nativo; mostrar `visar_sale_order_id` (orden completa de la cita).
- [x] **Worksheet / checklist / fotos / firma** — resuelto vía `visar_field_app` (app web sobre
      worksheet NATIVA, no `worksheet.template` propias). **Ampliado 07-jul-2026:** renderiza o2m
      (tarjetas), m2m (casillas), imágenes por línea, widgets, pestañas invisibles, ayuda ⓘ, borrado
      de fotos; plantilla "Fumigación interior o exterior (App v2)".
      **08-jul-2026 (bis):** m2m EN tarjetas (plaga multiselección), 2 columnas por grupo anidado
      (plaguicida nombre+dosis), campo condicional "Otro" (selección y m2m), polish de espaciado/etiquetas;
      2ª plantilla "Mantenimiento de áreas verdes (App v2)"; **sembrador versionado** `hooks.py`
      (`post_init_hook` + migración 19.0.1.2.0). Ver `25-field-app.md`.
      **16-17-jul-2026 (v19.0.1.5.0→1.10.0):** flujo ordenado (hoja tras "Comenzar", firma tras guardar
      la hoja → etapa **"Pendiente de firma"**); **validación de obligatoriedad** requerido/condicional/
      min-uno (cliente rojo+bloqueo y servidor) — **cierra I-05**; traza de botones (Llamar/WhatsApp/
      Maps) al chatter; lista **Hoy/Todos** + **ruta arrastrable**; icono de la app; PDF con **"Tiempo
      en sitio"** y **fotos** (fix de encoding + JPEG). Ver `25-field-app.md`.
      **10-ago-2026 (v19.0.1.16.0):** Fumigación reestructurada — **áreas obligatorias**
      (Cocina/Baño/Área de basura, no borrables, con dispensa "cliente no permitió") y **taxonomía de
      plagas de 2 niveles** (categoría → especies, indentadas) tras el gate "¿presencia activa?";
      el sembrador ahora **converge catálogos** (`_sync_selection` / `_sync_tag_records`) y la
      obligatoriedad respeta la visibilidad **también en servidor**. Ver `25-field-app.md`.
      **10-ago-2026 (v19.0.1.17.0):** **cámara obligatoria** — toda foto se toma en vivo con
      `getUserMedia` (el `<input capture>` era solo una pista que iOS ignora); el input oculto se
      rellena por `DataTransfer` así que el servidor no cambió. Multi-foto ya funcionaba.
      **Reporte firmado por WhatsApp** desde la app: Odoo renderiza el PDF y el runtime
      (`visar_fastapi`, `POST /internal/send-report` por loopback) lo entrega con pywa — primer
      camino Odoo → runtime. Ver `25-field-app.md`.
      **10-ago-2026 (v19.0.1.18.0):** los avisos al cliente (**en camino / llegó / reagendar**)
      dejan de ser simulación: se encolan en `visar.wa.message` y los manda un cron (disparado al
      encolar, con **caducidad por tipo** — un aviso viejo no se manda tarde) contra
      `/internal/send-notification`. Vista de oficina "Avisos por WhatsApp" filtrada por
      *No entregados*. Ver `25-field-app.md`.
- [x] **Reporte por WhatsApp funcionando en el servidor** (modo libre, dentro de la ventana de 24 h).
- [ ] **Plantillas de Meta aprobadas** — prerrequisito de negocio, bloquea el uso real:
      `WA_REPORT_TEMPLATE` (cabecera DOCUMENT) y las tres de aviso (`WA_TEMPLATE_ENROUTE`,
      `_ARRIVED`, `_RESCHEDULE`). Los **avisos no tienen camino libre viable**: van siempre a un
      cliente que agendó por la web y nunca escribió, así que están siempre fuera de la ventana —
      hasta la aprobación se encolan, dan 502 y caducan (visible en el buzón y en el chatter).
- [ ] Verificar la **cámara en teléfono real** (iOS no debe ofrecer la fototeca).
- [ ] Reporte dual interno vs cliente.
- [ ] Cross-link explícito cita ↔ tarea en agenda (hoy vía SO compartida + `visar_sale_order_id` en tarea).
- [ ] E2E: confirmar pago → verificar N tareas FSM correctas en UI técnico.

## Hecho — Fixes E2E web Odoo 19 (jun-2026)

| # | Síntoma | Causa | Fix |
|---|---------|-------|-----|
| 1 | 500 al Book now valoración | `website.pricelist_id` no existe en Odoo 19 | `website._get_and_cache_current_pricelist()` |
| 2 | 500 sidebar horarios | QWeb no soporta `getattr` | Plantilla usa solo `visar_quote`; inyección vía `request.render` |
| 3 | 500 wizard paso servicios | `post.getlist` en dict plano | `_visar_form_id_list()` → `request.httprequest.form.getlist()` |
| 4 | Cita maestro con SO valoración | Wizard `is_valuation` seguía al maestro | Bifurcación a flujo valoración + aviso |

## Parcial / pendiente

### Go-live / despliegue (CRÍTICO — ver `80-deploy-prod.md`)

- [x] `visar_fsm` tiene `post_init_hook` (proyectos FSM) — corre en `-i`.
- [ ] Catálogo legacy + tipos entrada `visar_appointment` **siguen solo en migraciones** (no en hook).
- [ ] Mover setup estructural de citas a `post_init_hook` idempotente compartido con migración.
- [ ] Configurar productos/catálogo/zonas/combo/add-ons en backend de prod.
- [ ] Probar **`-i` en BD vacía** con los tres módulos.

### E2E web (manual)

- [ ] Wizard completo: servicios → rangos → **calificación** → zona → horario → checkout (con/sin roedores).
- [ ] Verificar add-ons obligatorios en sidebar y checkout (cantidades sumadas).
- [ ] Wizard → tramo `is_valuation` → aviso → valoración → checkout.
- [ ] Combinaciones combo (interior + exterior + corte) y totales vs tabulador.
- [ ] Confirmar pago → tareas FSM agrupadas. 🆕 **13-ago-2026:** el combo fumigación +
      áreas verdes cae en **UNA** tarea de "Servicios combinados" (hoja, firma y PDF
      únicos); un combo triple con interior + exterior + corte sigue siendo 1 tarea.
      Cubierto por `visar_fsm/tests/test_fsm_grouping.py`. **Pendiente: pólizas** (fase 2,
      su generación de visitas es por línea y por periodo, ver `40-decisions.md`).

### Datos / operación

- [ ] Renombrar dimensión "Corte y poda" → **"Mantenimiento de áreas verdes"** (reunión 22-jun); forzar valoración si aplica.
- [ ] Configurar add-ons en fumigación (estaciones ×3 obligatorias cuando roedores=si vía producto roedores).
- [ ] Decidir migración única: attrs A/B/C vs solo pricelist por zona.

## Entorno local activo

- **Odoo:** `/Users/luisgarza27/Documents/HANOVA/odoo_19_visar`
- **Repo módulos:** `/Users/luisgarza27/Documents/HANOVA/VISAR/repo`
> ⚠️ **Esta sección describía la máquina de otra persona** (rutas `/Users/luisgarza27/…`, BD
> `visar_local`). Se deja genérica: sustituye `<RAÍZ>` por la raíz de tu checkout.

- **Git remoto:** `https://github.com/luisgarza-g/visar-luisg.git` (rama `main`)
- **Config:** `odoo.visar.conf`
- **BD local:** puerto **8071**, credenciales `admin / admin`
- **Arranque:**
  ```bash
  cd <RAÍZ>/odoo_19_visar
  PYTHONPATH=<RAÍZ>/odoo_19_visar:<RAÍZ>/odoo_19_visar/visar-homes \
    .venv/bin/python setup/odoo -c odoo.visar.conf
  ```
- **Actualizar módulos:**
  ```bash
  PYTHONPATH=<RAÍZ>/odoo_19_visar:<RAÍZ>/odoo_19_visar/visar-homes \
    .venv/bin/python setup/odoo -c odoo.visar.conf \
    -u visar_base,visar_fsm,visar_appointment --stop-after-init
  ```
- Tras `-u`, **reiniciar el servidor** (workers cachean registro de modelos/plantillas).

> **Nombres de base, para que no se repita el error.** En el servidor la base es **`visar-db`**.
> **`visar_prod` NO EXISTE** — no aparece en `psql -l`, y `/etc/odoo/odoo.conf` trae
> `db_name = visar-db`. Existen `visar-db`, `visar-db-2`, `visar-db-pres`,
> `visar-db-rehearsal` y `visar-test`. Varios documentos de esta carpeta usan el nombre
> equivocado. **Los módulos custom viven en `/opt/custom`**, no en una ruta `visar-homes/`.
>
> ⚠️ **Nunca correr tests contra `visar-db`.** Siempre sobre una copia desechable.

## Cómo probar (checklist actualizado)

1. **`/appointment`** — solo **Valoración Técnica** y **Cita de Servicios**.
2. **Valoración directa:** Book now → zona → horario → checkout ($500).
3. **Wizard normal:** servicios → rangos → **calificación** → zona → maestro → horario → checkout multi-línea (+ add-ons si aplica).
4. **Wizard con roedores:** calificación con roedores=Sí → verificar línea control roedores + estaciones en total.
5. **Wizard → valoración:** rango `is_valuation` → aviso → valoración → checkout ($500).
6. **FSM:** tras pago, revisar tareas en proyecto FSM correspondiente (backend / app técnico).
7. **Legacy D-03:** URL directa tipo individual → prequalify Zona + m².
8. **Agendado por WhatsApp (RPC):** desde `odoo shell`, recorrer `agent_booking_step` de
   principio a fin pasando en cada llamada el `booking` que devolvió la anterior. Confirmar que
   `step` avanza y **nunca repite** un paso ya contestado, que `options` no viene vacío donde
   debería haber opciones, y que con una respuesta inválida `error` viene lleno y `step` **no se
   mueve**. Probarlo **por JSON-RPC de verdad**, no solo en shell: un recordset colado en
   `options` revienta ahí y no en el shell.
9. **Paridad web ↔ agente:** el mismo cuestionario por los dos caminos, con las mismas
   respuestas, tiene que dar el **mismo `selections`**. Si divergen, eso es exactamente lo que
   `c115c21` venía a impedir.

## Convenciones de trabajo

- No instalar/actualizar contra la BD del usuario sin avisar.
- Validar Python (`py_compile`) y XML antes de dar por hecho un cambio.
- Respetar `60-odoo19-conventions.md`.
- **No crear productos en XML** — configurar en backend.
- Actualizar los **tres módulos** cuando cambie lógica compartida (`visar_base` primero en dependencias).
