# Referencia del core de Odoo 19 (anclas que tocamos)

Rutas relativas a: `/Users/luisgarza27/Documents/HANOVA/odoo_19_visar/odoo/addons/`
Versión: 19.0 Enterprise (`odoo/release.py` → `version_info = (19, 0, 0, FINAL, 0)`).

Módulos custom Visar: `visar_base`, `visar_fsm`, `visar_appointment` en `/Users/luisgarza27/Documents/HANOVA/VISAR/repo/`
(repositorio Git: `https://github.com/luisgarza-g/visar-luisg.git`, rama `main`).

> Los números de línea pueden desplazarse en upgrades; usa los nombres de método/campo
> como referencia primaria y reverifica antes de confiar en una línea exacta.

## Flujo de citas (D-03)

| Qué | Dónde |
|---|---|
| Orden de pantallas nativo (lista → horario → preguntas → submit) | `appointment/controllers/appointment.py:83, :185, :510, :652` |
| `appointment_type_page(appointment_type_id, state, staff_user_id, resource_selected_id, **kwargs)` | `appointment/controllers/appointment.py:185` |
| `filter_resource_ids` se parsea como JSON: `json.loads(unquote_plus(...))` | `appointment/controllers/appointment.py:330` |
| `_get_slots_from_filter()` → llama `_get_appointment_slots(filter_resources=...)` | `appointment/controllers/appointment.py:222` |
| `appointment_form_submit(...)` (POST submit) | `appointment/controllers/appointment.py:654` |
| `_handle_appointment_form_submission(..., extra_calendar_event_params=None)` — crea el `calendar.event` | `appointment/controllers/appointment.py:872` (punto de inyección) |
| Cálculo de slots `_get_appointment_slots(timezone, filter_users, filter_resources, asked_capacity)` | `appointment/models/appointment_type.py:833` |
| Disponibilidad por recurso `_slots_fill_resources_availability()` | `appointment/models/appointment_type.py:928` |

## Preguntas nativas (no usadas para Zona/m², ver decisiones)

| Qué | Dónde |
|---|---|
| `appointment.question.question_type` (char/text/phone/select/radio/checkbox — **sin numérico**) | `appointment/models/appointment_question.py:24-30` |
| Respuestas del cliente `appointment.answer.input` | `appointment/models/appointment_answer.py:17` |
| Enlace a la cita `calendar.event.appointment_answer_input_ids` | `appointment/models/calendar_event.py:83` |

## Recursos / técnicos

| Qué | Dónde |
|---|---|
| `appointment.type.schedule_based_on` ∈ {users, resources}; `resource_ids` | `appointment/models/appointment_type.py:171, :191` |
| `appointment.resource` (hereda `resource.mixin`: `resource_id`, `resource_calendar_id`, `capacity`) | `appointment/models/appointment_resource.py:9` |
| `hr.employee.resource_id` → `resource.resource` | `hr/models/hr_employee.py:82` |
| Grupos de seguridad: `appointment.group_appointment_manager` / `_user` | `appointment/security/res_groups_data.xml:9, :16` |

## Producto, variantes y precios (D-04)

| Qué | Dónde |
|---|---|
| Generación de variantes (`itertools.product`) y límite (1000) | `product/models/product_template.py:710, :757, :768` |
| `product.product.price_extra` (computado, **aditivo**) y `lst_price = list_price + price_extra` | `product/models/product_product.py:309-325` |
| `product.pricelist.item.applied_on` ∈ {3_global, 2_product_category, 1_product, **0_product_variant**} | `product/models/product_pricelist_item.py:51` |
| Regla por variante `_is_applicable_for()` | `product/models/product_pricelist_item.py:541` |
| `compute_price` ∈ {fixed, percentage, formula} | `product/models/product_pricelist_item.py:557` |

## Producto ↔ cita ↔ pago (D-04 / prepay)

| Qué | Dónde |
|---|---|
| `appointment.type.product_id` (UNA variante por tipo de cita) | `appointment_account_payment/models/appointment_type.py:19` |
| Activador del cobro previo `has_payment_step` | `appointment_account_payment/models/appointment_type.py:18` |
| Crea `calendar.booking` con `product_id` | `appointment_account_payment/controllers/appointment.py:62, :88` |
| Override que agrega la cita al carrito eCommerce (`_cart_add`) | `website_appointment_sale/controllers/appointment.py:13-52` (`_cart_add` en `:25`) |
| Preparación de la línea de la SO | `website_appointment_sale/models/sale_order.py:68` |
| Revalida disponibilidad antes de pagar | `website_appointment_sale/models/sale_order.py:43` |

## Vistas heredadas

| Vista base | id |
|---|---|
| Form de producto | `product.product_template_form_view` |
| Form de recurso | `appointment.appointment_resource_view_form` |
| Form de evento de calendario | `calendar.view_calendar_event_form` |
| Menú de configuración de citas (padre de nuestros menús) | `appointment.appointment_menu_config` |
