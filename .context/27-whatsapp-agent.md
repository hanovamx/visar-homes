# visar_whatsapp_agent — API para el agente de WhatsApp (5º módulo)

> Módulo añadido en jul-2026, después de la última revisión general de esta
> carpeta. Es la **mitad Odoo** de un agente de IA por WhatsApp; la otra mitad es
> un servicio externo **FastAPI** (`visar_fastapi/`, fuera de este repo) con su
> propio `.context/`.

## Qué es

Superficie **RPC de solo lectura** que consume el runtime externo del agente.
No tiene interfaz de usuario. El agente (LLM) contesta dudas de clientes sobre
**servicios y precios** por WhatsApp; este módulo le da acceso acotado al
catálogo y al tabulador, sin dejarlo escribir nada ni tocar modelos arbitrarios.

```
Cliente → WhatsApp Cloud API → visar_fastapi (FastAPI + LLM) → RPC → visar.agent.tools
```

**Fase 1: solo lectura. No agenda citas.** El wizard web (`visar_appointment`)
no se toca.

## Arquitectura híbrida (por qué fuera de Odoo)

El runtime vive fuera de Odoo a propósito: Odoo corre con pocos workers y no está
hecho para esperar la latencia de un LLM; exponer un webhook público sobre el ERP
amplía la superficie de ataque; separar el runtime deja escalarlo/desplegarlo sin
arriesgar el negocio. Odoo se queda con lo que le corresponde: **datos y
configuración**. Corre en el **mismo servidor** que Odoo. Ver `40-decisions.md`
(entrada nueva) y el `.context/40-decisions.md` de `visar_fastapi`.

## `visar_whatsapp_agent` (v19.0.1.1.0)

**Dependencias:** `visar_appointment`.

> Depende de `visar_appointment` —no solo de `visar_base`— porque **reutiliza su
> motor de precios** (`_visar_quote_booking`). Ver "La cotización no se
> reimplementa" abajo.

> **v19.0.1.1.0 (28-jul-2026) — Fase 2a:** se añadieron 3 modelos de configuración
> (`visar.agent.prompt`, `visar.llm.config`, `visar.whatsapp.config`), el método RPC
> `agent_runtime_config()`, y vistas + menú "Agente WhatsApp" (grupo
> `base.group_system`). **Sin secretos en la BD.** Ver `28-whatsapp-agent-phase2-design.md`.

### Modelos

| Modelo | Archivo | Para qué |
|---|---|---|
| `visar.agent.tools` | `models/visar_agent_tools.py` | **AbstractModel**. Métodos `@api.model` de solo lectura. Sin tabla; se llama por RPC. |
| `visar.agent.prompt` | `models/visar_agent_prompt.py` | Prompt del sistema editable (lista, uno activo por `sequence`). |
| `visar.llm.config` | `models/visar_llm_config.py` | Proveedor/modelo/`max_tokens`/`max_tool_iterations` (sin credenciales). |
| `visar.whatsapp.config` | `models/visar_whatsapp_config.py` | Cuenta de WhatsApp (no-secreto). **Display-only en 2a.** |

### Los métodos RPC

| Método | Entrada | Devuelve |
|---|---|---|
| `agent_catalog_snapshot()` | — | grupos, dimensiones, tramos y zonas (**sin** precios ni CPs) |
| `agent_runtime_config()` | — | `{prompt\|None, llm{provider,model,max_tokens,max_tool_iterations}}` — **sin secretos, sin `notes`** (van en el catálogo) |
| `agent_resolve_zone(cp)` | código postal | zona y cobertura |
| `agent_quote_service(payload)` | `{cp, items:[{service_code, m2}...]}` o `{service_code, cp, m2}` | líneas y total |
| `agent_customer_services(payload)` | `{phone}` | servicios agendados/pendientes del cliente (etapa C) |

> `agent_customer_services` (etapa C, jul-2026) es la ruta **"Servicio existente"**:
> teléfono → `res.partner` (últimos 10 dígitos) → órdenes confirmadas → cita
> (`calendar.event`) y tarea FSM (`project.task`). **Único método con `sudo()`**
> (acotado): cruza datos de cliente que el ACL del usuario share no ve, y devuelve
> un dict tipado y mínimo. No amplía el ACL del share.

`service_code` es un **código de dimensión** (`FUM_INT`, `FUM_EXT`, `MAV_JAR`),
no de grupo. Si se manda un grupo con varias dimensiones, la respuesta trae
`needs_clarification: true` con las opciones, sin total (cada dimensión tiene su
tabulador; no se adivina).

Ningún método acepta nombres de modelo, dominios ni SQL. Es intencional: acota lo
que el LLM puede pedir aunque le metan prompt injection.

### La cotización NO se reimplementa

`agent_quote_service` construye los mismos `items` que arma el wizard
(`_visar_resolve_wizard_items` produce la misma forma) y los pasa a
**`appointment.type._visar_quote_booking(items, zone)`**, el motor de precios que
ya existe (`20-architecture.md` → `visar_appointment`). Por eso el total del
agente es, **por construcción**, idéntico al de la web e incluye:

- la **variante combinada** de fumigación interior+exterior — la rejilla
  **zona × m² interior × m² exterior** (`_visar_combined_variant_for_tiers`,
  `70-tabulador.md`). El precio combinado **no** es la suma de cotizar interior y
  exterior por separado.
- los **descuentos de combo** entre servicios (`visar.combo.rule`);
- los **add-ons obligatorios** (D-06) y los tramos incluidos sin cargo.

Helpers de `visar_base` que también reutiliza: `visar.zone.cp._get_zone_for_cp`,
`product.template._visar_get_service_template_for_dimension` /
`_visar_tier_for_dimension_m2`, `visar.service.tier._visar_get_variant_for_zone`.

### Seguridad (principio de mínimo privilegio)

- Grupo **"Agente WhatsApp / Solo lectura"**
  (`security/visar_whatsapp_agent_groups.xml`) con ACLs de solo lectura
  (`security/ir.model.access.csv`) sobre los modelos de catálogo, producto,
  pricelist, add-ons, moneda, uom y website.
- Usuario `whatsapp_agent` (tipo **share**) en ese grupo.
- **Los métodos no usan `sudo`** (salvo lecturas incidentales de
  `ir.config_parameter` / `visar.combo.rule` que ya venían con `.sudo()` en el
  código reutilizado): corren como el usuario que llama, así que **esas ACLs son
  el límite real**.

> **Superficie de ACL (validada 23-jul-2026).** Reutilizar `_visar_quote_booking`
> arrastra lecturas de varios modelos. En el primer uso real faltaba **una**:
> `res.company` (lo lee la moneda de la compañía). Ya está en el CSV. Si aparece
> otro `AccessError`, el fix es una línea más.

### Gotchas de Odoo 19 encontrados al instalar

- `res.groups.category_id` **ya no existe** (pasó a `privilege_id` →
  `res.groups.privilege`). El grupo técnico se declara sin él.
- `res.users.groups_id` → **`group_ids`**.
- `res.partner.mobile` **ya no existe** (Odoo 19 lo eliminó; sólo queda `phone` +
  `phone_sanitized`). `agent_customer_services` (etapa C) tronaba en el 100% de
  las llamadas por buscar `mobile`; ahora sólo usa `phone`. **Diagnóstico en
  servidor 31-jul-2026 (BD `visar-db`).**
- API keys: el usuario *share* tiene un cap de duración de **1 día**
  (`max(group.api_key_duration) or 1.0`). El grupo fija `api_key_duration` alto
  (~10 años) para poder emitir una key de servicio de larga duración. `_generate`
  solo salta el cap si `env.is_system()`, así que la key se emite con el usuario
  agente y su cap ya subido, no en sudo.

### Validación de paridad (23-jul-2026, BD `visar_prod`)

Instalado y validado contra datos reales. `agent_quote_service` (usuario acotado)
= `_visar_quote_booking` (motor del wizard) **al peso** en: interior solo,
exterior solo, interior+exterior juntos (**una línea combinada**, 1150, que **no**
es la suma 690+1150=1840), y el combo triple (línea de corte a −50%). Round-trip
por XML-RPC desde el `OdooRPCClient` de `visar_fastapi` con API key: mismos
totales. Detalle en el `.context/50-status-roadmap.md` de `visar_fastapi`.

## Puesta en marcha

1. Instalar el módulo (arrastra `visar_appointment`).
2. Crear una **API key** para `whatsapp_agent` (Preferencias → Seguridad de la
   cuenta → Nueva API key). **API key, no contraseña.**
3. Notas de negocio para el prompt (opcional): parámetro del sistema
   `visar.agent.catalog_notes`.
4. Apuntar el servicio FastAPI a Odoo (`ODOO_FAKE=false`, credenciales). Ver el
   `README.md` del módulo y el `.context/` de `visar_fastapi`.

## Verificación de paridad — HECHA (23-jul-2026)

Ver "Validación de paridad" arriba. `agent_quote_service` coincide al peso con el
wizard en todos los escenarios probados, incluida la variante combinada y el
descuento de combo. La lógica es código compartido, así que la única forma de
divergir sería datos o ACL — y las ACLs ya se cerraron.

## Fase siguiente

> **Diseño detallado en [`28-whatsapp-agent-phase2-design.md`](./28-whatsapp-agent-phase2-design.md)**
> — plataforma de capacidades (un número, varios trabajos: LLM Q&A, flujo de
> cita determinista, salientes disparados por template), listo para implementar.

En resumen:
- **Config + prompt editables en UI Odoo** (`visar.whatsapp.config`,
  `visar.llm.config`, `visar.agent.prompt`) — que el prompt base salga de Odoo, no
  del código de `visar_fastapi`. Es el primer corte (Fase 2a).
- **Salientes disparados** (app de técnicos → template aprobado): **no** con el
  módulo WhatsApp nativo (choca con el webhook del agente); templates en Meta +
  envío por el runtime + automatización Odoo. Ver doc 28 (Fase 2b).
- **Agendar citas** como flujo determinista (cuestionario), no por LLM (Fase 2c).
- Almacenamiento seguro de credenciales (hoy en el `.env` del servicio).
