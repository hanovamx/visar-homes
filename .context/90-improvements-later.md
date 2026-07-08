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

## I-05 — App de campo: aplicar `required` / condicional-OBLIGATORIO en la captura
- **Qué:** la app **ya hace visibilidad condicional** (campos companion "Otro", 08-jul bis) pero
  **no bloquea** el cierre por `required="1"` ni por reglas condicionales-obligatorias (dosis si hay
  plaguicida; foto si el área se marcó tratada).
- **Por qué:** los `required`/condicionales se codifican en la vista para Studio/reporte nativo, pero la
  app es server-rendered y hoy no valida al enviar. Riesgo: worksheets incompletas cerradas en campo.
- **Recomendación:** reutilizar la lectura de dominio simple ya usada para la visibilidad "Otro"
  (`_otro_conditional` / `evalCondFields`), marcar requeridos y **validar en el submit** del form de
  worksheet/cierre. Encaja con la deuda "Cierre sin validación" de `25-field-app.md`.
- **Prioridad:** Media (según exigencia del negocio).

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
