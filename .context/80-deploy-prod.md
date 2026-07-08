# Despliegue a producción — pendiente crítico antes del go-live

> Aplicar **cuando se vaya a subir el módulo a prod**. Hoy el stack se ha probado con `-u`
> sobre `visar_local`; una **instalación limpia (`-i`)** puede quedar sin parte del setup.
> Origen: revisión de código jun-2026 + split en 3 módulos.

## El problema (install vs upgrade)

Odoo corre cosas distintas según la acción:

| Acción | Qué corre | Qué **no** corre |
|---|---|---|
| `-i` (instalación limpia) | `data/` del manifest + **`post_init_hook`** | `migrations/*` |
| `-u` (upgrade, si sube versión) | **`migrations/*`** (pre/post-migrate) | `post_init_hook` |

### Estado actual por módulo

| Módulo | Setup en `-i` | Setup solo en `-u` |
|---|---|---|
| **`visar_fsm`** | ✅ `post_init_hook` → proyectos FSM + `project_id` en productos | Re-ejecutado en migración 19.0.2.0.14; **v19.0.1.0.1** añade UI tarea (`visar_sale_order_id`) |
| **`visar_base`** | Solo `data/` (placeholder vacío) | — |
| **`visar_appointment`** | Solo `data/visar_questions_data.xml` | ❌ `_visar_migrate_legacy_catalog` (grupos, dimensiones, combo) en 19.0.2.0.7 |

En prod con **`-i`**, el catálogo legacy (grupos/dimensiones/combo) y la configuración de tipos de
entrada (`visar_flow`, maestro Servicios Visar) **no se crean automáticamente** salvo que ya existan
en BD o se configuren a mano. Solo "funciona" en `visar_local` porque se acumularon datos de dev/upgrades.

## Matiz importante: el setup deriva de campos ya poblados

El script `_visar_migrate_legacy_catalog` **no crea el catálogo de la nada**: recorre `product.template` con
`visar_is_service = True` y lee `visar_service_group` / `visar_dimension_kind`. En install limpia esos
campos nacen vacíos hasta que se etiqueten los productos reales de prod.

## Fix — dos partes

### Parte 1: mecánica (correr el setup también en install)

1. Crear `hooks.py` en **`visar_appointment`** (o mover setup compartido a `visar_base`) con función
   idempotente que llame a `_visar_migrate_legacy_catalog` + setup tipos entrada (`visar_flow`, maestro).
2. `'post_init_hook'` en manifest de `visar_appointment`.
3. **Mantener** migraciones, pero que **llamen** a la misma función (DRY). `visar_fsm` ya sigue este patrón.
4. **Limpieza opcional:** eliminar `_visar_ensure_entry_flow` y fallback config params del controlador
   (ver `90-improvements-later.md` I-01).

> **`visar_fsm` ya resuelve la Parte 1 para proyectos FSM.** Falta el equivalente para citas/catálogo.

### Parte 2: datos de prod (cómo se etiquetan los productos) — **Opción A (recomendada)**

Como el set de servicios es **conocido y chico**, configurarlo en backend:

- En cada `product.template` de servicio: **Servicio agendable**, grupo/dimensión, tramos, tipo de cita.
- Configurar **add-ons** en `optional_product_ids` + tabla Obligatorio/Cantidad.
- Configurar **zonas**, pricelists A/B/C, **regla combo**, producto valoración y producto roedores.
- Verificar **proyectos FSM** (`project_id` en producto) — el hook los asigna por dimensión si están vacíos.

## Checklist de go-live

- [x] `visar_fsm` — hook idempotente proyectos FSM.
- [ ] `visar_appointment` — hook idempotente catálogo + tipos entrada.
- [ ] (Opcional) quitar self-healing de `visar_flow` en controlador.
- [ ] Configurar productos/catálogo/zonas/combo/add-ons en backend de prod (Opción A).
- [ ] **Probar `-i` en BD vacía** instalando `visar_base`, `visar_fsm`, `visar_appointment` en orden.
- [ ] Verificar `/appointment`: **Valoración Técnica** y **Cita de Servicios**.
- [ ] E2E web + FSM (ver checklist en `50-status-roadmap.md`).

## Comando install limpio (referencia)

```bash
PYTHONPATH=/Users/luisgarza27/Documents/HANOVA/odoo_19_visar:/Users/luisgarza27/Documents/HANOVA/VISAR/repo \
  .venv/bin/python setup/odoo -c odoo.visar.conf \
  -i visar_base,visar_fsm,visar_appointment --stop-after-init
```
