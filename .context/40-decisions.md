# Decisiones de diseño

Cada decisión con su porqué. Las marcadas **[RESUELTA]** ya están reflejadas en el código.

## [IMPLEMENTADO — 13-ago-2026] El combo se presta como UN servicio externo

Cuando una cita trae **fumigación + mantenimiento de áreas verdes**, el pedido generaba **dos
tareas FSM** (una por proyecto) y el técnico veía **dos tarjetas y dos formularios** para una
sola visita: llegada, espera, hoja, firma, PDF y WhatsApp, todo dos veces. El cliente recibía
dos reportes de un mismo servicio.

**Qué se hace:** una visita = una tarea = una hoja = una firma = un PDF, en un proyecto FSM
anfitrión (**Servicios combinados**) con una plantilla de hoja que trae los dos juegos de
campos.

- **La regla de consolidación es CONFIGURACIÓN, no código** (requisito duro de D-07). Cada
  proyecto declara con quién comparte visita en `project.project.visar_fsm_combined_project_id`
  (`visar_fsm/models/project_project.py`). `_visar_create_grouped_tasks` agrupa por proyecto
  **efectivo**, y solo colapsa cuando la cita trae trabajo de **dos o más** proyectos distintos
  que apuntan al mismo combinado: una fumigación sola nunca cae ahí. Dar de alta un tercer
  servicio combinable es marcar un campo, no tocar código. Un combinado archivado, no-FSM o de
  otra compañía **no** consolida (fallo seguro: mejor dos servicios externos que uno roto).
- **Descartado: subtareas espejo** en los proyectos de origen para conservar el conteo. Serían
  tres registros por visita, dos de ellos cascarones que nadie llena y cuyo estado habría que
  espejear de por vida — todo para que cuadrara un reporte.
- **El conteo por línea de negocio vive en la TAREA, no en el proyecto:**
  `project.task.visar_service_group_ids` (m2m calculado y almacenado) sale de las líneas de la
  orden vía `product.template._visar_service_groups()`, el mismo primitivo del fan-out de CRM.
  Una tarea combo lleva los dos grupos y **cuenta en ambos** al agrupar (como las etiquetas
  nativas). El doble conteo es el requisito, no un bug: la suma por grupo es mayor que el número
  de tareas. Se agregó "Agrupar por > Servicio" a la vista de búsqueda de FSM.
  - Depende de `visar_sale_line_ids` (o2m técnico a `sale.order.line.task_id`, que el core no
    define). **No es la pestaña** descartada el 26-jun-2026: no se muestra en ninguna vista.
- **La tarea consolidada se renombra** con los servicios que cubre: el nombre nativo sale del
  producto de la línea representante y nombraría solo una mitad, que es lo que el técnico lee
  en su tarjeta.
- **Excepción deliberada a "la asignación de plantilla no se automatiza":** el proyecto
  combinado lo crea el código y nadie eligió su plantilla; además Odoo le pone la genérica
  nativa al nacer (`_compute_worksheet_template_id`), así que un guardia "solo si está vacío"
  nunca dispararía. `wire_combined_project` la escribe si está vacía **o** si sigue en la
  nativa, y respeta cualquier elección hecha a mano.
- **Pólizas: fase 2.** `_visar_generate_period_visit` es otro camino (una visita por línea y
  por factura pagada, idempotencia por `(orden, factura, línea)`), y consolidar ahí exige una
  regla nueva para una visita que pertenece a dos líneas, un guardia para conteos de visitas
  distintos, y aceptar que se **reduce a la mitad** el conteo que alimenta la siniestralidad.
- **Datos existentes:** las parejas de tareas combo que ya existan se quedan como están; la
  consolidación aplica a pedidos nuevos. Mezclar hojas ya capturadas no tiene respuesta limpia.
- **Módulos:** `visar_fsm` v19.0.1.1.0 (+ su primera migración), `visar_field_app` v19.0.1.24.0.
  Detalle en `25-field-app.md` → "🆕 Actualización — 13-ago-2026".

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

## [IMPLEMENTADO — 10-ago-2026] Los avisos al cliente van por BUZÓN con caducidad, no en línea

Los avisos "voy en camino" / "ya llegué" / "reagendar" se encolan en `visar.wa.message` y los manda
un cron; ya no son la simulación en chatter de `_visar_notify_client`.

- **Por qué no en línea (como el reporte):** el reporte lo manda el técnico pulsando un botón y
  esperando; estos son efecto secundario de un **cambio de etapa**. Enviar en línea colgaría el toque
  de "Voy en camino" mientras Meta responde, y un WhatsApp caído podría tumbar la transición. El
  buzón devuelve el control al instante.
- **El cron se dispara al encolar** (`_trigger()`), así que el camino feliz no espera los 5 minutos
  del intervalo — el intervalo solo cubre reintentos. Patrón de la cola de correo nativa.
- **Cada aviso CADUCA, y eso es el corazón del diseño.** Una cola de mensajes reintenta hasta
  lograrlo; una cola de AVISOS no debe: "su técnico va en camino" entregado una hora tarde es peor
  que no entregado. TTL por tipo (arrived 15 min < enroute 30 min < reschedule 24 h), derivado de la
  utilidad del mensaje, no de un número redondo.
- **Fallar visible:** caducidad e intentos agotados dejan nota en el chatter ("el cliente NO fue
  avisado — conviene llamarle") y la vista de oficina abre filtrada por *No entregados*. El chatter
  conserva el texto completo, así que no se pierde información respecto a la simulación anterior.
- **Reintento manual, no automático**, desde la vista: reenviar un aviso viejo solo tiene sentido si
  alguien confirma que sigue siendo verdad.
- **Coste aceptado:** la redacción que recibe el cliente pasa a vivir en el registro de plantillas de
  Meta (`{{1}}`/`{{2}}`), no en el repo. Cambiarla será una re-aprobación, no un commit. El texto del
  código se conserva como registro interno y respaldo del modo libre.
- **Prerrequisito de negocio, no deuda:** estos avisos van siempre a un cliente que agendó por la web
  y nunca escribió ⇒ **siempre** fuera de la ventana de 24 h de Meta. Sin las tres plantillas
  aprobadas no hay camino viable y el resultado esperado es 502 → caducado.
- Detalle en `25-field-app.md` → "🆕 Actualización — 10-ago-2026 (v19.0.1.18.0)".

## [IMPLEMENTADO — 10-ago-2026] Foto solo por cámara, y el WhatsApp saliente lo manda el runtime

Dos decisiones de la tanda v19.0.1.17.0 (app de campo).

**1. La foto se toma con `getUserMedia`, no con `<input capture>`.**
- **Por qué:** `capture="environment"` es una PISTA. Android la respeta; **iOS Safari la ignora**
  (más aún junto a `multiple`) y sigue ofreciendo el carrete. La evidencia de un servicio pierde su
  valor si puede ser una foto vieja.
- **Cómo, sin tocar el servidor:** el `<input type="file">` se conserva **oculto** y el widget lo
  rellena con los `File` capturados vía `DataTransfer`. El POST sigue siendo el mismo multipart, así
  que ninguna ruta ni validación cambió. Alternativa descartada: subir por una ruta nueva (duplicaba
  el camino de adjuntos y la validación de obligatoriedad).
- **Límite consciente:** cierra el camino fácil, **no** vuelve imposible falsificar (cámara
  virtual). Garantizarlo pide verificación en servidor — tarea aparte, no se finge resuelta.
- **Costes aceptados:** exige **HTTPS** (contexto seguro) y navegador moderno para `DataTransfer`.
  Escotilla `visar_field.allow_gallery_fallback` (def. NO) para desbloquear un dispositivo sin
  cambio de código.

**2. El WhatsApp saliente lo manda `visar_fastapi`, no Odoo.**
- **Por qué:** el access token de Meta está en el `.env` del runtime y **no debe entrar a la BD de
  Odoo** (misma razón que `visar.llm.config`); pywa ya resuelve subida de media y formato; el
  runtime ya es el único que habla con la Cloud API. Alternativa descartada: el módulo Enterprise
  `whatsapp` (metía credenciales de Meta en la BD, contra la decisión vigente).
- **Primera dependencia Odoo → runtime.** Va por **loopback** (`127.0.0.1:8000`, mismo servidor);
  nginx solo proxea `/whatsapp/webhook`, así que `/internal/*` no está en internet. Token
  compartido `X-Visar-Token` como segunda capa. **NO exponer `/internal/` en nginx**: convertiría
  el número verificado de Visar en un relay.
- **Se manda el PDF en base64, no un enlace.** Evita publicar el reporte en una URL con token y
  hace que llegue **adjunto**.
- **Restricción de negocio que el código no puede resolver:** fuera de la ventana de 24 h Meta solo
  entrega **plantillas aprobadas**; el cliente que agendó por web nunca escribió, así que en
  producción `WA_REPORT_TEMPLATE` (cabecera DOCUMENT) es **obligatoria**. Es tiempo de aprobación de
  Meta, no trabajo pendiente.
- Detalle en `25-field-app.md` → "🆕 Actualización — 10-ago-2026 (v19.0.1.17.0)".

## [IMPLEMENTADO — 10-ago-2026] Plantillas: se MODIFICAN en código, no se duplican

Para reestructurar "Fumigación interior o exterior (App v2)" (áreas obligatorias + taxonomía de
plagas de 2 niveles) se **editó la plantilla existente** en `hooks.py` en vez de crear una nueva.

- **Por qué no una plantilla nueva:** el modelo de línea (`x_visar_area_tratada_v2`) ata su FK a UN
  modelo de worksheet, así que una plantilla nueva obliga a re-declarar los ~12 campos de línea;
  además la maqueta del PDF despacha por **nombre** de plantilla (haría falta otra rama) y habría
  que re-apuntar el proyecto, dejando un duplicado permanente en la configuración. El cambio pedido
  era **aditivo** (ningún campo cambia de tipo) → no había migración de datos que esquivar.
- **Cuándo SÍ tocaría duplicar:** si hubiera que preservar hojas ya llenadas con la semántica
  vieja. Hoy no aplica (datos de prueba).
- **Corolario del sembrador:** editar un catálogo de `hooks.py` ahora **sí** llega a una BD ya
  instalada (`_sync_selection` / `_sync_tag_records`). Antes no: `_ensure_field` no toca campos
  existentes, así que la opción nueva se quedaba en el código. Aditivo por defecto; `prune=True`
  es opt-in porque el valor guardado de un `selection` **es la cadena**.
- **Corolario de obligatoriedad:** "oculto ⇒ no obligatorio" ahora se aplica **también en el
  servidor**, y evaluando la cadena de ancestros (la taxonomía anida 2 niveles). Antes el servidor
  solo miraba `required`/`required_if` y habría exigido campos invisibles.
- Detalle en `25-field-app.md` → "🆕 Actualización — 10-ago-2026".

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

## [IMPLEMENTADO — 27-ago-2026] El prompt base se inyecta siempre; cada ruta tiene su memoria

`visar.agent.prompt` gana un campo `ruta`. Sin ruta = **prompt base**, que viaja
en todas las conversaciones desde el primer mensaje. Con ruta = **memoria**, que
se añade después del base y del catálogo y solo mientras la conversación esté
ahí. `agent_runtime_config()` devuelve las memorias en `route_prompts`.

- **Por qué extender el modelo y no crear uno nuevo:** reutiliza vista, acción,
  menú, ACLs y el RPC que ya existían. Un modelo aparte habría sido más limpio en
  el papel y tres archivos más de seguridad y vistas en la práctica.
- **Matiza —no deroga— *"No modelar todo como prompt"* (jul-2026).** Aquello sigue
  valiendo para los **salientes**, que son deterministas y disparados por evento.
  Las rutas entrantes sí son todas handlers de LLM, y por eso caben en el mismo
  modelo.
- **Selection, no Char ni Many2one.** Los ids de ruta son constantes del runtime
  (`app/routing/menu.py`). Con un Char, una errata se guarda limpia, parece
  configurada y no la lee nadie.
- **El base es `ruta` vacía, no un valor `'base'`.** Así el `-u` añade la columna
  en NULL y **el registro de producción ya queda bien colocado sin escribir una
  sola fila**. Con un `'base'` haría falta un `UPDATE`, y la fila que ese `UPDATE`
  se dejara no sería ni base ni ruta: invisible para los dos lectores, o sea el
  agente sin prompt en producción. Coste: `ruta` no puede ser `required`.
- **Sin restricción de unicidad, y es deliberado.** Postgres trata los NULL como
  distintos, así que un `unique(ruta)` no vigilaría precisamente los base;
  impediría archivar una versión anterior (que es para lo que la lista de
  registros existe); y **se crea durante el `-u`**, así que si producción tuviera
  dos filas que la violan, la actualización abortaría a medias sobre la base
  viva. En su lugar: desempate determinista (`sequence, id` — gana el titular) y
  un calculado `es_vigente` que lo hace **visible** en la lista y en el formulario.
- **La siembra va en `data/` con `noupdate="1"`, y el base NO se siembra.** Las
  memorias son contenido nuevo: no hay nada que pisar, y `noupdate="1"` hace que
  lo que afine el consultor sobreviva al siguiente `-u`. Sembrar el base, en
  cambio, crearía un **segundo** candidato en producción; si ganara el desempate,
  los 20 000 caracteres del prompt real desaparecerían sin un solo error.
  Las memorias van a `sequence 20` para que, aun vaciando una `ruta` por
  accidente, el base (secuencia 10) siga ganando.
- **Los lectores no pueden levantar.** Si la RPC falla y el runtime aún no tiene
  nada cacheado, `RuntimeConfigCache.refresh` re-lanza y el servicio deja de
  contestar **a todos**. Degradar a `None`/`{}` es aceptable; fallar, no.
- **Compatible en las dos direcciones**, así que el orden de despliegue no puede
  romper nada: Odoo nuevo + runtime viejo ignora la clave; runtime nuevo + Odoo
  viejo se queda sin memorias. Se despliega **Odoo primero** porque su caso es
  inerte y el otro solo degradado.
- **La migración no escribe nada, solo registra.** Y se ganó el sitio a la
  primera: en producción hay **dos** prompts base activos, y el `WARNING` lo dijo.

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

---

# Agendado por WhatsApp (ago-2026)

> Las decisiones de esta sección se tomaron en el diseño
> [`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md) §12 y **no estaban
> reflejadas aquí**. Se espejan con su estado real al 20-ago-2026. El detalle y el porqué largo
> viven en el doc 33; aquí queda el registro y si está hecho o no.

## [IMPLEMENTADO — 19/20-ago-2026] Se agenda ENTERO dentro del chat

El cliente recorre el cuestionario, elige día y hora, aparta el horario y recibe la liga de
pago sin salir de WhatsApp. **Supera la decisión 10 del doc 29** (hand-off por deep link al
wizard web), que había resuelto lo mismo mandando al cliente a la web.

Lo único que sale del chat es **el pago**.

## [IMPLEMENTADO — 19-ago-2026] El cuestionario vive en el MODELO, no en el controlador

Las reglas (podar, secuenciar, normalizar, ofrecer) bajaron del controlador web a
`appointment.type` (`appointment_wizard_flow.py`), y los dos canales las comparten.

**Por qué:** el web las tenía atadas a `request.session` y el agente necesitaba **las mismas**
por RPC. La alternativa era una segunda copia, y este proyecto ya se quemó con eso — ver el
riesgo estructural "dos front-ends, un flujo" (doc 33 §11) e I-11.

Que el riesgo es real quedó demostrado el 20-ago: el runtime llevaba **duplicada** una regla de
"elige al menos una" que Odoo no tenía, y dejaba el paso de extras sin salida (`6999839`).

## [IMPLEMENTADO — 17-ago-2026] Apartado de horario de 10 minutos (butaca de cine)

Una reserva sin pagar **no consume capacidad** en Odoo, así que dos clientes podían llegar al
pago del mismo horario y el segundo pagaba y se quedaba sin cita. En el web el hueco es
estrecho; por WhatsApp, entre "te mando la liga" y "el pago entra" pasan minutos.

Se descuenta en `_get_resources_remaining_capacity`, el **único** punto por el que pasan todos
los caminos — así protege los dos canales con un solo cambio.

## [IMPLEMENTADO — 19-ago-2026] La liga de pago vive y muere con el apartado

Al caducar el apartado, la liga **deja de cobrar**. Cierra el hueco del pago tardío: sin esto se
podía cobrar algo que ya no se puede entregar. Si al ir a pagar el slot sigue libre, se vuelve a
apartar solo; si ya es de otro, se rechaza **antes** de que haya dinero de por medio.

## [IMPLEMENTADO — 19/20-ago-2026] Corregir UN paso, no volver a empezar

Desde la pantalla de revisión se corrige un paso y se re-pregunta **solo lo que dependía de él**.

La regla sale sola de la poda que ya existía: lo que dependía de la respuesta vieja la perdió y
aparece pendiente; lo que no, conserva la suya y se salta. Hizo falta una función nueva,
`_visar_wizard_next_pending_step` — las dos que había servían para avanzar, no para corregir.

**Semántica de presencia:** `extras_ids` y `poliza_plan_id` marcan "contestado" por la
**presencia de la clave**, no por su valor. *"No quiero extras"* y *"no me lo han preguntado"*
tienen que ser estados distintos.

## [IMPLEMENTADO — 19-ago-2026] Al cliente se le da una VENTANA, no una hora

"3 pm" significa *entre 3 y 4*. Es honesto con lo que pasa en la calle y evita la conversación
de "dijeron a las 3 en punto". Se redacta con el `start`/`stop` que `agent_day_slots` ya
devuelve.

## [CONFIRMADA CON VISAR — 19-ago-2026] El bloque de 1 h son 20 min de traslado + 40 de servicio

**Corrige el acta de junio**, que decía lo contrario (40 min = 20 servicio + 20 traslado). Ver
[`91-reunion-2026-06-22.md`](./91-reunion-2026-06-22.md) §4.

El bloque sale de `appointment_duration` (no horneado); el reparto es configuración. Si algún
día la parte de servicio varía por `items`, lo que cambia es el reparto, no el predicado.

## [DECIDIDA — 19-ago-2026, NO IMPLEMENTADA] Los 20 min son un PRESUPUESTO, no un radio

Para un slot que empieza en `T` con el compromiso anterior terminando en `E`:

```
presupuesto de viaje = 20 min + (T − E)
```

Pegados son 20 justos; con hueco por delante, el hueco se suma — un trayecto de 40 min **sí** se
ofrece si el técnico tiene la mañana libre. No existe un tope duro de "nunca a más de 20 min":
lo que no se puede es **comerse el traslado de otra cita**.

Consecuencia buscada: la disponibilidad depende de **quién reservó antes**. Al cliente no hay
nada que explicarle, porque **nunca ve la opción que no cabe**.

> ✅ **IMPLEMENTADA** el 21-ago-2026 en `visar_appointment/models/visar_travel_feasibility.py`
> (593 líneas). Este aviso decía *"Sin implementar. No hay ni una línea de esto en código"* y se
> quedó ahí; corregido el 31-ago-2026.
>
> Los 20 min son ahora `visar.travel.minutes`, editable en **Ajustes → Visar → Agendado** (0–120,
> con validación), y hay una prueba que revienta si alguien vuelve a hornear el número.
> **Degrada, nunca bloquea**: sin coordenadas, sin token de Mapbox o con Mapbox caído, el horario
> se ofrece igual — una falla de geocodificación no puede costar una reserva. El interruptor
> `visar.travel.enabled` nace **encendido**; `visar.travel.depart_at`, apagado.

## [DECIDIDA — 19-ago-2026] Los bordes del día no se restringen por viaje

Solo se valida el viaje **entre** paradas. La primera parada del día no le quita el traslado a
nadie: no hay cita anterior que proteger.

Con el modelo de presupuesto esto deja de ser una concesión y pasa a ser lo correcto — un tope
duro sí habría obligado a modelar de dónde sale el técnico, y el primer trayecto del día es
justo el más largo. **Efecto práctico: no hace falta geocodificar "Visar Home" para esta fase.**

## [CORRECCIÓN — 19-ago-2026] La zona NO aproxima distancia

La primera versión del diseño decía "zona primero, geometría después: `visar.zone.cp` poda casi
todo el espacio gratis". **Era falso.** Las zonas de Visar son una **métrica de precio**, no de
distancia: no están trazadas por cercanía, y dos direcciones de la misma zona pueden estar a 45
min.

La zona sigue sirviendo para saber qué técnicos atienden y qué lista aplica. El control de costo
descansa entero en "una llamada de Matrix por (día, técnico, franja horaria)".

## [DECIDIDA — 21-ago-2026, IMPLEMENTADA] El tiempo de viaje se pide con hora de salida

Se manda `depart_at` en cada llamada a Matrix, con el **punto medio de la parada** (no del slot
candidato). Mapbox responde con el tráfico previsto para esa fecha y hora según 90 días de
histórico.

**Por qué:** sin hora de salida, la respuesta son velocidades típicas sin hora del día — un error
del mismo tamaño que el presupuesto de 20 min que se está midiendo. Un trayecto de 15 min a
mediodía pasa de 20 en hora pico, y ese es exactamente el caso que el predicado tiene que
distinguir.

**Lo que NO cambia:** el costo sigue sin depender del número de slots. La hora de salida sale de
la parada, no del horario candidato. Un día cuesta una llamada por **franja con paradas**: 2 en
un día mediano, 4 en el día pico. El ancho de franja son 3 h, medidas (§5.3.3), y es parámetro.

**Descartado:** `driving-traffic` (mezcla tráfico en vivo, que para una cita de dentro de tres
semanas es ruido, y limita a 10 coordenadas — por debajo del pico real de 10 paradas + destino)
y pedir matrices asimétricas para ahorrar elementos (los elementos están dentro del tramo
gratuito de Mapbox; lo que escasea son las peticiones).

> ⛔ **APAGADO POR DEFECTO — no se puede usar todavía (verificado el 21-ago-2026).**
> `depart_at` en Matrix es una BETA con alta previa; sin ella Mapbox devuelve **422 y tira la
> petición entera**, no solo el parámetro. Como todas las llamadas lo llevaban, el filtro quedó
> **inerte en silencio** — pasaba por degradación y no lo era.
>
> **Corregido el 24-ago-2026 apagándolo de raíz** (`visar.travel.depart_at = 0`), no dejándolo
> en `auto`: el default tiene que ser el comportamiento que se ha visto funcionar contra la base
> real, no uno que depende de que un apagado automático salga bien. Apagado, el camino es el de
> `825d536` **exacto**: una llamada por (día, técnico), sin franjas, clave sin franja, tope 12.
>
> El reintento-sin-hora sigue disponible con `auto`. **El día que concedan la beta:
> `visar.travel.depart_at = 1` Y `visar.travel.matrix_max_calls = 30`, los dos.**

## [DECIDIDA — 17-ago-2026, NO IMPLEMENTADA] El CP se pide temprano

Se pide justo después del servicio, para (1) rechazar fuera de cobertura en la segunda pregunta
y no después de seis, y (2) **precalentar** zona, pools, agenda y matrices de viaje mientras el
cliente contesta el resto. Es tiempo gratis.

> ⛔ **Sin implementar**, y desde el 21-ago-2026 **sin objeto**: la razón que le quedaba —*"es la
> salida probable para desbloquear la rama de valoración (I-17)"*— murió cuando I-17 se cerró por
> otro camino. Hoy la dirección se sigue pidiendo al final. Si se retoma, que sea por sus dos
> méritos propios (rechazar fuera de cobertura temprano y precalentar), no por I-17.

## [DECIDIDA — 17-ago-2026] Los `items` NUNCA se arman a mano

Siempre por `_visar_resolve_wizard_items(selections)`. Los métodos RPC reciben `selections`,
nunca `items`. Es lo que garantiza que el total del agente sea, por construcción, el del web.

## [IMPLEMENTADO — 18-ago-2026] Hand-off humano: lead + chatter + actividad asignada

Antes el agente decía "en seguida te contacta un asesor" y **no pasaba nada más**: era la falla
del sistema manual —el contexto se pierde, nadie da seguimiento— reproducida dentro del sistema
nuevo, y encima con una promesa explícita al cliente.

> ⛔ **Bloqueado por dato:** el equipo de CRM de WhatsApp no tiene líder ni miembros, así que la
> actividad se crea y **no cae en la bandeja de nadie**.

## [VIGENTE] El pago sigue SIMULADO; Stripe llega después

No bloquea: se construye y prueba con el proveedor *Demo*. Pero la UI/UX contempla **pago
rechazado** y **pago pendiente** desde el primer día, y el apartado **se congela mientras haya
una transacción en vuelo** — con Stripe una transacción puede quedar `pending` (3-D Secure,
SPEI/OXXO), y soltar el horario a mitad del cobro sería el peor caso posible.

## ~~[ABIERTA]~~ [CERRADA — 21-ago-2026] La rama de valoración no cerraba

> Esta entrada seguía marcada **[ABIERTA]** diez días después de resolverse. Corregida el
> 31-ago-2026.

**Contradecía la decisión 3 del doc 33 §12** ("valoración: SÍ la maneja el agente, hasta el mismo
paso de horarios"): `valuation` era terminal, nunca se preguntaba la dirección, así que no había
zona, ni técnicos, ni un día que ofrecer. Tocaba justo a los clientes que están peor —**termitas,
chinches y "no sé qué es"** son los tres cortes a valoración.

**Ya no.** `valuation` es un paso que se **acusa** (precio + motivo, una opción) y sigue al de
dirección. Se acotó al chat con `valuation_inline`, bandera que pone solo `agent_booking_step`,
así que el web no cambió. Sus items salen de `_visar_wizard_valuation_items()`. De paso cerró un
bug de cobro que solo se veía con la rama abierta: en el corte **mixto**, la pantalla de revisión
cotizaba interior mientras `agent_prepare_booking` cobraba la valoración. Ver §10.7 y §(a) del
doc 33, e I-17.

## [APRENDIDO — 27-ago-2026] Un `try/except` alrededor de una consulta NO protege la transacción

Los lectores de `visar.agent.prompt` tienen que ser **incapaces** de levantar: si
`agent_runtime_config` falla y el runtime aún no tiene nada cacheado,
`RuntimeConfigCache.refresh` re-lanza y el servicio **deja de contestarle a
todos**. Se envolvieron en `try/except Exception`… y no bastaba.

Comprobado sobre una copia de producción **sin** correr el `-u`, que es
exactamente el estado que hay entre desplegar el código y actualizar el módulo:

```
RESULTADO: REVENTO -> InFailedSqlTransaction
           current transaction is aborted, commands ignored until end of transaction block
```

El `SELECT` sobre la columna `ruta`, que todavía no existe, falla y deja la
transacción **abortada**. El `except` devuelve `None` como se pedía, pero a partir
de ahí **cualquier** consulta siguiente revienta — incluida la de
`visar.llm.config`, que no tenía guardia — y el método levanta igual. La defensa
protegía la línea, no el turno.

La forma correcta en Odoo es el **savepoint**:

```python
with self.env.cr.savepoint():
    record = self.search(...)
```

La consulta fallida se deshace sola y la transacción sigue sirviendo. Con eso, una
base sin actualizar degrada como debe: `prompt: None`, `route_prompts: {}`, y el
runtime cae a su `BASE_PROMPT` de respaldo.

> **Regla que sale de esto:** "no puede levantar" en un método que corre dentro de
> una transacción de Odoo significa **savepoint**, no `try/except`. Y la forma de
> saberlo no es razonarlo: es restaurar una copia en el estado anterior y llamar
> al método.
