# App de Campo — `visar_field_app`

> **Estado:** documentado por primera vez el **02-jul-2026**. Este módulo es el **cuarto**
> del proyecto y **no aparecía** en el mapa de carpetas de `00-overview.md` ni en
> `20-architecture.md` (que describen solo `visar_base → visar_fsm → visar_appointment`).
> Es más nuevo que el resto de `.context/` (última actualización general: 26-jun-2026).
>
> **Qué requerimiento cubre:** es la **mitad pendiente de D-07** — la parte "UI técnico" y
> "worksheet / fotos / firma" que `50-status-roadmap.md` marcaba como `[ ]`. Se resolvió como
> **app web separada que escribe en la worksheet NATIVA**, no con `worksheet.template` propias
> como asumían los docs originales. Mismo objetivo, ruta distinta.

---

## 🆕 Actualización — 07-jul-2026 (captura enriquecida)

> Trabajo posterior al documento original. **Varias ⚠️ de abajo ya NO aplican.** El renderizador
> de la worksheet dejó de ser "v1 plana": ahora respeta widgets, pestañas invisibles y campos
> relacionales.
>
> ⚠️ ~~El manifest sigue en v19.0.1.0.1~~ **[Actualización 08-jul (bis)]** — ya está en
> **v19.0.1.2.0** (bumps del mapa y del sembrador).

### Renderizador de worksheet — ahora soporta

El reflejo de la vista formulario nativa (`_worksheet_descriptors` y helpers) evolucionó de un
`iter('field')` plano a un **recorrido que respeta la jerarquía**:

| Capacidad | Cómo | Constante / método |
|---|---|---|
| **Salta `<header>`** (barra de estado / botones) | no es captura | `_collect_field_nodes` |
| **Respeta invisibilidad de ANCESTROS** | campos en `<page>`/`<group>` con `invisible="1"` NO se muestran | `_node_is_invisible` + recorrido |
| **Omite por widget** | `statusbar` (barra de etapas Studio) y `signature` (se captura en la sección firma nativa) | `WORKSHEET_SKIP_WIDGETS` |
| **Omite nombre redundante** | `…nombre_de_quien_firma` (duplicado del canvas de firma) | `WORKSHEET_SKIP_NAME_HINTS` |
| **`one2many` → tarjetas dinámicas** | patrón "Fotos": "+ Agregar" clona una tarjeta inerte (inputs `disabled`, placeholder `__IDX__`), un solo POST sincroniza todo (crea/actualiza/elimina por conjunto) | `_o2m_descriptor`, `_sync_worksheet_lines`, plantilla `o2m_card` |
| **`many2many` → grupo de casillas** | opciones del comodelo; escritura `(6,0,[ids])` vía `request.httprequest.form.getlist` | rama m2m en `_scalar_descriptor` + `_worksheet_write_values` |
| **Imagen por tarjeta o2m** | `binary` en línea → input file (`capture=environment`) + **miniatura** servida por ruta propia | `LINE_SKIP_TYPES` (ya NO incluye binary) |
| **Ayuda por campo (ⓘ)** | tooltip que Studio guarda en el **nodo de la vista** (`node.get('help')`), NO en el campo del modelo (`fields_get` casi siempre vacío); botón "ⓘ" que alterna el texto (móvil-friendly) | `field_help` (plantilla) + `initHelp` (JS) |

> **`WORKSHEET_SKIP_TYPES` sigue existiendo** pero solo se usa en el *fallback* `fields_get`.
> En el camino normal, o2m se renderiza y m2m también. **La ⚠️ de "omite o2m/m2m" ya no aplica.**

### Sincronización de líneas o2m (`_sync_one_o2m`) — reglas

- **Regla de conjunto:** las filas enviadas = estado deseado. Se eliminan solo las líneas
  **originales** no reenviadas (`valid_ids − submitted_ids`); las **recién creadas nunca** se
  borran (bug corregido: antes el cleanup las eliminaba por no estar en `submitted_ids`).
- **Marcador `o2mpresent~{campo}`** distingue "subficha vaciada" de "subficha no incluida".
- **`x_name` autorrellenado** al crear (modelos de línea de Studio lo traen `required`/NOT NULL):
  primer valor char/text/selection de la tarjeta, o `"{etiqueta} {n}"`.
- **Binary de línea:** solo se escribe si llega archivo nuevo (preserva la foto previa).
- **Seguridad:** un `line_id` reenviado debe pertenecer a `record[o2m].ids` (evita escribir líneas ajenas).

### Rutas nuevas (además de las de la tabla de más abajo)

| Ruta | Método | Qué hace |
|---|---|---|
| `POST …/task/<id>/photo/<att_id>/delete` | http (csrf) | Borra una foto **subida por el técnico** (adjunto con `res_field=False`), acotada a la tarea. **No** toca adjuntos respaldados por campo (firma, binarios de worksheet). UI: tocar la foto revela una "×". |
| `GET …/task/<id>/line-image/<line_id>/<field>` | http | Sirve la foto de UNA línea de subficha (binary del modelo de línea). Valida: tarea del técnico + línea de ESA worksheet + `field` es binary declarado. Sniff PNG/JPEG. |
| `POST …/task/<id>/worksheet` | (ampliada) | Ahora además sincroniza subfichas o2m (`_sync_worksheet_lines`) y m2m. |

> **Bug latente corregido:** la lista de Fotos ahora filtra `res_field=False` — antes la **firma
> nativa** (`worksheet_signature`, adjunto imagen sobre `project.task`) podía aparecer y borrarse
> desde la sección Fotos.

### Configuración y asignación de plantillas (worksheet.template)

- **Dónde se definen:** app **Servicio externo → Configuración → Plantillas de hoja de trabajo**
  (`worksheet.template`, de `industry_fsm_report`/`worksheet`). Los campos son un modelo dinámico
  `x_…` editado con **Studio** ("Design Template" = `open_studio_button`, requiere Enterprise).
- **Cómo llega a la app:** la app lee `project.task.worksheet_template_id`. Ese campo lo hereda
  la tarea del **proyecto** (`project.project.worksheet_template_id`) o se fija por tarea. Como
  `visar_fsm` crea **una tarea por proyecto**, el reparto real es **por proyecto**: p. ej. el
  servicio *Fumigación interior + exterior* (producto → `project_id`) usa el proyecto **"Fumigación"**;
  cambiar la plantilla de ese proyecto cambia las tareas **nuevas** (las existentes conservan la suya).
- **Sembrado por XML/código:** posible pero con matiz — la `worksheet.template` se puede crear por
  XML (Odoo lo hace), pero **los campos** viven en un modelo dinámico cuyo nombre lleva el id del
  template (`x_project_task_worksheet_template_<id>`), así que se siembran por **Python** (crear
  `ir.model.fields state=manual` + reescribir el arch de la vista) o por **export de Studio**.

### Nueva plantilla construida — "Fumigación interior o exterior (App v2)"

Plantilla `worksheet.template` **id 16** en `visar_prod`, creada por script Python (no Studio),
como reemplazo actualizado del template #9. Notebook de 3 pestañas (Inspección inicial / Ejecución /
Cierre), con: casilla, selección, **many2many** (Factores de riesgo, modelo de tags `x_visar_factor_riesgo`),
imágenes, texto, y subficha **one2many** "Áreas tratadas" (modelo `x_visar_area_tratada_v2`, con foto
por tarjeta). El antiguo `x_comments` se relabeló a **"Observaciones finales del técnico"** + ayuda.

> **[Actualización 08-jul (bis)]** — ya **NO** vive solo en la BD: el builder se consolidó en
> `hooks.py::seed_worksheet_templates` (idempotente) + `post_init_hook` + migración. Ver la sección
> "🆕 Actualización — 08-jul-2026 (bis)" arriba. También se le agregó una **segunda** plantilla,
> "Mantenimiento de áreas verdes (App v2)".
>
> **Bugs del builder (corregidos):** `x_name` NOT NULL en modelos de línea; m2m que **desaparecía**
> por nombre de tabla-relación autogenerado demasiado largo (fix: `relation_table`/`column1`/`column2`
> explícitos y cortos); API `Registry._setup_models__` (no `setup_models`) — pero `ir.model[.fields].create`
> ya auto-refleja (setup + init de columnas), no hace falta llamarla a mano.

### Brechas que siguen abiertas

- **Requerido / condicional-OBLIGATORIO NO se aplica en la app.** Los `required="1"` y las reglas
  condicionales (dosis si hay plaguicida; foto si el área se marcó tratada) se codifican en los nodos
  de la vista (Studio/reporte nativo los honran), pero la app **no bloquea** el cierre por ellos.
  **[Actualización 08-jul (bis)]** — la **visibilidad** condicional SÍ se implementó (campos
  companion "Otro"); lo que falta es el **bloqueo** por requerido/condicional (I-05).
- **Binary dentro de subficha o2m anidada** no soportado (raro). ~~m2m dentro de tarjetas o2m~~
  **[RESUELTO 08-jul (bis)]** — m2m sí se soporta en tarjetas (grupo de casillas).
- Sigue en pie lo de "Deuda técnica conocida" que no marcamos como resuelto abajo (forms separados,
  sin validación de cierre, sin offline, PIN texto plano, "servicios de hoy" sin filtro de fecha).

---

## 🆕 Actualización — 08-jul-2026 (mapa de servicios + geocodificación)

> Cubre la **parte 1 del pliego de la app de campo**: ver los servicios en un **mapa** y
> **"Abrir en Google Maps"** desde el detalle. Esta tanda bumpeó el manifest y añadió la
> **dependencia `base_geolocalize`**. La parte 2 (etapas En camino/En ejecución/…, cronómetros,
> contacto del cliente, alarma "esperando cliente") sigue **pendiente** (plan aparte).
>
> ⚠️ **Nota de versión:** el manifest va en **v19.0.1.2.0** con `post_init_hook='post_init_hook'`.
> Ese hook (y parte del bump) son de **otra tanda del 08-jul** — el **sembrador de plantillas**
> (`hooks.py::seed_worksheet_templates`), documentado en la sección "🆕 Actualización — 08-jul-2026
> (bis)" más abajo. No tiene que ver con el mapa.

### Por qué "mapa propio" y no el nativo de Field Service

El mapa nativo (`Servicio externo → Mis tareas → Mapa`, módulo `web_map`) es una **vista OWL
de backend**: requiere usuario interno con login, que los técnicos **no tienen** (app por PIN,
pública). Además `web_map` **ya es Leaflet** (empaqueta su propia copia) y solo usa **Mapbox si
hay token** (`ir.config_parameter web_map.token_map_box`); en `visar_prod` ese token está
**vacío**, así que el mapa nativo hoy corre sobre **OSM igual que el nuestro**. Por eso se
**replica** el stack (Leaflet + OSM) en la superficie pública en vez de reusar la vista. Lo que
SÍ se reutiliza: (a) el **almacén de coordenadas compartido** (`res.partner.partner_latitude/
longitude` — geocodificar en cualquiera de los dos mapas beneficia al otro); (b) el **mismo
token Mapbox** (`web_map.token_map_box`) para geocodificar.

### Mapa de servicios (Leaflet + OpenStreetMap)

- Alternador **Lista ⇄ Mapa** en `field_tasks` (por defecto **Lista**). Un solo request: lista
  y mapa conviven en la misma página; el JS alterna paneles (`d-none`). No hay ruta nueva.
- Un **marcador por servicio geocodificado**; popup con nombre + enlace al detalle
  `/visar/field/task/<id>`. Encuadre a todos los marcadores (`fitBounds`, `maxZoom 16`).
- Leaflet **vendorizado** en `static/src/lib/leaflet/` (js/css + imágenes de marcador),
  añadido a `web.assets_frontend`.
- **Gotchas resueltos (JS):** (1) NO usar `L.Icon.Default` — antepone su `imagePath` a la URL
  y **duplicaba la ruta → 404**; usar `L.icon()` con URLs absolutas al módulo. (2) El contenedor
  arranca `d-none`: Leaflet mide 0 oculto, así que `invalidateSize()` **y** `fitBounds()` se
  **re-ejecutan al mostrar** la pestaña Mapa (si no, abre desencuadrado / muy alejado).
- Coordenadas: `_task_map_payload` (controlador) pasa `map_tasks_json` a la plantilla como
  atributo `data-tasks`; `has_coords` = lat/long no nulos (`0.0/0.0` = sin geocodificar). Por
  **decisión de negocio se plotea también el centroide** (aprox) cuando la calle no resuelve.

### "Abrir en Google Maps" (detalle)

`_google_maps_url(task)` arma `https://www.google.com/maps/search/?api=1&query=<dir>` con la
dirección de servicio; botón en `field_task_detail` (solo si hay dirección). En móvil abre la
app de Maps. Sin API key.

### Geocodificación — dirección de SERVICIO, no la del cliente de facturación

- **La dirección de servicio es `task.partner_id`** (contacto tipo `delivery`/obra), **no** el
  cliente de facturación (ese es el partner **padre**). Confirmado con datos: cada tarea apunta
  a su propio contacto de entrega, con su calle. El mapa y la geocodificación usan esa.
- Método `res.partner._visar_geo_localize()` (nuevo `models/res_partner.py`), **por precisión**:
  1. **Mapbox** si hay token `web_map.token_map_box` (**server-side**; el token NO se expone en
     la página pública). Mismo endpoint forward v5 que el mapa nativo.
  2. **OSM** (`base_geolocalize`) con **consulta enriquecida**: incluye colonia (`street2`) y
     estado, y limpia ruido de número (`"No. 8707"` → `"8707"`); **fallback al centroide de CP**.
  Devuelve `'exact'` (nivel calle) / `'approx'` (centroide) / `False`.
- Disparador: **acción de servidor + menú** *App de Campo Visar → "Geolocalizar direcciones de
  clientes"* (`hr.group_hr_user`, `views/geolocalize_action.xml`) → llama
  `project.task._visar_geolocalize_service_partners(force=False)`. Por defecto solo procesa los
  que **no** tienen coordenadas; `force=True` re-geocodifica todos. La notificación reporta
  cuántas resolvieron a nivel calle vs. solo centroide.
- **Tiles en OSM (no Mapbox) a propósito:** la página del técnico es **pública**; poner el token
  en el JS para tiles lo expondría. Se **geocodifica con Mapbox (server-side)** y se **dibuja con
  tiles OSM** (gratis, sin exponer token ni pagar map-loads). Cambiar tiles a Mapbox es un extra
  pendiente si se acepta exponer un token público (restringible por URL).

### Lección de geocodificación (OSM vs. Mapbox)

OSM Nominatim tiene **mala cobertura de calles residenciales MX** y **colapsa** las direcciones
no resueltas al **centroide del CP** (dos calles del mismo CP → mismo punto — el síntoma que
disparó este trabajo). Pero una dirección **real + consulta bien formada SÍ resuelve gratis en
OSM** (probado: *Palo Blanco 8707* pasó de centroide a la calle real solo al mejorar la query;
la query por defecto de `base_geolocalize` la degradaba por omitir colonia/estado y dejar el
`"No."`). Direcciones falsas/no mapeadas nunca resuelven. Para máxima precisión sin depender de
la calidad del dato: **Mapbox** (token compartido con el mapa nativo) o Google
(`base_geolocalize.google_map_api_key`).

### Archivos tocados

- `models/res_partner.py` (**nuevo**) — `_visar_geo_localize` + helpers Mapbox/OSM.
- `models/project_task.py` — `_visar_geolocalize_service_partners(force=False)` (+ notificación).
- `controllers/main.py` — `_google_maps_url`, `_task_address`, `_task_map_payload`; contexto
  `maps_url` (detalle) y `map_tasks_json`/`geocoded_count` (lista).
- `views/field_app_templates.xml` — alternador Lista/Mapa + contenedor `#visar-field-map`; botón
  "Abrir en Google Maps".
- `views/geolocalize_action.xml` (**nuevo**) — `ir.actions.server` + `menuitem`.
- `static/src/js/field_app_map.js` (**nuevo**), `static/src/lib/leaflet/*` (vendor Leaflet 1.9.4),
  `static/src/css/field_app.css` (estilos `#visar-field-map`).
- `__manifest__.py` — bump de versión + dep `base_geolocalize`, assets Leaflet + mapa, data
  `geolocalize_action.xml`.

### Pendientes / notas

- La acción solo procesa **faltantes**; para **re-geocodificar** los ya guardados (subir de
  centroide OSM a calle Mapbox) hace falta `force=True` (aún sin entrada de menú "re-geolocalizar
  todo").
- Los contactos de entrega de prueba **no traen `state_id`** (baja la precisión); conviene que
  el flujo de reserva capture estado.
- La **ruta Mapbox de éxito quedó sin probar en vivo** (falta token real): validada la selección
  de proveedor y el fallback *Mapbox-error → OSM*.
- **Requerimiento 2** (etapas + cronómetros + contacto + alarma "esperando cliente") **pendiente**.

---

## 🆕 Actualización — 08-jul-2026 (bis) — plantillas v2, "Otro" condicional y **sembrador**

> Segunda tanda del 08-jul (posterior al mapa). Endurece el renderizador de worksheet y, sobre
> todo, **convierte las plantillas construidas por script en un sembrador versionado** dentro del
> módulo. Manifest **v19.0.1.2.0** con `post_init_hook`. Resuelve la ⚠️ de la sección de mapa que
> decía que el hook/bump "no estaba documentado".

### Renderizador — nuevas capacidades (encima de las de 07-jul)

- **many2many EN tarjetas o2m** (antes solo en campos principales): `LINE_SKIP_TYPES` ya solo
  excluye `one2many`. La escritura de m2m de línea lee `request.httprequest.form.getlist('o2mline~…')`.
  **Reemplaza** la ⚠️ 07-jul de "m2m dentro de tarjetas o2m no soportado".
- **2 columnas por grupo anidado:** un `<group>` con 2 `<field>` dentro del `<form>` de la subficha
  se renderiza como **fila de 2 columnas**. `_o2m_line_rows` produce *filas* (lista de listas), la
  tarjeta las pinta (`col` cuando la fila tiene 2). Autoría en la vista, **no** automático por tipo.
  (Uso actual: Plaguicida nombre + dosis.)
- **Campo condicional "Otro":** un campo companion `{base}_otro` (label "Especifique cuál otro") se
  muestra **solo** cuando el campo base tiene "Otro"/"Otros" elegido — sirve para **selección y
  many2many**. Detección por convención de nombre (`_otro_conditional`: busca la opción/etiqueta
  que empieza con "otro"); el companion se emite oculto (`o_visar_condfield d-none`) con atributos
  `data-showif`/`-kind`/`-val`, y un handler JS **delegado** (`evalCondFields`) lo revela al cargar
  y en cada `change` (funciona también en tarjetas clonadas). El companion se coloca **justo
  después** del base en el arch (si el base está en un grupo-par, va tras el grupo).
- **Polish:** etiquetas de tarjeta sin `small` (igualan a las principales), más espaciado
  (`card p-3`, campos `mb-3`), botón "×" de foto ahora **círculo perfecto** (cuadrado fijo +
  flex-center), y el **helper ⓘ del o2m** junto al título en negritas.

### Sembrador de plantillas — `hooks.py` (la fuente de verdad)

- `seed_worksheet_templates(env)` **idempotente** reconstruye AMBAS plantillas al **estado final**
  (no reproduce los scripts incrementales; escribe el arch canónico directo): registros,
  modelos dinámicos, modelos de línea (`x_visar_area_tratada_v2`, `x_visar_labor_jardineria`),
  modelos-etiqueta (`x_visar_plaga` = 7, `x_visar_factor_riesgo` = 10), m2m de plaga, grupo par de
  plaguicida, companions "Otro", helpers de subficha y `x_comments` relabeleado.
- **Cableado triple** (para que caiga en prod pase lo que pase):
  1. `post_init_hook='post_init_hook'` (manifest) → install limpio siembra solo.
  2. `migrations/19.0.1.2.0/post-migrate.py` → **upgrade** de un módulo ya instalado siembra solo
     (convención de migraciones del proyecto).
  3. Manual por shell: `from odoo.addons.visar_field_app.hooks import seed_worksheet_templates;
     seed_worksheet_templates(env); env.cr.commit()`.
- **Por qué Python y no XML puro:** el modelo dinámico lleva el id del template en su nombre, así que
  los **campos** no se pueden declarar en XML (ver 07-jul "Sembrado por XML/código"). Cierra **I-06**.
- ⚠️ **Idempotente ⇒ reescribe el arch** al canónico: **ediciones en Studio sobre prod se pierden**
  en el próximo seed/upgrade. El código es la fuente de verdad de **estas dos** plantillas.
- ⚠️ Ruta de **install limpio verificada por composición** (cada pieza ya se probó al crear 16/18 y
  el camino idempotente está verificado), **no** en BD vacía → probar `-i` limpio antes de prod.
- **Bugs corregidos esta tanda:** el descriptor o2m no traía la clave `conditional` → KeyError al
  renderizar (agregada + plantilla endurecida con `.get`); companions insertados en el subview
  `<list>` en vez del `<form>` (el `iter('field')` global tomaba el list primero) → se opera sobre
  el `<form>`.

### Plantillas que siembra

- **"Fumigación interior o exterior (App v2)"** — 3 pestañas; **plaga = multiselección**
  (Voladores/Rastreros/Roedores/Termitas/Polilla/Chinches/Otros), Plaguicida nombre+dosis en 2
  columnas, companions "Otro" en área/plaga/plaguicida/acción y en Factores de riesgo (main).
- **"Mantenimiento de áreas verdes (App v2)"** — 3 pestañas; subficha "Labor de jardinería"
  (tipo de servicio + companion "Otro", ¿se completó?, observaciones).
- **Asignación sigue siendo manual:** sembrar solo **crea** las plantillas; hay que apuntar el
  proyecto correcto (`project.project.worksheet_template_id`) — no se automatiza.

### Pendiente de esta tanda

- **#2 Fotos múltiples por campo** (galerías de adjuntos por campo-imagen + **eliminar la sección
  Fotos externa**): **NO implementado**; es el último punto del batch de cambios de plantilla.
  Plan acordado: galerías "vivas" (adjuntos etiquetados por campo lógico, vía un campo nuevo en
  `ir.attachment`) para campos-foto principales, y multi-file-al-guardar para la foto de tarjeta.
- **Requerido/condicional-obligatorio:** la app ya hace **visibilidad** condicional (companions
  "Otro"), pero sigue **sin bloquear** el cierre por `required` ni por reglas condicionales (I-05).

---

## 🆕 Actualización — 08-jul-2026 (Req 2: etapas + timers + contacto + validación de cierre)

> Flujo en sitio del técnico: `Voy en camino → (Confirmar llegada) → (Esperar al cliente ⏱) →
> Comenzar servicio → Cerrar servicio`, más rama `Cliente no llegó → reagenda`. **Reutiliza las
> etapas NATIVAS de Field Service y el timesheet NATIVO**, no un estado propio. Requiere `-u`
> (campos nuevos).

### Decisión de diseño: reusar lo nativo (no reinventar)

- **Estado = etapas nativas `project.task.stage_id`** (las de `industry_fsm`, relabeladas es_MX por
  Visar), resueltas por **xmlid estable** `env.ref('industry_fsm.planning_project_stage_N')`
  (1=En camino, 2=En ejecución, 3=Completado, 4=Incidencia—Reprogramar; 0=Programado). **No** hay
  `visar_field_status`. Así el kanban/Gantt del backend queda sincronizado. (Hay una 6ª etapa de BD
  "Pendiente de firma" sin xmlid → **NO se usa** por decisión del negocio.)
- **Cronómetro de trabajo = timesheet NATIVO oculto.** No se usa el widget de cronómetro nativo
  (`timer.timer`) porque está **ligado a usuario** y su *Stop* abre un wizard — inservible en la app
  pública sin login. En su lugar, al cerrar se escribe una línea `account.analytic.line`
  (`_visar_write_service_timesheet`) con las horas `visar_service_start→cierre`, atribuida al
  **empleado** técnico. Todos los proyectos FSM tienen `allow_timesheets=True`.
- El **único cronómetro visible** es la espera al cliente (cuenta regresiva + alarma). El de trayecto
  ("en camino") se **difirió** (I-08).

### Flujo y botón contextual (uno a la vez)

| Botón | Etapa nativa | Efectos |
|---|---|---|
| Voy en camino | En camino (`stage_1`) | — |
| Confirmar llegada | **En ejecución (`stage_2`)** ⬅ salta directo | sella `visar_arrived_at`; muestra leyenda + opción de espera |
| Esperar al cliente | *(sigue En ejecución)* | sella `visar_waiting_start` + `visar_waiting_minutes`; cuenta regresiva |
| Comenzar servicio | *(sigue En ejecución)* | sella `visar_service_start` (cronómetro oculto arranca) + **registra `visar_client_wait_minutes`** |
| Cerrar servicio | Completado (`stage_3`) + `state='1_done'` | **exige firma + nombre**; escribe timesheet; atribución |
| Cliente no llegó | Incidencia—Reprogramar (`stage_4`) + `state='1_canceled'` | actividad + nota chatter; vuelve a la lista |

> **Cambio 08-jul (bis):** "Confirmar llegada" **salta la etapa FSM directo a *En ejecución*** (antes se
> quedaba en *En camino*), para que gestión vea "ejecutando" desde que el técnico llega. Las sub-fases
> de la app (`llego → esperando → en_ejecucion`) ahora **conviven dentro de la etapa En ejecución** y se
> distinguen por los sellos de tiempo. Al pulsar "Comenzar servicio" se guarda cuánto se esperó al
> cliente (`visar_client_wait_minutes` = ahora − `visar_waiting_start`; 0 si no se inició la espera).

`_task_flow_state(task)` es **primario por etapa** (`stage_id` manda → si gestión la cambia en el
backend, la app lo refleja); dentro de *En ejecución* refina la sub-fase por los sellos
(`service_start`→`en_ejecucion`, `waiting_start`→`esperando`, `arrived_at`→`llego`, sin sellos →
`en_ejecucion`). La plantilla muestra **un** botón principal por fase; "Cerrar servicio" es el form de
firma de siempre (abajo), visible solo en `en_ejecucion`. Estados terminales `cerrado`/`reagenda`
muestran una etiqueta.

### Sincronía de etapa app ↔ backend (`write` override)

Como el estado es la etapa nativa **y** hay sellos de sub-fase, un cambio de etapa hecho **a mano en
"Servicio externo"** debe reflejarse en la app. `project.task.write` (override en `models/project_task.py`)
detecta el cambio de `stage_id` y llama `_visar_reconcile_flow_markers`: si la etapa nueva **no es de
servicio** (≠ En ejecución/Completado) **limpia** los sellos de sub-fase (`visar_arrived_at`,
`visar_waiting_start/_minutes`, `visar_service_start`, `visar_client_wait_minutes`). Sin esto, al
revertir la etapa quedaban sellos obsoletos que "ganaban" y congelaban la app (timer/¡Tiempo!/reagenda
fantasma tras reabrir). Solo actúa cuando la etapa **cambia** de verdad (no en un guardado sin cambio),
y el `write` de limpieza no toca `stage_id` (no reentra). Cambios por **SQL directo** lo saltan (no es
ruta normal).

### Temporizador de espera (editable) + alarma

- En "llegó": **input numérico editable** (default = parámetro global; se guarda por tarea en
  `visar_waiting_minutes` al pulsar). Botón secundario/discreto "Esperar al cliente" + acción
  principal "Comenzar servicio". Leyenda: *"…tiene N mins para abrir tras ser notificado."*
- Cuenta regresiva **vanilla-JS** (`initWaiting`): `remaining = waiting_start + minutes − now`,
  recalcula al cargar (sobrevive recargas). Al expirar: **beeps WebAudio** + `navigator.vibrate` +
  banner rojo parpadeante + revela **"Cliente no llegó"**. Audio en móvil: se desbloquea el
  `AudioContext` con el primer toque tras la recarga (no hay asset de sonido).
- ⚠️ **Fix de huso (08-jul bis):** `waiting_start_iso` se pasa con sufijo **`'Z'`**
  (`task.visar_waiting_start.isoformat() + 'Z'`). Odoo guarda datetimes **naive en UTC**; sin el
  marcador, `new Date(...)` los interpreta como hora **local** y el cronómetro arrancaba desfasado
  por el offset del huso (p. ej. `360:59` en Monterrey UTC-6 para 1 min).
- Configuración: `ir.config_parameter visar_field.waiting_minutes` (default **10** por código,
  overrideable en Parámetros del sistema); helpers `_default_waiting_minutes` / `_coerce_waiting_minutes`.

### Reagenda ("Cliente no llegó") — señal a gestión, sin reagendar

`_visar_flag_reschedule(employee)`: etapa 4 + `state='1_canceled'` + atribución
(`visar_reschedule_requested_by_id/_at`) + **actividad** (`activity_schedule('mail.mail_activity_data_todo')`)
+ **siempre** una nota `message_post`. El técnico vuelve a su lista; **gestión** reagenda el calendario
en el backend. Asignado de la actividad: `user_ids → visar_sale_order_id.user_id (vendedor) →
project_id.user_id (PM)` — porque los técnicos **no tienen usuario**, `user_ids` suele estar vacío
(verificado: cae al vendedor).

### Validación de cierre

"Cerrar servicio" exige **firma Y nombre** — bloqueado en JS (submit) **y** en el servidor (redirige
con `?close_error=1`). Cubre parte de la deuda "Cierre sin validación".

### Contacto del cliente (detalle)

Bloque bajo el nombre / sobre la dirección: **teléfono + "Llamar" (`tel:`) + "WhatsApp" (`wa.me`)**.
`_task_contact`: en **Odoo 19 `res.partner` ya no tiene `mobile`** (solo `phone` + `phone_sanitized`);
el contacto de servicio (entrega) suele **no** tener teléfono → cae al cliente
(`commercial_partner_id`). Email omitido. (El módulo `whatsapp` está instalado → automatización futura.)

### Campos nuevos en `project.task`

`visar_arrived_at`, `visar_waiting_start`, `visar_waiting_minutes`, `visar_service_start`,
**`visar_client_wait_minutes`** (espera al cliente, min), `visar_reschedule_requested_by_id`,
`visar_reschedule_requested_at`. (Se reusan `visar_field_closed_by_id/_at`.) Todos se muestran
(readonly) en la pestaña **"Visar - Campo"** del formulario de tarea (`views/project_task_views.xml`).

### Rutas / archivos

- `POST …/task/<id>/status` con `action ∈ {enroute, arrived, waiting, start, reschedule}` (redirige al
  detalle; `reschedule` a la lista). `arrived` → etapa En ejecución; `waiting` guarda los minutos
  POSTeados; `start` calcula `visar_client_wait_minutes` y sella `service_start`.
- `field_task_close` ampliada (validación + etapa + timesheet). `field_task_detail` context:
  `flow_state`, `contact`, `waiting_minutes`, `waiting_start_iso` (con `'Z'`), `close_error`.
- `project.task.write` override (`_visar_reconcile_flow_markers`).
- `controllers/main.py`, `models/project_task.py`, `views/field_app_templates.xml`,
  `views/project_task_views.xml`, `static/src/js/field_app.js` (`initWaiting` + validación de cierre),
  `static/src/css/field_app.css`.

### Verificado (E2E contra `visar_prod`, 08-jul)

Flujo completo por HTTP; cierre → etapa Completado + `state=1_done` + **línea de timesheet** (empleado);
reagenda → etapa Incidencia + `1_canceled` + **actividad al vendedor** + nota; validación de cierre
bloquea sin firma/nombre; temporizador editable persiste (`visar_waiting_minutes`) y alimenta
`data-minutes`. La alarma sonora/vibración y el éxito de Mapbox solo se confirman en teléfono real.

> ⚠️ **Dato (no código): PIN duplicado.** `visar_field_pin='123'` está en **dos** empleados
> (Pedro Martínez id 2 **y** Administrator id 1); `_visar_field_find_by_pin` devuelve uno arbitrario
> (devolvió Administrator), así que la **atribución del cierre/timesheet fue no determinista**.
> Depurar PINs y (a futuro) forzar unicidad. Encaja con la deuda "PIN en texto plano".

### Pendiente

- **Tiempo de trayecto "en camino"** diferido → **I-08**.
- Etiqueta de lista "Mis servicios de hoy" sigue sin filtro de fecha; cierre sin validar
  worksheet/fotos (solo firma+nombre).

---

## Qué es

App web tipo **POS / `pos_hr`** para técnicos de campo:

- Los técnicos **no** necesitan usuario interno de Odoo (no consumen licencia).
- El dispositivo se identifica una sola vez; cada técnico abre su **turno** con un **PIN**
  (modelo `visar.field.session`).
- Cada técnico ve **solo sus** servicios (tareas FSM) filtrados por
  `project.task.visar_technician_ids` (empleados, no usuarios).
- Captura en campo escrita **directamente sobre la tarea nativa**: fotos (adjuntos),
  hoja de trabajo (worksheet nativa), firma del cliente y cierre.
- Atribución del cierre por técnico (`visar_field_closed_by_id`) para comisiones y auditoría.

**Principio de diseño (mismo que D-03/D-05):** portal/website + QWeb + controladores públicos,
**sin tocar el frontend OWL/Interactions nativo**. Ver la decisión "opción A vs B" en
`40-decisions.md` — este módulo la respeta.

**Ubicación / manifest:** `visar_field_app/__manifest__.py` — nombre técnico `visar_field_app`,
display "Visar - App de Campo (Técnicos)", **v19.0.1.4.0**, `application=True`.
(El trabajo Req 2 de 08-jul añadió campos nuevos sobre esta línea de versión; no forzó un bump propio.)

**Dependencias:** `visar_fsm`, `website`, `industry_fsm_report`, `base_geolocalize`.

---

## Modelos

### Propios

| Modelo | Archivo | Para qué |
|---|---|---|
| `visar.field.session` | `models/field_session.py` | Turno del técnico. Campos: `name` (computed), `employee_id`, `date_start`, `date_end`, `state` (`open`/`closed`), `note` (user-agent del dispositivo). Método `action_close()`. Se crea al login por PIN y se cierra al logout. |

### Extensiones

| Modelo | Archivo | Campos / helpers |
|---|---|---|
| `hr.employee` | `models/hr_employee.py` | `visar_field_pin` (protegido por `hr.group_hr_user`). Helper `_visar_field_find_by_pin(pin)` — busca empleado activo por PIN (en sudo desde el controlador público). |
| `project.task` | `models/project_task.py` | **Solo atribución del cierre:** `visar_field_closed_by_id` (empleado que cerró), `visar_field_closed_at`. **`visar_technician_ids` NO se define aquí** — vive en `visar_fsm` y esta app solo lo **consume**. |

> **Nota de dependencia crítica:** la app depende de que `visar_fsm` haya poblado
> `project.task.visar_technician_ids` con el empleado correcto. Ese enriquecimiento
> (`visar_fsm/models/sale_order_fsm.py::_visar_enrich_fsm_tasks`) copia los técnicos desde
> `calendar.event.appointment_resource_ids.visar_employee_id`. **Si el recurso de la cita no
> tiene `visar_employee_id`, la tarea queda sin técnico y NO aparece en la app** (hay que
> asignar el técnico a mano en la tarea). Ver `40-decisions.md` — "Técnicos = recursos, no
> usuarios" y su *pendiente operativo*.

---

## Controlador — `controllers/main.py`

Clase `VisarFieldApp(http.Controller)`. Todas las rutas son **públicas** (`auth='public'`) y
corren en **sudo**, pero acotadas al empleado de la sesión del dispositivo. Claves de sesión HTTP:
`visar_field_employee_id` y `visar_field_session_id`.

| Ruta | Método | Qué hace |
|---|---|---|
| `GET /visar/field` | http | Pantalla login por PIN (redirige a `/tasks` si ya hay sesión). |
| `POST /visar/field/login` | http (csrf) | Valida PIN → crea `visar.field.session` → guarda empleado/turno en sesión. |
| `POST /visar/field/logout` | http (csrf) | Cierra el turno abierto y limpia la sesión. |
| `GET /visar/field/tasks` | http | Lista de tareas del técnico (`_employee_tasks`). |
| `GET /visar/field/task/<id>` | http | Detalle del servicio: fotos, worksheet dinámica, firma. |
| `POST …/task/<id>/photo` | http (csrf) | Sube fotos como `ir.attachment` sobre `project.task`. |
| `GET …/task/<id>/image/<att_id>` | http | Sirve una foto de la tarea (acotada al técnico). |
| `POST …/task/<id>/worksheet` | http (csrf) | Guarda campos de la worksheet **nativa** (modelo dinámico `x_...`). |
| `POST …/task/<id>/close` | http (csrf) | Cierra: firma nativa + `state='1_done'` + atribución (`visar_field_closed_by_id`). |
| `GET …/task/<id>/report` | http | Renderiza el PDF **nativo** `industry_fsm.worksheet_custom`. |

### Filtro de tareas (`_employee_tasks`)

```python
domain = [('visar_technician_ids', 'in', employee.ids)]
if not include_closed:
    domain.append(('state', 'not in', ('1_done', '1_canceled')))
```

> ⚠️ **No hay filtro por fecha.** La UI dice "Mis servicios de hoy" pero el dominio muestra
> **todas** las tareas abiertas asignadas al técnico, no solo las de hoy.

### Worksheet dinámica (reflexión sobre el modelo `x_...`)

`_worksheet_*` leen la vista formulario nativa de la worksheet, construyen descriptores por campo
y coaccionan tipos al guardar. Enlace worksheet→tarea: `x_project_task_id`. Reporte nativo:
`industry_fsm.worksheet_custom`.

> ⚠️ **Limitación v1 (histórica):** omitía **`one2many` / `many2many`** y los protegidos.
> **[RESUELTO 07-jul-2026]** — o2m se renderiza como tarjetas dinámicas y m2m como casillas;
> también se respetan widgets, pestañas invisibles y ayuda por campo. Ver "🆕 Actualización" arriba.
> Sigue vigente: protegidos (`x_project_task_id`, `x_name`) y degradación a `fields_get` si la vista es ilegible.

---

## Flujo del técnico

```
GET /visar/field  ── PIN ──►  crea visar.field.session (turno abierto)
      │
GET /visar/field/tasks  ──►  tareas con visar_technician_ids ∋ empleado, state ∉ {done, cancel}
      │
GET /visar/field/task/<id>
      ├─ Subir fotos        → ir.attachment sobre project.task           (form propio)
      ├─ Guardar worksheet  → registro x_... nativo                       (form propio)
      └─ Cerrar servicio    → worksheet_signature nativo + state='1_done'
                              + visar_field_closed_by_id / _at            (form propio)
      │
GET …/task/<id>/report  ──►  PDF nativo industry_fsm.worksheet_custom
      │
POST /visar/field/logout  ──►  action_close() del turno
```

> **Los tres bloques de captura son formularios independientes.** "Cerrar servicio" **no** envía
> la worksheet ni las fotos: cada uno tiene su propio botón. Llenar la worksheet y pulsar "Cerrar
> servicio" sin "Guardar hoja de trabajo" **pierde** lo escrito en la worksheet.

---

## Frontend

| Archivo | Contenido |
|---|---|
| `views/field_app_templates.xml` | 3 plantillas QWeb (`t-call="website.layout"`): `field_login`, `field_tasks`, `field_task_detail` (Bootstrap; muestra `task.visar_sale_order_id` — campo de `visar_fsm`). |
| `static/src/js/field_app.js` | Pad de firma sobre `<canvas>` en **vanilla JS** (sin OWL). Al enviar el form de cierre, vuelca la firma a un `data-URL` en input oculto. |
| `static/src/css/field_app.css` | Estilos mínimos del canvas de firma y las fotos. |

## Seguridad — `security/ir.model.access.csv`

| Modelo | `base.group_user` | `hr.group_hr_user` |
|---|---|---|
| `visar.field.session` | solo lectura | CRUD completo |

- Rutas públicas + sudo, acotadas por el empleado de la sesión (cada handler revalida que la
  tarea pertenezca al técnico vía `_task_for_employee`).
- CSRF en todos los POST.
- PIN protegido por `hr.group_hr_user` en el backend; **comparación en texto plano, sin throttling**.

## Backend UI

- `views/menus.xml` — menú raíz **"App de Campo Visar"** (`hr.group_hr_user`, sequence 95) con
  "Abrir App de Campo" (`ir.actions.act_url` a `/visar/field`) y "Sesiones de técnicos".
- `views/field_session_views.xml` — lista + form del turno.
- `views/hr_employee_views.xml` — añade `visar_field_pin` tras el PIN nativo del empleado.
- `views/project_task_views.xml` — pestaña "Visar - Campo" con la atribución del cierre (readonly).

---

## Mapa reuso vs revamp

Para futuros cambios: qué es base sólida y qué es candidato a rehacer.

| Componente | Verdicto | Por qué |
|---|---|---|
| Identidad PIN + turno (`visar.field.session`) | 🟢 **Reusar** | Patrón POS limpio, sin licencia. Base sólida. |
| Scoping por `visar_technician_ids` | 🟢 **Reusar** | Es el **contrato** con `visar_fsm`. No romper esta interfaz. |
| Captura → campos NATIVOS (fotos, firma, cierre, atribución) | 🟢 **Reusar** | El acierto de diseño: los reportes nativos siguen funcionando. Mantener el principio. |
| Worksheet dinámica (reflexión) | 🟢 **Reusar (ya extendida 07-jul)** | Ya soporta o2m (tarjetas), m2m (casillas), imágenes por línea, widgets, pestañas invisibles y ayuda. Queda pendiente: requerido/condicional. |
| Frontend QWeb + POST/redirect (recarga completa) | 🔴 **Candidato a revamp** | Sin offline, sin validación cliente, forms separados. Mayor brecha para un app de *campo*. **Pero:** migrar a PWA/SPA **contradice** la decisión documentada (`40-decisions.md`); tratarlo como desviación consciente, no rewrite silencioso. |
| Seguridad (público + sudo) | 🟡 **Endurecer antes de prod** | PIN texto plano, sin throttling, `/report` sin límite. |
| Vistas/menús backend | 🟢 **Reusar** | Superficie admin fina, bajo riesgo. |

Todo lo verde es reusable **sea cual sea** la decisión sobre el frontend: un frontend reescrito
seguiría llamando al mismo contrato de controlador.

## Deuda técnica conocida (no bloquea hoy)

- **"Mis servicios de hoy" es inexacto** — `_employee_tasks` no filtra por fecha; muestra todas las
  abiertas. Agregar filtro o corregir la etiqueta.
- **Worksheet y cierre son forms separados** — cerrar sin guardar la worksheet pierde lo escrito.
- **Cierre sin validación** — **[PARCIAL 08-jul-2026]** ahora exige **firma + nombre** (JS + servidor,
  Req 2); sigue **sin** validar worksheet/fotos ni `required`/condicional (I-05).
- ~~**Sin campos relacionales en worksheet** (o2m/m2m)~~ — **[RESUELTO 07-jul-2026]** o2m (tarjetas) + m2m (casillas) + imágenes por línea. Nuevo pendiente: la app no aplica `required`/condicional.
- **PIN en texto plano, sin throttling y SIN unicidad** — en `visar_prod` el PIN `123` está duplicado
  en dos empleados → atribución no determinista (ver I-09). Aceptable en prototipo, no en producción.
- **Sin captura offline** — un `project.task` en `1_done` desaparece de la lista del técnico
  (`1_done ∈ CLOSED_STATES`); toda la operación asume conexión.

## Pendientes / decisiones abiertas

- Confirmar la ruta de la worksheet: ¿seguir con la app web sobre worksheet nativa, o volver a
  `worksheet.template` como asumía el D-07 original de `50-status-roadmap.md`?
- Definir si el frontend permanece server-rendered (decisión `40-decisions.md`) o migra a
  PWA/offline (necesidad real de campo que la decisión original no ponderó).
- **Reporte dual interno vs cliente** (D-07 pendiente) — hoy la app sirve un único PDF nativo.
- Endurecer identidad (hash de PIN, throttling) y añadir guardas de cierre si el negocio lo exige.
