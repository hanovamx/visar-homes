# Decisiones de diseño

Cada decisión con su porqué. Las marcadas **[RESUELTA]** ya están reflejadas en el código.

## [RESUELTA] Técnicos = recursos, no usuarios
`appointment.type.schedule_based_on = 'resources'`. Cada técnico es un `appointment.resource`
ligado a un `hr.employee` (`visar_employee_id`).
- **Por qué:** los técnicos de campo no necesitan licencia/usuario Odoo para ser reservables.
- **Implica:** el filtrado de disponibilidad usa `filter_resources` (no `filter_users`).
- **Pendiente operativo:** para que la disponibilidad respete el horario y ausencias del
  empleado, el recurso debe compartir el `resource_id`/`resource_calendar_id` del empleado.
  El módulo `appointment_hr` (working hours) solo aplica al modo *users*.

## [RESUELTA] Inversión de flujo vía controlador + redirect (opción A), no override OWL/JS (B)
Página propia `prequalify` + redirect al horario nativo con `filter_resource_ids`.
- **Por qué:** el frontend de citas usa el framework **Interactions** (`@web/public/interaction`),
  no un SPA OWL. Patchear esas Interactions y su QWeb es frágil (≈7/10): un error JS rompe toda
  la página, los métodos no son API estable, y cada upgrade suele tocarlas. La opción A no toca
  nada de ese JS; solo depende de un parámetro de URL que el core ya sabe leer.
- **Costo de A:** 1–2 recargas de página (UX aceptable). Si luego se quiere UX sin recargas,
  hacerlo como mejora aislada y opcional.

## [ACTIVA — parcialmente resuelta] Captura de Zona y m² fuera de `appointment.question`
Se capturan en páginas propias del wizard/valoración y se guardan como campos en `calendar.event`
(`visar_zone_id`, `visar_booking_items`), **y además** se inyectan en el sistema nativo de respuestas.
- **Por qué original:** `appointment.question` no tiene tipo numérico (solo char/text/phone/select/radio/checkbox).
  Campos en `calendar.event` son directos de mostrar al administrativo.
- **Limitación conocida (legacy D-03):** el flujo prequalify numérico sigue usando `visar_m2` en cita;
  el wizard D-05 guarda rangos en `visar_booking_items`.
- **Implementado jun-2026 (híbrido):** preguntas en `visar_questions_data.xml` (zona, m², plaga,
  roedores, tipo plaga). `_visar_enrich_answer_inputs` las pobla en `appointment_answer_input_ids`
  al submit. Las preguntas **no** aparecen en el formulario nativo de horario (desvinculadas de tipos
  entrada en migración 19.0.2.0.12); sí en **Questions & Answers** del backend.
- **Mejora pendiente:** tipo `select` nativo para Zona (hoy char con texto); m² como char con etiqueta del tramo.

## [RESUELTA] Configuración del mapeo en la ficha del PRODUCTO + vista lista global
Pestaña "Visar / Citas" en `product.template` (campo `visar_appointment_type_id` que liga todo +
tabulador inline `visar_tier_ids`), más una vista lista global del modelo `visar.service.tier`.
- **Por qué:** el modelo mental del cliente es "servicios = productos"; configurar ahí es natural.
  La vista lista global da un panel de administración rápido sin entrar producto por producto.
- Enlace producto ↔ tipo de cita asumido **1:1** (validar con Visar si alguna vez es 1:N).

## [RESUELTA — D-04] Variantes nativas + tabla de tramos + pricelist por zona
- **Variante = servicio × rango de m²** (variantes nativas de Odoo). NO meter la zona como atributo.
- **Tramos en `visar.service.tier`** porque los rangos de m² **difieren por servicio**
  (Fum. interior: 1-250/251-500; Corte de pasto: 51-100) → un atributo "rango" compartido no sirve.
- **Zona = pricelist con %** (A=+15%, B=base, C=−10%), porque el tabulador resultó ser un %
  constante, no precios arbitrarios. Excepción: Valoración Técnica = $500 plano (regla fija con
  precedencia sobre la regla %).
- **Por qué no `price_extra`:** es **aditivo**; no modela un tabulador donde el precio de la
  combinación no es la suma de extras.
- **A validar con Visar:** que el +15%/−10% sea regla estable y no coincidencia de los valores
  actuales. Si fuera arbitrario por celda → migrar a ítems de pricelist `fixed` por variante×zona.

## [D-05 — VIGENTE] Wizard multi-servicio en lugar de selección nativa
El cliente ya no elige un `appointment.type` nativo: un **wizard propio de varios pasos**
(servicios → tipo de fumigación → dimensiones → zona) determina los servicios y variantes.
- **Por qué:** Visar necesita vender **combinaciones** (fumigación interior + exterior + corte) en
  una sola reserva, con reglas de precio cruzadas (combo, exterior aditivo). El flujo nativo 1-servicio
  no lo soporta.
- **Cómo:** mismo patrón que D-03 (páginas QWeb propias + estado en sesión, sin OWL/JS). Reusa la
  infraestructura de filtrado por zona y la redirección al horario nativo con `filter_resource_ids`.

## [D-05 — RESUELTA] Punto de entrada: cuadro nativo con dos tipos de cita
Se **conserva** el listado nativo `/appointment`, pero con **solo dos `appointment.type`**:
- **Valoración Técnica:** al elegirlo se pregunta **solo Zona** → horario → cita con producto $500.
- **Cita de Servicios:** al elegirlo se abre el **wizard completo** (servicios → fumigación →
  dimensiones → zona) → una cita con varias líneas.
- **Decisión Visar (22-jun-2026).** Enrutado por campo `appointment.type.visar_flow`
  (`valuation`/`wizard`) en el override `appointment_type_page`. Ancla técnica: `entry-point-d05`
  en `20-architecture.md`. **Implementado v19.0.2.0.3** (migraciones + self-healing `_visar_ensure_entry_flow`
  en controlador; pendiente mover a `post_init_hook`).
- **Nota:** la valoración existe en dos formas — (a) **tipo de cita directo** (este), y (b) **desde el wizard**
  cuando un tramo tiene `is_valuation=True`. En (b) ya **no** se agenda en el maestro Servicios Visar:
  se muestra un **aviso** y se redirige al mismo flujo que (a). Mismo producto $500.

## [D-05 — RESUELTA — jun-2026] Wizard con tramo valoración → flujo valoración directa
Cuando el cliente elige un rango marcado `is_valuation` en el wizard:
- **Antes (bug):** seguía al maestro **Servicios Visar** → SO correcta ($500) pero cita con nombre incorrecto.
- **Ahora:** paso **aviso** (`…/wizard/valoracion-aviso`) → pantalla `…/visar/valoracion` → horario del tipo
  **Valoración Técnica** → checkout $500.
- **Por qué:** el requisito D-04/D-05 dice *agendar visita de $500 en lugar del servicio directo*; la cita
  debe ser de valoración, no multi-servicio maestro.
- **Implementación:** `_visar_selections_require_valuation`, `_visar_get_valuation_appointment_type`,
  rutas aviso + redirect; plantilla `visar_wizard_valuation_notice`.

## [D-05 — RESUELTA] Una sola cita con varias líneas de producto
Una reserva multi-servicio genera **un** `calendar.event` y **una** orden de venta con **N líneas**
(una variante por servicio), no varias citas.
- **Decisión del cliente (22-jun-2026):** "se crea una sola cita pero se asignan/cobran varios
  productos con sus respectivas variantes". Confirmado explícitamente.
- **Implica:** un único horario común; intersección de técnicos elegibles de todos los servicios;
  el cobro suma todas las líneas en la misma SO.

## [D-05 — RESUELTA] Dimensiones como rangos (opciones), no m² numérico
El Paso 3 presenta **rangos predefinidos** (del tabulador) como opciones; cada rango = un
`visar.service.tier` → variante. Se elimina el input numérico libre de m².
- **Por qué:** el tabulador define precios por **rango cerrado**; elegir el rango resuelve variante y
  precio sin ambigüedad y evita capturar m² exactos.
- **Implica:** los tramos se vuelven la fuente de las opciones del wizard (añadir `name`/`label`).

## [D-05 — RESUELTA] Reglas del tabulador (ver `70-tabulador.md`)
Confirmadas con Visar el 22-jun-2026:
- **Exterior aditivo pero independiente:** fumigación exterior se cobra como línea aparte (tramo 0–50 m²
  = precio 0). **Se puede agendar sin interior.**
- **Combo −50% del corte:** aplica **solo** cuando la reserva incluye los **tres** servicios
  (interior + exterior + corte). La línea de corte va al 50% (factor `visar.combo_corte_factor = 0.5`,
  `ir.config_parameter`). Si falta cualquiera de los tres → corte a precio completo.
- **Valoración $500:** servicio fuera de rango → línea de Valoración Técnica $500 (plano, abonable);
  **una sola** línea aunque varios servicios la disparen (**provisional**, a reconfirmar).
- **Zona = pricelist con %** (A +15%, B base, C −10%); valoración $500 plano (precedencia sobre %).

## [D-05 — RESUELTA] Varios técnicos por cita
Con varios servicios, la cita puede tener **varios técnicos**: se asignan los necesarios para cubrir
**todos** los servicios elegidos; si un técnico no cubre todos, se agregan los que falten.
- **Decisión Visar (22-jun-2026):** "pueden ser varios por cita".
- **Implica:** la asignación NO es la intersección estricta (un técnico que cubra todo). Es una
  **cobertura**: para cada servicio, al menos un recurso elegible (servicio + zona) en la cita.
- **Agenda multi-técnico (RESUELTA, Visar 22-jun-2026):**
  - **Horario:** solo se ofrecen slots donde **todos** los técnicos requeridos estén libres a la vez.
  - **Modelo:** **un solo `calendar.event`** con **varios `appointment.resource`** (vía
    `booking_line_values` múltiples).
  - **Asignación:** cuando varios técnicos cubren un servicio, elegir **por carga** (el menos ocupado).
  - **Sin coincidencia:** si no hay slot común para todos, **mostrar mensaje al usuario** (no bloquear silencioso).

## Pendientes de confirmar con Visar
- ¿El +15%/−10% por zona y el factor combo 0.5 son reglas estables?
- ¿La valoración a **una** línea $500 es definitiva (hoy provisional)?
- ¿El enlace producto ↔ tipo de cita es siempre 1:1?

## [IMPLEMENTADO — jun-2026] Split en tres módulos

Monolito `visar_appointment` dividido para separar responsabilidades:

| Módulo | Responsabilidad |
|---|---|
| **`visar_base`** | Catálogos (zonas, grupos, dimensiones, tramos, combo, add-ons D-06), extensiones producto/SO |
| **`visar_fsm`** | Generación tareas FSM agrupadas (D-07), `post_init_hook` proyectos |
| **`visar_appointment`** | Wizard web, controlador, tipos entrada, plantillas, preguntas nativas |

- **Por qué:** D-06/D-07 no dependen del website; facilita reuso y despliegue incremental.
- **Dependencias:** `visar_appointment` → `visar_fsm` → `visar_base`.

## [IMPLEMENTADO — jun-2026] D-06 Add-ons obligatorios (Opción A + sumar)

- Modelo `visar.product.optional.line` en `visar_base`.
- M2m nativo `optional_product_ids` es selector; tabla se autogenera/reconcilia.
- Duplicados entre servicios: **SUMAR** cantidades.
- Inyección web en `_visar_build_sale_lines`; backend en `sale.order._visar_apply_mandatory_addons`.

## [IMPLEMENTADO — jun-2026] Calificación wizard + producto roedores

- Paso `…/wizard/calificacion` después de rangos (solo flujo normal, no valoración).
- `roedores=si` → producto `visar_is_roedores` + add-ons obligatorios de ese producto.
- Respuestas en Questions & Answers vía preguntas XML + `_visar_build_calification_answer_inputs`.

## [IMPLEMENTADO — 07-jul-2026] Renderizador de worksheet enriquecido (app de campo)

La app de campo (`visar_field_app`) **refleja** la vista formulario nativa de la worksheet dinámica
y la renderiza como formularios web propios. Se extendió de "v1 plana" a soportar la estructura real
que diseña Studio.

- **Qué se decidió renderizar:** respetar `<header>`/pestañas invisibles/widgets; **one2many** como
  **tarjetas dinámicas** (patrón "Fotos", clon inerte + un solo POST que sincroniza por conjunto);
  **many2many** como **grupo de casillas**; **imagen por tarjeta o2m** (input file + miniatura por ruta);
  **ayuda por campo** leída del **nodo de la vista** (`node.get('help')`, no del modelo).
- **Por qué así:** mantiene el principio D-03/D-05 (QWeb + POST/redirect, sin OWL) y sigue escribiendo
  en modelos/campos **nativos** para que los reportes nativos funcionen. Alternativas descartadas:
  many2many como booleanos (contradice "selección múltiple", data sucia); PWA/SPA (contradice `40`).
- ~~**Límite conscientemente NO resuelto:** la app **no evalúa `required`/condicional** — se codifica en
  los nodos de la vista para Studio/reporte nativo, pero la app no bloquea el cierre. Feature aparte.~~
  **[RESUELTO 17-jul-2026]** — la hoja valida requerido/condicional/min-uno (cliente + servidor);
  lee el `required="1"` del nodo de la vista. Ver `25-field-app.md` (Req 7).
- **Plantillas:** se configuran en Studio (`worksheet.template`), se **asignan por proyecto**
  (`project.project.worksheet_template_id`, heredado a la tarea). Sembrado por código = Python
  (`ir.model.fields state=manual` + arch), porque el modelo dinámico lleva el id del template en su nombre.
- **Detalle en** `25-field-app.md` → "🆕 Actualización 07-jul-2026".

## [IMPLEMENTADO — 08-jul-2026] Sembrador de plantillas en código (no XML), triple-cableado

Las `worksheet.template` de la app de campo ("Fumigación interior o exterior (App v2)" y
"Mantenimiento de áreas verdes (App v2)") se **siembran por código Python idempotente**
(`visar_field_app/hooks.py::seed_worksheet_templates`), no por datos XML.

- **Por qué no XML:** cada `worksheet.template` autogenera un modelo dinámico cuyo nombre incluye el
  **id del template** (`x_project_task_worksheet_template_<id>`); los campos viven en ese modelo, así
  que **no se pueden declarar en XML** (el nombre no existe hasta crear el registro). Odoo mismo solo
  siembra el registro vacío en XML y agrega campos por Studio. Alternativa descartada: export de Studio
  (requiere Enterprise/Studio y no queda versionado limpio).
- **Triple cableado** (para que caiga en prod en cualquier escenario): `post_init_hook` (install limpio)
  + `migrations/19.0.1.2.0/post-migrate.py` (upgrade de módulo ya instalado) + ejecución manual por shell.
- **Idempotente = fuente de verdad:** re-ejecutar reescribe el arch al canónico; **ediciones en Studio
  sobre prod se pierden**. El código manda para estas dos plantillas.
- **Asignación NO se automatiza:** sembrar crea las plantillas; apuntar el proyecto correcto
  (`project.project.worksheet_template_id`) es paso manual deliberado.
- Detalle en `25-field-app.md` → "🆕 Actualización — 08-jul-2026 (bis)". Cierra backlog I-06.

## [PARCIAL — jun-2026] D-07 FSM agrupado por proyecto

- `visar_fsm/models/sale_order_fsm.py`: override generación nativa, una tarea por `project_id`.
- Proyectos seedeados en `visar_fsm/hooks.py` (configurable vía `product.template.project_id`).
- Pendiente: worksheets, reportes, E2E app técnico.

## [IMPLEMENTADO — 26-jun-2026] Ocultar `sale_line_id` y mostrar orden de venta completa en tarea FSM

- **Decisión:** todos los servicios Visar son precio fijo/prepago; al administrativo le interesa la **orden
  completa** (cita multi-línea), no la línea representante que usa el core para timesheet.
- **Qué:** `sale_line_id` sigue asignándose en `_visar_create_grouped_tasks` pero se **oculta** en la vista;
  se muestra `visar_sale_order_id` (related a `sale_order_id`).
- **Por qué:** una cita con fumigación + corte genera varias líneas SO pero una o pocas tareas FSM; ver
  "Artículo de la orden de venta" confunde. El puente nativo FSM↔Venta no se rompe.
- **Descartado:** pestaña One2many `visar_sale_line_ids`.
- **Módulo:** `visar_fsm` v19.0.1.0.1 — `models/project_task.py`, `views/project_task_views.xml`.

## [IMPLEMENTADO — jun-2026] Datos demo: productos existentes, no XML

En `visar_local` los productos/variantes **ya existían** (con atributos **A/B/C × rango** en cada
variante, no solo pricelist). D-05 **no recrea** productos en XML:

- Enlace vía migración `19.0.2.0.7/post-migrate.py` (`_visar_migrate_legacy_catalog`).
- Tramos `visar.service.tier` apuntan a variantes **zona B + rango**; en checkout
  `_visar_variant_for_zone` elige la variante equivalente en la zona del cliente (A/B/C).
- Convive con pricelist por zona en la SO (doble mecanismo hasta decidir migración única).

## [IMPLEMENTADO — jul-2026] Agente WhatsApp: arquitectura híbrida (runtime externo + API en Odoo)

Un **agente de IA por WhatsApp** que contesta dudas de servicios y precios. Se
parte en dos: un servicio externo **FastAPI** (`visar_fastapi/`, con LLM y loop de
tools) y un módulo Odoo **`visar_whatsapp_agent`** que solo expone una API RPC de
solo lectura. Detalle en `27-whatsapp-agent.md`.

- **Por qué externo:** Odoo tiene pocos workers y no está hecho para esperar la
  latencia de un LLM; un webhook público sobre el ERP amplía la superficie de
  ataque; separar el runtime deja escalarlo/desplegarlo sin arriesgar el negocio.
  Corre en el **mismo servidor** que Odoo.
- **Fase 1: solo lectura, sin agendar.** El wizard (`visar_appointment`) no se toca.
- **[RESUELTA] El precio no se reimplementa:** `agent_quote_service` arma los mismos
  `items` del wizard y llama `_visar_quote_booking`, así el total del agente es
  idéntico al de la web (variante combinada interior+exterior, combos, add-ons).
  Por eso el módulo depende de `visar_appointment`, no solo de `visar_base`.
- **[RESUELTA] Mínimo privilegio:** usuario `whatsapp_agent` (share) + grupo de solo
  lectura; los métodos **no** usan `sudo`, así que las ACLs son el límite real.
- **Superficie de API acotada:** tres métodos tipados; ningún nombre de modelo,
  dominio ni SQL desde el LLM (defensa ante prompt injection).
- **Pendiente:** instalar y **validar paridad de precio** contra el wizard; modelos
  de configuración editables por consultores (`visar.llm.config`, etc.).

## [DISEÑO — jul-2026] Fase 2: el número como plataforma de capacidades

Un solo número que hace **varios trabajos** detrás de un **dispatcher**: dudas por
LLM (hecho), flujo de cita determinista (cuestionario, sin LLM), y **salientes
disparados** por eventos (p. ej. app de técnicos). Diseño completo en
`28-whatsapp-agent-phase2-design.md`.

- **No modelar todo como "prompt":** los trabajos difieren en dos ejes
  (entrante/saliente, LLM/determinista). "Prompt editable" es solo el handler LLM.
- **[DECISIÓN] No usar el módulo WhatsApp nativo para los salientes.** El nativo
  asume que Odoo es dueño del webhook, que ya tiene el runtime FastAPI para lo
  entrante. Coexistir lo deja medio ciego (envía pero no recibe estados/respuestas)
  y duplica el token. Los salientes se hacen con **templates aprobados en Meta** +
  envío por el runtime + **automatización Odoo** sobre el evento. El nativo solo
  convendría si Odoo fuera el hub de WhatsApp (no es el caso).
- **[REGLA de plataforma] Ventana de 24 h:** fuera de 24 h del último mensaje del
  cliente, WhatsApp **solo** permite templates aprobados → los salientes disparados
  son templates, no texto libre.
- **Secretos:** Fase 2a mueve a Odoo solo lo **no-secreto** (prompt, model, notas);
  token/app_secret/api_key siguen en el `.env` del runtime hasta tener
  almacenamiento seguro (mover secretos a la BD los mete en los backups).

## [IMPLEMENTADO — ago-2026] Pólizas: el cobro adelantado es una línea, no un multiplicador

La regla "la póliza se paga 2 meses por adelantado" vivía como `ratio = 2` al facturar.
El sitio web nunca pasa por ahí: `website_sale` cobra exactamente `order.amount_total`.
- **Por qué la línea:** no hay forma de cobrar de más "por detrás" en el checkout web.
  Si el importe difiere del total del pedido, la transacción se rechaza. El segundo mes
  tiene que existir como línea para poder cobrarse.
- **Una línea por servicio, no una sumada:** reproduce exacto el IVA y el descuento de
  combo por línea, permite contar los periodos pagados de **cada** servicio en una
  póliza combo, y al quitar un servicio se va su anticipo (ondelete cascade).
- **Efecto colateral que se arregló solo:** antes la factura (2 meses) nunca quedaba
  pagada por el cobro (1 mes), así que `_invoice_paid_hook` no disparaba y **no se
  generaba ninguna visita**. El bug de precio tapaba un bug de servicio.

## [IMPLEMENTADO — ago-2026] Precios de póliza: listas (zona × plan), no descuento en código

Un pedido solo puede tener UNA lista de precios, y las de suscripción vivían aparte de
las de zona, así que el carrito nunca las resolvía.
- **Alternativa descartada (calcular en código):** `_recompute_prices()` se dispara al
  escribir la dirección en el checkout y **resetea `price_unit` y `discount`**. Un precio
  puesto a mano se pierde ahí, después de habérselo enseñado al cliente. Además dejaba el
  descuento como un hecho de Python, no configurable.
- **Alternativa descartada (precios fijos en las listas de zona):** exigía 78 precios
  duplicados —el atajo por `list_price` da mal en zonas A y C— y armaba la trampa de
  `_cart_add` (cualquier añadido de los productos 30/31 volvería el pedido suscripción).
- **Elegida:** 6 listas (zona × plan) con 2 reglas globales que **derivan** de la lista de
  la zona. 12 reglas sustituyen a 78, sin duplicar un solo precio.
- **Costo aceptado:** cambiar el descuento son 3 reglas (una por zona), no una. A cambio
  el consultor lo edita en la UI sin tocar código. Se evaluó un campo único en el plan;
  se dejó fuera para no volver a meter lógica de precio en Python.

## [IMPLEMENTADO — ago-2026] La póliza se contrata en el wizard, no en `/shop`

Se ofrecía desde una página de producto donde el cliente **volvía a elegir** Zona /
Tamaño inmueble / Tamaño jardín, datos que el wizard ya había recogido.
- **Dónde:** un paso más, justo después de *¿Deseas agregar algo más?*.
- **Por qué ahí y no en el carrito:** el wizard es dueño de la sesión de reserva y arma
  el carrito de una sola vez al final; decidir antes deja el carrito correcto desde el
  primer momento, sin editar líneas después.
- **La primera visita hereda la cita:** si no, el horario y el técnico que el cliente
  acababa de elegir se perdían — `_timesheet_service_generation` saca las líneas de
  póliza antes de que visar_fsm cree su tarea.
- **Los add-ons no entran en el precio de la póliza:** son cargo único de la primera
  factura, no se repiten. Anunciarlos "al mes" infla el precio y no es lo que se cobra
  en el mes 3.
- **Bimestral se ofrece a precio de paridad**, vendido por conveniencia (agenda
  automática, visitas de garantía incluidas). **Pendiente de confirmar con Visar** si se
  queda así o lleva descuento.
