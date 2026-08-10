# Estado y roadmap

> Última actualización: **3-ago-2026** — pólizas: cobro adelantado como línea real + paso
> de póliza en el wizard, **desplegado en producción**
> (**visar_appointment v19.0.2.4.1**, **visar_base v19.0.1.4.0**,
> **visar_subscription v19.0.1.3.0**, **visar_fsm v19.0.1.0.4**).
> Entrada anterior: 26-jun-2026 — split en 3 módulos + D-06 + D-07 parcial + calificación wizard.
> Productos/variantes **no se crean en XML** — se configuran/enlazan en backend + migraciones legacy.

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
      Pendiente: verificar en **teléfono real** y **extremo a extremo con Meta** (requiere plantilla
      aprobada con cabecera DOCUMENT: fuera de la ventana de 24 h el mensaje libre no se entrega).
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
- [ ] Confirmar pago → tareas FSM agrupadas (1 Fumigación + 1 Corte para combo triple).

### Datos / operación

- [ ] Renombrar dimensión "Corte y poda" → **"Mantenimiento de áreas verdes"** (reunión 22-jun); forzar valoración si aplica.
- [ ] Configurar add-ons en fumigación (estaciones ×3 obligatorias cuando roedores=si vía producto roedores).
- [ ] Decidir migración única: attrs A/B/C vs solo pricelist por zona.

## Entorno local activo

- **Odoo:** `/Users/luisgarza27/Documents/HANOVA/odoo_19_visar`
- **Repo módulos:** `/Users/luisgarza27/Documents/HANOVA/VISAR/repo`
- **Git remoto:** `https://github.com/luisgarza-g/visar-luisg.git` (rama `main`)
- **Config:** `odoo.visar.conf`
- **BD:** `visar_local`, puerto **8071**, credenciales `admin / admin`
- **Arranque:**
  ```bash
  cd /Users/luisgarza27/Documents/HANOVA/odoo_19_visar
  PYTHONPATH=/Users/luisgarza27/Documents/HANOVA/odoo_19_visar:/Users/luisgarza27/Documents/HANOVA/VISAR/repo \
    .venv/bin/python setup/odoo -c odoo.visar.conf
  ```
- **Actualizar módulos:**
  ```bash
  PYTHONPATH=/Users/luisgarza27/Documents/HANOVA/odoo_19_visar:/Users/luisgarza27/Documents/HANOVA/VISAR/repo \
    .venv/bin/python setup/odoo -c odoo.visar.conf \
    -u visar_base,visar_fsm,visar_appointment --stop-after-init
  ```
- Tras `-u`, **reiniciar el servidor** (workers cachean registro de modelos/plantillas).

## Cómo probar (checklist actualizado)

1. **`/appointment`** — solo **Valoración Técnica** y **Cita de Servicios**.
2. **Valoración directa:** Book now → zona → horario → checkout ($500).
3. **Wizard normal:** servicios → rangos → **calificación** → zona → maestro → horario → checkout multi-línea (+ add-ons si aplica).
4. **Wizard con roedores:** calificación con roedores=Sí → verificar línea control roedores + estaciones en total.
5. **Wizard → valoración:** rango `is_valuation` → aviso → valoración → checkout ($500).
6. **FSM:** tras pago, revisar tareas en proyecto FSM correspondiente (backend / app técnico).
7. **Legacy D-03:** URL directa tipo individual → prequalify Zona + m².

## Convenciones de trabajo

- No instalar/actualizar contra la BD del usuario sin avisar.
- Validar Python (`py_compile`) y XML antes de dar por hecho un cambio.
- Respetar `60-odoo19-conventions.md`.
- **No crear productos en XML** — configurar en backend.
- Actualizar los **tres módulos** cuando cambie lógica compartida (`visar_base` primero en dependencias).
