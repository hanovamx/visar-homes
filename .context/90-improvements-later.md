# Mejoras para después (backlog)

> Lista viva de mejoras/deuda técnica **no bloqueantes**. Ir agregando aquí cosas que surjan.
> Lo **crítico para el go-live** va en `80-deploy-prod.md`, no aquí.
> Formato sugerido por ítem: qué, por qué, opción/recomendación, prioridad.

## I-01 — Quitar self-healing de `visar_flow` y fallback de config params
- **Qué:** `_visar_ensure_entry_flow` escribe `visar_flow` en la BD durante un **GET público**;
  `_visar_resolve_entry_flow` añade un fallback por `ir.config_parameter`.
- **Por qué:** es un parche por la fragilidad del setup actual. Escribir en requests de lectura es
  *smell* (efectos colaterales, locking, perf).
- **Recomendación:** una vez el setup sea confiable en install (ver `80-deploy-prod.md` #1), eliminar
  ambos. Queda atado a ese fix.
- **Prioridad:** Media (depende del fix de deploy).

## I-02 — Aislar y testear `_visar_filter_slots_multi_service`
- **Qué:** re-camina y reconstruye el árbol nativo de slots (months/weeks/days/slots,
  `url_parameters`, `available_resources`). Es lo más sensible a upgrades de Odoo.
- **Por qué:** es el corazón frágil del multi-técnico; lo primero que se rompe en una actualización.
- **Recomendación:** (a) aislarlo + prueba unitaria; (b) evaluar mostrar la **unión** de recursos en la
  lista y validar la coincidencia **solo en el submit** (ya se re-eligen recursos ahí), para quitar el
  override más pesado.
- **Prioridad:** Media.

## I-03 — Simplificar `visar.combo.rule` (o su doble elegibilidad)
- **Qué:** modelo genérico (required/discount dimensions + factor + vista + menú + ACL + seeding +
  flag `combo_discount_eligible` en tier) para **una** regla del spec (interior+exterior+corte → 50%
  al corte).
- **Por qué:** más superficie de la necesaria si el combo es fijo. Además hay **doble mecanismo** de
  elegibilidad: `discount_dimension_ids` y `tier.combo_discount_eligible` hacen lo mismo.
- **Recomendación (criterio):** ¿Visar prevé más de un combo o ajustarlo sin redeploy?
  - **Sí** → conservar el modelo, pero **dejar un solo** mecanismo de elegibilidad.
  - **No (regla fija)** → colapsar a `ir.config_parameter visar.combo_corte_factor` + check por código
    de dimensión en `_visar_build_sale_lines`.
## I-04 — Hook de install para catálogo `visar_appointment`
- **Qué:** `_visar_migrate_legacy_catalog` y setup tipos entrada solo en migraciones; no hay `post_init_hook` en `visar_appointment`.
- **Por qué:** install limpio en prod queda sin grupos/dimensiones/combo. `visar_fsm` ya tiene hook; falta el equivalente citas.
- **Recomendación:** ver `80-deploy-prod.md` Parte 1.
- **Prioridad:** Alta (bloqueante go-live).

## I-05 — [RESUELTO 17-jul-2026] App de campo: aplicar `required` / condicional-OBLIGATORIO en la captura
- **Qué:** la hoja de trabajo ahora **valida obligatoriedad** (cliente + servidor) antes de guardar:
  `required="1"` del arch, companion "Otro" (obligatorio al mostrarse), reglas condicionales por
  disparador (`WORKSHEET_REQUIRED_IF`: foto de evidencia si infestación activa; foto+núm. bolsas si
  residuos embolsados) y **mínimo una** línea en las subfichas (`WORKSHEET_MIN_ONE`).
- **Cómo:** el descriptor lee `node.get('required')` (antes solo miraba el `required` de modelo, siempre
  falso en campos `x_`); `initWorksheetValidation` marca en rojo/bloquea/hace scroll; el servidor
  revalida en `POST …/worksheet` (`_worksheet_validation_errors`) y no escribe si falta algo.
- **Ver:** `25-field-app.md` → "🆕 Actualización — 17-jul-2026". No cambió campos de plantilla (el
  reparto ya estaba en el arch; solo se **aplica**). Cierra la deuda "Cierre sin validación".

## I-06 — [RESUELTO 08-jul-2026] App de campo: sembrar las plantillas en código + bump de versión
- **Qué era:** las plantillas "App v2" se construyeron por **scripts Python fuera del módulo**; vivían
  solo en la BD. Manifest desfasado.
- **Resuelto:** consolidado en `visar_field_app/hooks.py::seed_worksheet_templates` (idempotente, ambas
  plantillas al estado final), cableado como `post_init_hook` + `migrations/19.0.1.2.0/post-migrate.py`
  (upgrade) + ejecutable por shell. Manifest en **v19.0.1.2.0**. Detalle en `25-field-app.md`
  ("🆕 Actualización — 08-jul-2026 (bis)").
- **Residual:** ruta de install limpio verificada por composición, no en BD vacía → probar `-i` limpio
  antes de prod (tarea de go-live). Idempotente reescribe el arch (ediciones Studio en prod se pierden).

## I-07 — App de campo: pendientes del mapa / geocodificación (tanda 08-jul)
- **Qué:** cabos sueltos de la vista de mapa (Leaflet + OSM) y la geocodificación de direcciones de
  servicio (`res.partner._visar_geo_localize`, menú "Geolocalizar direcciones de clientes"). Detalle en
  `25-field-app.md` (sección 08-jul-2026).
- **Puntos:**
  1. **Ruta Mapbox de éxito sin probar en vivo** — falta un token real (`web_map.token_map_box`). Validar
     con una dirección conocida que Mapbox devuelve match a nivel calle. (Ya probado: fallback Mapbox-error → OSM.)
  2. **"Re-geolocalizar todo"** — la acción de menú solo procesa faltantes; re-geocodificar los ya guardados
     (subir de centroide OSM a calle Mapbox) requiere `force=True`, sin entrada de menú aún.
  3. **Tiles Mapbox (opcional)** — hoy los tiles son OSM (gratis, sin exponer token en la página pública).
     Cambiar a tiles Mapbox implica exponer un token público (restringible por URL) + posibles map-loads.
  4. **Capturar `state_id` en la reserva** — los contactos de entrega sin estado geocodifican peor.
- **Prioridad:** Baja-Media (según se adopte Mapbox y el volumen de servicios en el mapa).

## I-08 — App de campo (Req 2): tiempo de trayecto "en camino" (planeado, DIFERIDO)
- **Qué:** medir cuánto tarda el técnico en llegar — sello `visar_enroute_start` ("Voy en camino") →
  `visar_arrived_at` ("Confirmar llegada").
- **Por qué se difirió:** al refinar el Req 2 (etapas nativas + timesheet oculto) se decidió **dejarlo
  fuera por ahora**; el **único cronómetro visible** es el de "esperando cliente" (10 min, configurable).
- **Para qué revisitarlo:** insumo del futuro **aviso automático por WhatsApp** al cliente ("el técnico
  va en camino / ETA"). El módulo `whatsapp` **ya está instalado** en `visar_prod`.
- **Prioridad:** Baja.

## I-09 — App de campo: PINs duplicados / sin unicidad
- **Qué:** `hr.employee.visar_field_pin` no es único. En `visar_prod` el PIN **`123`** está en **dos**
  empleados (Pedro Martínez id 2 y Administrator id 1). `_visar_field_find_by_pin` devuelve uno
  arbitrario (devolvió Administrator), así que la **atribución** del cierre/timesheet/reagenda
  (`visar_field_closed_by_id`, empleado del timesheet) sale **no determinista**.
- **Por qué:** detectado al verificar el Req 2 (08-jul). Rompe comisiones/auditoría por técnico.
- **Recomendación:** depurar PINs duplicados en datos; añadir constraint de unicidad (y a futuro,
  hash + throttling, junto con la deuda "PIN en texto plano" de `25-field-app.md`).
- **Prioridad:** Media (afecta atribución real).

## I-10 — Fumigación interior+exterior = UNA sola línea (variante combinada) + conflicto fila "1-250"
- **Qué:** el wizard ahora **fusiona** fumigación interior + exterior en **una sola línea de venta** que
  apunta a la **variante combinada** (ambos ejes de tamaño: "Tamaño inmueble" attr 10 interior + attr 14
  exterior), en vez de dos líneas. El precio se **lee en vivo** de la regla de pricelist de esa variante
  (decisión: nada horneado en código/migración; el consultor configura las 27 variantes combinadas
  3×3×3). Código: `visar_appointment` **v19.0.2.3.0**, `visar_base` **v19.0.1.4.0**,
  `product.template._visar_combined_variant_for_tiers` / `_visar_axis_attribute`; la fusión ocurre solo
  en `_visar_build_sale_lines` (los `items` siguen siendo dos → pools, valoración y descuento combo del
  corte intactos). El label y el Q&A "metros" se colapsan a una entrada (línea única minimal).
- **Supuesto vigente (por eso este ítem):** asumimos que basta con **configurar los precios de las 27
  variantes combinadas** (suma interior+exterior por zona; A/C hoy están mal, ver `70-tabulador.md`).
  Eso es cierto para **21 de 27** variantes.
- **El cabo suelto — fila interior "1-250":** las 6 variantes `(1-250, {51-100,101-500}, {A,B,C})` hacen
  **doble función**: son a la vez la línea de **exterior-solo** (precio jardín standalone, p. ej. 800 en B)
  y la **combinada interior-1-250 + jardín** (suma, p. ej. 1400). Una variante = un precio → no se pueden
  cumplir ambos. Configurar precios **no lo resuelve** para esas 6. (Las filas 251-500 y 501-1000 no
  comparten variante y sí se arreglan solo con precio.)
- **Decisión de negocio que lo destraba:** ¿Visar mantiene la reserva **exterior-solo** (jardín sin
  interior; hoy el paso "cobertura" ofrece "Solo exterior / jardín")?
  - **Sí** → **reestructurar**: añadir un valor de interior **"Sin interior"** al attr 10, repuntar los
    tramos exteriores (`visar.service.tier` scope=exterior de la plantilla 30) a las variantes
    `(Sin interior, E)` (que llevan el precio jardín standalone), liberando las `(1-250, E)` como
    combinadas reales. **El código no cambia** (el resolutor arma la combinada por *valores* interior+
    exterior+zona). Luego configurar las 27.
  - **No** (se retira exterior-solo) → basta configurar las 27 (sin reestructura).
- **Estado (act. 3-ago-2026):** commiteado en `fa17506` (22-jul-2026) y **vivo en producción**
  desde el `-u` del 3-ago. Ojo: el código Python ya corría antes de ese `-u` —el número de
  versión no gatea Python, solo XML/campos/migraciones—, así que la línea combinada llevaba
  semanas activa aunque el módulo figurara "sin actualizar".
  El cliente confirma que **los 27 precios de variante combinada ya están configurados en
  producción** (la nota de precios A/C mal era de su entorno local).
- **Sin verificar:** el punto estructural de la fila **1-250** (6 variantes que hacen doble
  función exterior-solo / combinada) **no** se resuelve configurando precios, y no se ha
  comprobado contra los datos de producción. Antes de darlo por cerrado, confirmar con datos
  reales si sigue aplicando o si la restructura "Sin interior" ya se hizo.
- **Prioridad:** Media — ya no bloquea (la línea única está activa), pero el cabo de 1-250
  sigue abierto hasta verificarlo.

## I-11 — El descuento de combo de las líneas de servicio sigue expuesto a `_recompute_prices`
- **Qué:** `_visar_fill_wizard_cart_and_redirect` escribe `discount` en la línea después de
  `_cart_add`. `_recompute_prices()` pone `discount` a 0 y recalcula `price_unit`, y se dispara
  desde `res.partner.write()` al cambiar `country_id`/`vat`/`zip` — o sea, **al escribir la
  dirección en el checkout**.
- **Por qué importa:** el cliente vería el descuento de combo y podría acabar pagando sin él.
  No es teórico: es el mismo mecanismo que obligó a proteger las líneas de anticipo.
- **Recomendación:** ampliar el filtro de `sale.order._get_update_prices_lines()` (hoy solo
  excluye anticipos) para cubrir también las líneas con `calendar_booking_ids`. Ver el gotcha
  en `60-odoo19-conventions.md`.
- **Prioridad:** Alta — riesgo de cobrar de menos, silencioso.

## I-12 — Un solo producto de anticipo para todos los servicios
- **Qué:** `VISAR-ANTICIPO` es un único producto; la línea copia los impuestos del servicio que
  espeja para que el total cuadre.
- **Por qué importa:** hoy todos los servicios llevan 16%, así que no hay conflicto. Si algún día
  conviven servicios con tasas distintas, hará falta un producto de anticipo por grupo de
  impuestos (o el recálculo de `tax_ids` desde el producto podría desalinear el total).
- **Prioridad:** Baja — latente, sin impacto con el catálogo actual.

## I-13 — Póliza Bimestral a precio de paridad
- **Qué:** el plan bimestral se ofrece al mismo precio que la compra única (descuento 0%),
  vendido por conveniencia. El mensual lleva −5%.
- **Por qué importa:** pide compromiso sin ahorro; junto al mensual con su badge de ahorro puede
  leerse como una opción sin sentido.
- **Recomendación:** decidir con Visar si lleva descuento propio o se retira del paso. Cambiarlo
  es configuración (3 reglas de lista), no código; retirarlo es el parámetro
  `visar.poliza_plan_ids`.
- **Prioridad:** Media — decisión de negocio, no técnica.

## I-14 — Archivos del repo con dueño `root`
- **Qué:** ~30 archivos de `visar_subscription` y `visar_field_app`, más objetos dentro de
  `.git/objects`, pertenecen a `root` por algún `sudo git` / copia con sudo del pasado.
- **Por qué importa:** los de `.git` **impedían `fetch` y `commit`** ("insufficient permission for
  adding an object"); se corrigieron el 3-ago. Los del árbol de trabajo bloquean editar esos
  archivos y volverán a aparecer si se repite la operación con sudo.
- **Recomendación:** `sudo chown -R josegonzalez:josegonzalez /opt/custom` y no volver a correr
  git ni copiar archivos como root en el repo.
- **Prioridad:** Baja, pero reaparece.
