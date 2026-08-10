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

## 🆕 Actualización — 27-jul-2026 — reportes por servicio, preview backend y solo-lectura en cerrados

> Tanda de reportes PDF + ciclo de vida de la hoja. **Cambio SOLO Python/plantillas**: los
> reportes y el candado son código y plantillas; la única parte que exige `-u` es la de campos
> nuevos (solo-lectura). Ver `60-odoo19-conventions.md` → "Versión del manifest": el manifest
> subió a **v19.0.1.14.0** SOLO por los campos nuevos; los cambios posteriores (preview inline,
> reportes de Áreas verdes/Valoración) son Python → **basta reiniciar**, sin bump.

### 1. Hoja de trabajo de servicios CERRADOS: solo lectura + "Habilitar edición"

- **Problema:** `WORKSHEET_STATES` incluye `'cerrado'`, así que un servicio Completado mostraba
  la hoja **editable** y el `POST …/worksheet` aceptaba cambios. La lista de la app deja ver los
  cerrados (decisión de negocio), pero no deberían editarse sin más.
- **Fix (cliente + servidor):** `_worksheet_locked(task, flow_state)` = `cerrado` **y** sin
  desbloqueo. La plantilla pinta los controles `disabled`, oculta "Guardar" / "+ Agregar" /
  subir-borrar fotos, y muestra un banner + botón **"Habilitar edición"**. El servidor rechaza el
  `POST …/worksheet` de una tarea cerrada no desbloqueada (**el flag vive en el servidor**, no en
  el form: un POST directo / pestaña vieja no lo burla).
- **Campos nuevos** en `project.task`: `visar_worksheet_reopened_at` / `visar_worksheet_reopened_by_id`
  (marca + atribución). Ruta nueva `POST …/worksheet/reopen` (`_visar_worksheet_reopen`, cualquier
  técnico asignado; nota en el chatter). **Se re-bloquea al guardar** (`_visar_worksheet_relock`,
  otra nota): cada edición exige volver a habilitarla. `_visar_reconcile_flow_markers` limpia el
  desbloqueo si la etapa sale de Completado (no queda desbloqueo obsoleto).

### 2. Botón "Reporte (PDF)" en la tarea (backend) — preview EN LÍNEA, no descarga

- Botón inteligente en `project.task` (junto a "Hoja de trabajo Completada" / "Previsualización
  del cliente"), `invisible` salvo `is_fsm`. Abre el **mismo** reporte que ve el técnico
  (`industry_fsm.task_custom_report`, enriquecido por la maqueta Visar).
- `visar_action_view_worksheet_report` devuelve una **`act_url` (pestaña nueva)** a
  `GET /visar/report/task/<id>/worksheet` (`auth='user'`, `check_access('read')`) que sirve el PDF
  con `Content-Disposition: inline` → el navegador lo abre en su visor y desde ahí se descarga
  (como la app de campo). `report_action` NO se usa porque **fuerza la descarga directa**.

### 3. Fix de galerías del reporte: mostrar TODAS las fotos (no solo la primera)

- **Bug:** las galerías de campos-foto PRINCIPALES (`x_foto_inicial`, `x_foto_ejecucion` en
  Fumigación) se consultaban sobre el **registro de worksheet** (`record._name`/`record.id`), pero
  la app las guarda como adjuntos de la **TAREA** (`project.task`/`task.id`, ver
  `field_task_ws_photo`). No encontraba nada → caía al binary del campo (la foto representativa) →
  **solo 1 foto**. La evidencia por-línea (área/zona) sí funcionaba (consultaba el registro de
  línea). Fix: `_visar_report_evidence_section` consulta `('project.task', self.id, …)`.

### 3b. Fotos WebP no se veían en el PDF (recuadro gris) — conversión con PIL directo

> Detectado en PROD (no en el clon): las fotos del task 196 estaban en **WebP**. En el
> PDF salían como un **recuadro gris con borde** (`<img>` con data-URI que el visor no dibuja).
> wkhtmltopdf 0.12.6.1 estaba OK (patched qt); el problema era el **formato** de la imagen.

- **Causa doble:**
  1. `odoo.tools.image.image_process` **omite WebP y SVG** (`RIFF…WEBPVP8` / `<`) y devuelve
     los **bytes originales sin tocar** → el reporte embebía un data-URI **WebP**.
  2. El **WebKit viejo de wkhtmltopdf (0.12.x) NO renderiza WebP** → recuadro gris. (Teléfonos
     y navegadores suben WebP muy a menudo; el test con JPEG nunca lo ejercitó.)
- **Fix:** `_visar_ws_report_image` ya **no** usa `image_process`; abre con **PIL directo**
  (`Image.open`), aplica `exif_transpose` (orientación), aplana alpha sobre blanco, reduce
  (`thumbnail`) y guarda **JPEG** sea cual sea el origen. Así el PDF siempre lleva JPEG.
- ⚠️ **Trampa de Pillow en Odoo:** Odoo llama `PIL.Image.preinit()` (registra solo
  BMP/GIF/JPEG/PNG/PPM) y `Image.open` **no** autocarga el resto **dentro del proceso Odoo**
  → abrir WebP lanza `UnidentifiedImageError` y la imagen se descartaba (silenciosa). Hay que
  **importar `PIL.WebPImagePlugin` explícitamente** (efecto de import; guardado con try) al
  cargar el módulo. Sin esto el fix "funciona en un python suelto pero no en Odoo". No se
  convierte en la subida: el fix en el reporte cubre lo YA capturado y lo futuro.
- Cambio **solo Python** → basta **reiniciar** en prod (sin `-u`).
- Verificado (clon, WebP real desechable + rollback): `image_process` devolvía WebP (`RIFF`); el
  helper nuevo devuelve JPEG (`\xff\xd8\xff`); `pdfimages` confirma las fotos embebidas como JPEG.
  Regresión OK: foto real, PNG con alpha (aplanado) y basura (`False` limpio).

### 4. Reportes de cliente por servicio (además de Fumigación)

`_visar_worksheet_report_sections` despacha por **nombre de plantilla** a una maqueta dedicada
(condensada + **toda la evidencia fotográfica en una sección al final**); el resto sigue el
recorrido genérico de la vista. Helper compartido `_visar_report_services_section` (tabla de
la orden). Constantes de nombre en `hooks.py` (`FUMIGACION_NAME`, `JARDINERIA_NAME`, `VISITA_NAME`).

> **Tabla de servicios = importes CON IVA (lo que paga el cliente).** El IVA de Visar es
> `price_include` (tax_included), pero **no de forma uniforme** entre órdenes: en unas
> `price_unit` ya trae el IVA, en otras no (el impuesto se suma). Mezclar `price_unit`
> (a veces con IVA) con `price_subtotal` (sin IVA) hacía que "Subtotal" saliera **menor** que
> unidad × cantidad (por el 16%). `_visar_report_services` usa ahora **`price_total`** (subtotal
> + impuestos, uniforme) para el importe y **deriva el unitario** del total (`price_total/qty`),
> así unidad × cantidad = importe = total. Columnas etiquetadas "(IVA incl.)". Verificado en las
> dos modalidades y con cantidad fraccionaria (0.25).

- **Mantenimiento de áreas verdes** (`_visar_jardineria_report_sections`): Servicios → Horario →
  **Labores realizadas** (tabla `x_labores`, pliega "Otro") → **Cierre del servicio** (indicaciones
  del cliente, área limpia, residuos + nº de bolsas, observaciones finales) → **Evidencia** (4
  galerías: Estado inicial / Resultado final / Residuos generados / Retiro de residuos, adjuntos de
  la tarea). **Omite** (interno): `x_solicitudes_adicionales` (lead comercial), `x_estado_equipo`
  (mantenimiento de equipos).
- **Visita de valoración técnica** (`_visar_visita_report_sections`): Servicios ($500) → Horario →
  **Resumen de la valoración** (tipo inmueble [pliega "Otro"], complejidad, superficie, nº de
  habitaciones, nº de visitas) → **Hallazgos y diagnóstico** (descripción, factores, resumen
  comunicado al cliente) → **Servicios recomendados** (`x_servicios_identificados`, m2m, pliega
  "Otro") → **Evidencia** (una galería POR ZONA etiquetada con `x_zona`, fotos por-línea
  `x_imagen_zona`). **Omite** (operativo/interno): `x_restricciones_acceso`, `x_materiales_especiales`.
- Helper `_visar_selection_label(record, field, otro_field)` = etiqueta del `selection` plegando
  "Otro" al companion.

> **Evidencia = TODAS las fotos.** Las galerías de campo principal se leen de la tarea; las de
> línea (área/zona), del registro de línea. Único tope: `_WS_REPORT_GALLERY_MAX = 12` por galería
> (salvaguarda anti data-URI gigante que corrompe wkhtmltopdf; los trabajos reales van muy por
> debajo). ⚠️ La evidencia FINAL de Fumigación aún topa a 12 **en total** entre áreas (pendiente si
> se quiere por-área).

> **Verificado (27-jul, BD clonada, registros desechables con rollback):** ambos reportes arman las
> secciones esperadas, pliegan "Otro", excluyen los campos internos y renderizan el HTML sin error;
> la valoración muestra las fotos de cada zona (2 y 1). Horario/Servicios aparecen solo con datos
> reales (llegada/cierre y línea de orden).

---

## 🆕 Actualización — 17-jul-2026 (ter) — PDF: fotos que no salían + render lento

> Dos problemas del reporte PDF, **ambos ajenos a las tandas Req 7/8** (uno de datos/entorno, otro un
> bug latente del renderizador de fotos). Manifest **v19.0.1.10.0** (cambio de método Python → basta
> **reiniciar** el server; no hay campos ni XML nuevos).

### A. "Ver reporte (PDF)" tardaba ~2 min (entorno, no código)

- **Causa:** `ir.config_parameter web.base.url = http://192.168.1.87:8069` (IP vieja de otra red) y
  `report.url` sin definir. wkhtmltopdf pide los **assets CSS del reporte** a esa URL; como la IP ya
  no es la de la máquina (ahora `192.168.6.181`), **cada fetch se cuelga hasta el timeout** (~5 s × N).
- **Medición:** QWeb HTML = **0.2 s**; wkhtmltopdf = **122 s** (todo el tiempo). El contenido no tiene
  la culpa.
- **Fix:** `report.url = http://localhost:8069` (alcanzable siempre). Render **122 s → 3.5 s**.
- ⚠️ **Es de la BD clonada**, no de prod (en prod `web.base.url` es el dominio real, alcanzable desde
  sí mismo). Si se re-restaura el dump vuelve el valor viejo → re-aplicar. NO poner localhost en prod.

### B. El reporte salía SIN fotos (bug latente en `_visar_ws_report_image`)

- **Causa (encoding):** `image_process` de Odoo trabaja con bytes **crudos** en ambos extremos, pero
  el campo binary llega en **base64** y `image_data_uri` (plantilla) también espera **base64**. El
  código pasaba base64 crudo a `image_process` → `UnidentifiedImageError` → la foto se **descartaba en
  silencio**. **Toda** foto real se caía (los tests con PNG 1×1 no lo delataban por invisibles). Fix:
  `b64decode` **antes** y `b64encode` **después**.
- **Causa 2 (render):** al embeber las fotos ya arregladas, un **PNG grande** (una captura/mapa pesa
  ~270 KB aun a 900 px) como data-URI **corrompe wkhtmltopdf**: la **primera página salía en blanco**
  (el texto desaparecía) aunque las fotos aparecieran en las siguientes. Fix: recomprimir a **JPEG**
  y bajar tamaño (`_WS_REPORT_IMG_PX=640`, `_WS_REPORT_IMG_QUALITY=70`) → data-URI de ~50-100 KB;
  documento estable. Verificado: página 1 completa (cabecera + horas + hoja) y páginas 2-3 con las
  dos fotos. PDF de 518 KB (PNG, pág. 1 rota) → **154 KB** (JPEG, todo OK).
- **Nota histórica:** el bug de encoding entró en la tanda "captura enriquecida" (07-jul) al añadir el
  reescalado; nunca se ejercitó con fotos reales, así que quedó latente hasta ahora.

---

## 🆕 Actualización — 17-jul-2026 (bis) — "Tiempo en sitio" en el PDF (Req 8)

> El reporte PDF muestra ahora el **tiempo que el técnico pasó documentando en el domicilio**:
> de **'Confirmar llegada'** a la **última** vez que pulsó **'Guardar hoja de trabajo'**. Manifest
> **v19.0.1.9.0** (campos nuevos → `-u`).

### El problema

El PDF ya traía **"Registro de horas"** (sección nativa *Timesheets*), pero eso muestra el
**tiempo trabajado** (la línea de timesheet que la app escribe al cerrar: `visar_service_start` →
cierre). No es lo que se quería. El dato pedido es **llegada → última guarda de la hoja**.

### Campos nuevos en `project.task`

- `visar_worksheet_last_saved_at` (Datetime) — se actualiza en **cada** guardado de la hoja (el
  `visar_worksheet_saved_at` existente sigue siendo el de la **primera** vez, para la etapa/auditoría).
- `visar_onsite_minutes` (Float, **compute stored**, `_compute_visar_onsite_minutes`) — minutos de
  `visar_arrived_at` → `visar_worksheet_last_saved_at` (0 si falta alguno). Stored para verlo/agrupar
  en el backend (pestaña "Visar - Campo").
- Al **resetear el flujo** (`_visar_reconcile_flow_markers`, etapa vuelta antes de la llegada) se
  limpia también `visar_worksheet_last_saved_at`.

### Reporte

- `report_worksheet.py::_get_report_values` agrega `visar_time_map = {task.id: {arrived, saved, duration}}`
  (de `task._visar_onsite_report()`), solo si hay **ambos** sellos. Los timestamps se formatean en el
  **huso del técnico** que documentó (`_visar_report_tz`: guardó → cerró → primer técnico → compañía);
  la duración con `_visar_format_duration` ("1 h 23 min" / "42 min" / "—").
- `report/worksheet_report_templates.xml`: plantilla nueva `visar_worksheet_report_onsite` que
  inyecta el bloque **"Tiempo en sitio"** (llegada, última guarda, **Duración** resaltada con el color
  de marca) **justo antes** de la sección nativa "Registro de horas", para que la diferencia entre
  ambos quede clara. Solo aparece si hay `visar_time_map` para la tarea.

### Verificado (17-jul, contra `visar_prod`, tarea desechable)

- **E2E 10/10:** el 2º guardado avanza `last_saved_at` y **no** toca `saved_at` (primera); con la
  llegada retrasada 42 min, `visar_onsite_minutes == 42`; el HTML del reporte incluye "Tiempo en
  sitio" con "42 min", "Confirmó llegada" y "Última guarda", y aparece **antes** de "Registro de horas".
- **PDF renderizado** (`_render_qweb_pdf`): el bloque se ve con el acento de marca, sobre la sección
  de horas nativa. (Nota: en la prueba el timesheet nativo salió 00:00 porque el cierre fue inmediato;
  en uso real muestra service_start → cierre — el dato *distinto* que ya existía.)

---

## 🆕 Actualización — 17-jul-2026 — obligatoriedad de la hoja de trabajo (Req 7, cierra I-05)

> La hoja de trabajo dejó de guardarse a medias: valida campos obligatorios en cliente (rojo +
> bloqueo) **y** servidor (defensa en profundidad). Manifest **v19.0.1.8.0** (`-u`; no hay campos
> nuevos, pero el arch de las plantillas se re-lee). **Resuelve I-05** ("bloqueo por requerido/
> condicional"). **No** cambia los campos de las plantillas: el reparto obligatorio/opcional YA
> estaba declarado en el arch (`required="1"`); lo que faltaba era **aplicarlo**.

### El hallazgo

Los `required="1"` de las tres plantillas son atributos de **nodo de la vista**, pero el renderer
solo miraba el `required` de **modelo** (siempre `False` en campos `x_` manual) → no exigía ni
mostraba nada. La obligatoriedad ya estaba pensada; solo estaba **muerta**.

### Fuentes de obligatoriedad (una sola verdad, cliente = servidor)

Cada descriptor de campo trae ahora `required` (bool) y `required_if` (dict o None):

| Regla | De dónde sale | Ejemplos |
|---|---|---|
| **Siempre obligatorio** | `node.get('required')` del arch (fuente de verdad; el reporte nativo también lo honra) | Fumigación: recorrido, nivel, fotos inicial/ejecución; línea: área, plaga, plaguicida |
| **Companion "Otro" obligatorio al mostrarse** | se deriva de su `conditional` (visible ⇔ obligatorio) | "Especifique cuál otro" cuando el select/casilla = Otro |
| **Condicional por disparador** | `WORKSHEET_REQUIRED_IF` (Python; el arch no lo expresa) | foto evidencia si `x_infestacion_activa`; foto+núm. bolsas si `x_residuos_embolsados` |
| **Subficha ≥ 1 línea** | `WORKSHEET_MIN_ONE` (Python) | `x_areas_tratadas`, `x_labores`, `x_zonas_evidencia` |

> **Decisión de negocio (16-jul, confirmada por el cliente):** min-uno en las **tres** subfichas;
> las tres reglas condicionales activas; el resto del reparto declarado se respeta sin promover más
> campos. La interpretación del help "obligatoria si 'Aplicado'" de la foto de evidencia se ató a
> `x_infestacion_activa` (no existe un campo 'Aplicado').

### UI (buenas prácticas de formularios)

- Asterisco rojo `*` en cada etiqueta obligatoria; en los **condicionales** el asterisco arranca
  oculto y el JS lo **revela cuando su disparador se cumple** (`refreshStars`, misma pista visual
  que ya usan los companion "Otro").
- Al intentar guardar con faltantes: **borde rojo + tinte + etiqueta roja** en el campo, mensaje
  inline debajo (`o_visar_field_err`), **scroll al primer error** y el submit se **bloquea**.
- No se regaña antes de tiempo: los errores se pintan al **primer intento** y de ahí en vivo.
- Snippets QWeb reutilizables `visar_field_app.req_star` / `req_error` (main y tarjetas o2m).

### Validación (misma lógica en ambos lados)

- **Cliente** (`initWorksheetValidation` en `field_app.js`): lee atributos `data-req` / `data-req-if[-kind|-val]`
  del wrapper (en tarjetas el controlador va calificado por fila, como `data-showif`); `data-min-one`
  en el o2m. Emptiness por tipo de control (foto = miniatura o archivo; m2m/booleano = alguna casilla;
  select = valor; texto = trim).
- **Servidor** (`_worksheet_validation_errors`, llamado en `POST …/worksheet` **antes** de escribir):
  reconstruye los descriptores y revalida. Si falta algo, **no escribe nada** (ni avanza la etapa) y
  redirige con `?ws_error=1` (banner). Es el mismo criterio; el cliente es la UX, el servidor la red.
- **Foto de línea condicional/obligatoria:** cuenta un archivo nuevo en el POST **o** una foto ya
  guardada en esa línea (adjunto). **Min-uno** cuenta solo tarjetas con contenido (una vacía se
  ignora, como en el guardado); una tarjeta empezada exige sus obligatorios.

### Verificado (17-jul, contra `visar_prod`, tarea desechable creada y borrada)

- **E2E HTTP 16/16** (Fumigación): rechazo de vacío, sin confirmación, sin fotos, sin áreas
  (min-uno), área sin plaga (obligatorio de línea), "Otro" sin texto, "infestación activa" sin foto
  de evidencia; y guardado OK con la hoja completa (→ etapa Pendiente de firma + 1 área).
- **Navegador real** (Chrome, iPhone 13 táctil): submit bloqueado, marcas rojas, mensaje de mínimo-uno,
  scroll al primer error, y el **asterisco condicional** de la foto de evidencia aparece/desaparece al
  marcar/desmarcar "infestación activa". Sin errores JS.

---

## 🆕 Actualización — 16-jul-2026 (bis) — traza de botones, hoja tras "Comenzar" y firma tras la hoja

> Segunda tanda del 16-jul. Manifest **v19.0.1.7.0** (campos nuevos + etapa sembrada → `-u`).
> Cambia el **orden obligatorio** del trabajo en sitio: *Comenzar → hoja de trabajo → firma → cerrar*.

### 4. Traza de "Llamar" / "WhatsApp" / "Abrir en Google Maps"

- `POST …/task/<id>/track` con `action ∈ {call, whatsapp, maps}` → `_visar_log_field_action`
  deja una **nota interna** (`mail.mt_note`) en el chatter de la tarea: quién pulsó, qué botón y
  **a qué destino** (el teléfono o la dirección los resuelve el SERVIDOR, no el cliente).
- **`navigator.sendBeacon`, NO `fetch`** (`initTracking` en `field_app.js`): "Llamar"/"WhatsApp"
  **abandonan la página** (`tel:`, `wa.me`) y un fetch en vuelo se cancelaría al descargarse el
  documento; el beacon lo entrega el navegador igual. Tampoco retrasa el toque (la traza no debe
  estorbar al técnico: si falla se pierde la nota, no la llamada). El csrf y la URL viajan en un
  div `#visar-track`; los enlaces solo llevan `data-visar-track`.
- **Doble-toque accidental = una nota** (guarda de 3 s en JS, por acción). Un toque repetido más
  tarde **sí** se registra: reintentar una llamada es información real para gestión.
- El **autor** de la nota es el usuario público (la app es pública, sin login); el cuerpo dice el
  nombre del técnico. Consistente con el resto de avisos del módulo.

### 5. La hoja de trabajo no existe hasta "Comenzar servicio"

- Se pinta y se acepta **solo** en las fases `en_ejecucion` / `cerrado` (`WORKSHEET_STATES`); antes
  se ve una tarjeta apagada *"Se habilita al pulsar «Comenzar servicio»"*.
- Se gatea por **fase**, no por el sello `visar_service_start`: la fase la manda `stage_id` (si
  gestión pone la etapa a mano sin sellos, la app igual deja capturar; ver `_task_flow_state`).
- **Servidor también**: `POST …/worksheet` rechaza el guardado fuera de esas fases (un POST directo
  o una pestaña vieja no escriben). En `cerrado` sigue visible: el técnico consulta lo capturado.

### 6. Firma tras guardar la hoja + etapa "Pendiente de firma"

- **Etapa nueva en el flujo:** Programado → En camino → **En ejecución** → **Pendiente de firma** →
  Completado. Existía en `visar_prod` creada a mano: **archivada**, `sequence=10` (empatada con En
  ejecución) y con el nombre en_US mal ("In Progress", copiado al duplicarla). `seed_signature_stage`
  la **adopta** (no duplica): la activa, la pone en `sequence=15`, corrige ambos nombres, la liga a
  los 12 proyectos FSM y le da **xmlid propio** (`visar_field_app.visar_stage_pending_signature`) →
  `_visar_stage_pending_signature()` la resuelve con `env.ref`, sin ids cableados.
- **Cableado triple** como el sembrador de plantillas: `post_init_hook` + `migrations/19.0.1.7.0/`
  + a mano por shell. `noupdate=True`: es dato vivo, no se pisa en upgrades.
- **Guardar la hoja** (`POST …/worksheet`) sella `visar_worksheet_saved_at` / `_by_id` (la PRIMERA
  vez; auditoría) y mueve la etapa con `_visar_set_stage_pending_signature()`, que **solo avanza
  desde En ejecución** (si gestión ya la pasó a Completado/Incidencia, re-guardar no la retrocede).
- **La firma + "Cerrar servicio" solo aparecen** con `signature_available`: fase `en_ejecucion` **y**
  hoja guardada. **Sin plantilla de hoja** no hay nada que guardar → basta con haber comenzado (si
  no, esos servicios no se podrían cerrar nunca). El cierre lo revalida en el servidor.
- ⚠️ **Trampa mortal evitada:** `_visar_reconcile_flow_markers` borra los sellos cuando la etapa no es
  "de servicio". Como guardar la hoja **cambia de etapa**, si *Pendiente de firma* no se agregaba a
  `in_service`, ese mismo `write` borraba `visar_service_start` → la app retrocedía a *esperando* y
  volvía a ocultar la hoja recién guardada. Hay un check E2E dedicado a esto.
- `_task_flow_state` mapea *Pendiente de firma* → `en_ejecucion` (el técnico sigue en el domicilio,
  ahora firmando). Al volver la etapa atrás (antes de la llegada), el reconcile **también** limpia
  `visar_worksheet_saved_at` → la firma vuelve a exigir guardar la hoja (el dato capturado NO se toca).

### Campos nuevos en `project.task`

`visar_worksheet_saved_at`, `visar_worksheet_saved_by_id` (readonly, en la pestaña "Visar - Campo").

### Verificado (16-jul, contra `visar_prod`)

- **E2E HTTP 24/24** sobre una tarea desechable (creada y borrada; **nada tocado de los datos
  reales**): hoja oculta antes de comenzar y POST rechazado; hoja visible tras comenzar y firma aún
  oculta; cierre rechazado sin hoja; guardar → etapa *Pendiente de firma* + sellos + **`service_start`
  sobrevive**; firma visible; cierre → Completado + `1_done`; 3 notas de traza correctas e internas.
- **Navegador real** (Chrome, iPhone 13 táctil): beacon en los 3 botones — incluido "Llamar", que
  navega a `tel:` —, doble-toque → 1 sola nota, reintento >3 s → 2.
- ⚠️ **Trampa de pruebas:** un `nohup odoo-bin` que choca con un servidor ya levantado falla con
  *"Address already in use"* y **el puerto sigue respondiendo** (el proceso viejo). Comprobar que
  responde el puerto **no** prueba que corra tu código. Para probar sin tocar el servidor de nadie:
  `--http-port=8070`.

---

## 🆕 Actualización — 16-jul-2026 (icono, filtro Hoy/Todos y ruta arrastrable)

> Tanda de 3 arreglos sobre la lista de servicios. Manifest **v19.0.1.6.0** (modelo nuevo → hace
> falta `-u visar_field_app`). Cierra la deuda histórica **"Mis servicios de hoy" sin filtro de
> fecha** y la ⚠️ de `_employee_tasks`.

### 1. Icono de la app (menú raíz)

`static/description/icon.png` era un **PNG transparente de 111 bytes** (placeholder), así que
"App de Campo Visar" salía sin icono aunque `menus.xml` ya apuntaba bien con `web_icon`. Se
reemplazó por la **hoja verde** de la marca, sacada del **favicon del sitio** (`website.favicon`,
ICO multi-tamaño → se extrajo el 256×256; el logo de la compañía es la misma imagen).
`ir.ui.menu.web_icon_data` es **computed/stored**: solo se refresca con `-u` del módulo.

### 2. Lista: alcance Hoy / Todos (`?scope=`)

- **`Hoy` (por defecto):** los agendados para hoy **en el huso del TÉCNICO**, en **cualquier
  estado** — decisión de negocio: los ya cerrados siguen visibles **al final**, apagados y con
  etiqueta (*Completado* / *Reprogramar*), para que el técnico vea el avance de su día. Antes un
  servicio **desaparecía** al cerrarlo (`state ∉ CLOSED_STATES`).
- **`Todos`:** todo lo asignado, cualquier fecha/estado, **días más recientes primero** (sirve para
  consultar lo hecho). Sin arrastre (mezcla días) y con **fecha completa** en la tarjeta.
- Son **enlaces** (`?scope=`), no botones JS: la lista la arma el servidor y la URL se puede
  recargar/compartir. Alcance inválido → `today` (`_scope`).
- ⚠️ **Huso:** "hoy" se calcula con `employee.tz` (`_today_bounds`, `tz.localize` para que el DST no
  desfase). Con el día del **servidor** (UTC) la lista se vaciaría cada tarde en Monterrey (UTC-6).
  Por lo mismo la **hora de la tarjeta se formatea en Python** (`_task_times`): `t-esc`/`t-field` en
  una página **pública** pinta UTC (el usuario público no tiene huso) — 14:00 se veía "20:00".
- **El MAPA es SIEMPRE de hoy**, aunque la lista esté en "Todos" (`today_tasks` aparte).

### 3. Ruta arrastrable (drag & drop) que el mapa refleja y persiste

- **Modelo nuevo `visar.field.route.order`** (`employee_id`, `task_id`, `sequence`; UNIQUE por par).
  **Por qué modelo y no un entero en `project.task`:** un servicio puede tener **varios técnicos**
  (citas multi-técnico del maestro; hoy 4 de 7 tareas en `visar_prod` lo son) → un entero compartido
  haría que el orden de un técnico reordenara la lista del otro, y los números 1..N asignados sobre
  **su** lista chocarían con los servicios que solo ve el otro. Cada técnico ordena **su** ruta.
- **Orden efectivo** (`_task_sort_key`): día → **pendientes antes que cerrados** → orden manual
  (`UNORDERED_SEQUENCE = 9999` para los no arrastrados, que caen al final del día) → hora agendada
  → id. Un servicio agendado **después** del último arrastre entra al final por hora, sin romper nada.
- **El número de parada es UNO solo** para lista y mapa (`_task_map_payload` lo calcula; la lista lo
  pinta desde `stop_numbers`). Se numera **aunque no haya coordenadas** (el mapa salta ese número:
  gaps honestos) y **los cerrados NO se numeran ni entran a la ruta** (pin apagado "✓"): el número es
  *lo que falta por recorrer*, no un historial.
- **`POST /visar/field/tasks/reorder`** (http + csrf, ids por coma) → valida contra los
  **pendientes de HOY del propio técnico** (descarta ajenos/basura/duplicados; `[]` → 400) →
  `_visar_set_order` → responde **JSON con el mapa recalculado** (numeración + ruta Mapbox en el
  orden nuevo) y `field_app_reorder.js` **repinta el mapa sin recargar**
  (`window.visarFieldMap.refresh`, API nueva de `field_app_map.js`).
- **JS con Pointer Events, NO drag&drop HTML5** (este último **no dispara en táctil** = el 100% del
  uso real). Asa `⠿` (`touch-action: none` **obligatorio**: sin eso el navegador se queda el gesto
  vertical para hacer scroll y no llegan los `pointermove`), umbral de 6 px para distinguir
  **toque** (abre el servicio) de **arrastre**, y el click posterior al arrastre se traga.
- **Auto-scroll en los bordes** (`EDGE_ZONE`/`EDGE_SPEED` + rAF): la lista del día **no cabe** en un
  teléfono; sin esto el gesto se topa con el borde de la pantalla y no se puede llevar una tarjeta
  de la última posición a la primera (verificado: con auto-scroll el scroll va 508 → 0 y la tarjeta
  llega a la parada 1). La tarjeta arrastrada es `position: fixed` → se queda bajo el dedo mientras
  la lista rueda por debajo.
- **Sin red** (pasa en campo): el `fetch` falla → se **recarga** la página para volver al orden real
  en vez de mostrar un orden que no se guardó.

### Verificado (16-jul)

- **E2E HTTP contra `visar_prod`** (20/20): alcance Hoy/Todos, cerrados al final con etiqueta, mapa
  siempre de hoy, hora en huso del técnico, numeración lista=mapa, reorder persistente **entre
  sesiones/dispositivos** (logout→login) y rechazo de ids ajenos.
- **Navegador real** (Chrome, perfil iPhone 13 táctil): el gesto completo — despegue de la tarjeta,
  hueco de destino, auto-scroll, renumeración, mapa repintado sin recargar, persistencia al recargar
  y "tocar abre el servicio". Sin errores JS.
- ⚠️ **Assets:** `visar_prod.conf` **no tiene dev mode** → el bundle queda cacheado en el proceso;
  tras editar JS/CSS hay que **reiniciar el servidor** (si no, se sirve el JS viejo y las pruebas de
  navegador mienten).

### Pendiente de esta tanda

- El arrastre **no reordena la agenda real** (`planned_date_begin` no se toca): es la ruta del
  técnico, no una reprogramación. Si el negocio quiere que mover una tarjeta **reagende**, es otro
  trabajo (y toca al calendario/gestión).
- **Orden manual vs. ruta óptima:** sigue sin optimización (vecino más cercano); ahora el técnico la
  hace a mano. Ver la nota de `_task_map_payload`.

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
  companion "Otro"). ~~lo que falta es el **bloqueo** por requerido/condicional (I-05).~~ **[RESUELTO 17-jul-2026]**
- **Binary dentro de subficha o2m anidada** no soportado (raro). ~~m2m dentro de tarjetas o2m~~
  **[RESUELTO 08-jul (bis)]** — m2m sí se soporta en tarjetas (grupo de casillas).
- Sigue en pie lo de "Deuda técnica conocida" que no marcamos como resuelto abajo (forms separados,
  sin validación de cierre, sin offline, PIN texto plano). ["servicios de hoy" sin filtro de
  fecha se resolvió el 16-jul-2026.]

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
  "Otro"). ~~pero sigue **sin bloquear** el cierre por `required` ni por reglas condicionales (I-05).~~ **[RESUELTO 17-jul-2026]** — la hoja de trabajo valida requerido/condicional/min-uno (cliente + servidor).

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
  `visar_field_status`. Así el kanban/Gantt del backend queda sincronizado. (~~Hay una 6ª etapa de BD
  "Pendiente de firma" sin xmlid → **NO se usa** por decisión del negocio.~~ **[CAMBIO 16-jul-2026]**
  — el negocio SÍ la quiere: ahora se usa al guardar la hoja de trabajo. La siembra
  `hooks.py::seed_signature_stage` y se referencia por xmlid propio. Ver la actualización de 16-jul.)
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
| Guardar hoja de trabajo | **Pendiente de firma** ⬅ **[16-jul-2026]** | sella `visar_worksheet_saved_at/_by_id`; **habilita la sección de firma** |
| Cerrar servicio | Completado (`stage_3`) + `state='1_done'` | **exige firma + nombre** (y, desde 16-jul, hoja guardada); escribe timesheet; atribución |
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
- ~~Etiqueta de lista "Mis servicios de hoy" sigue sin filtro de fecha~~ **[RESUELTO 16-jul-2026]**.
- ~~Cierre sin validar worksheet/fotos (solo firma+nombre).~~ **[RESUELTO 17-jul-2026]** — la hoja se
  valida (requerido/condicional/min-uno) antes de habilitar la firma; el cierre sigue exigiendo
  firma+nombre, pero ya no se puede cerrar con la hoja incompleta.

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
display "Visar - App de Campo (Técnicos)", **v19.0.1.10.0**, `application=True`.
(Historial de bumps en las secciones "🆕 Actualización" de arriba; el más reciente, 17-jul, por los
arreglos del PDF.)

**Dependencias:** `visar_fsm`, `website`, `industry_fsm_report`, `base_geolocalize`.

---

## Modelos

### Propios

| Modelo | Archivo | Para qué |
|---|---|---|
| `visar.field.route.order` | `models/field_route_order.py` | **[16-jul-2026]** Orden manual de la ruta del técnico (arrastrar y soltar): `employee_id`, `task_id`, `sequence` (UNIQUE por par). Helpers `_visar_order_map` / `_visar_set_order`. Es **por técnico** a propósito (hay tareas multi-técnico). |
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
| `GET /visar/field/tasks` | http | Lista de tareas del técnico (`_employee_tasks`). **[16-jul-2026]** `?scope=today` (por defecto) / `all`; el mapa siempre es de hoy. |
| `POST /visar/field/tasks/reorder` | http (csrf) | **[16-jul-2026]** Guarda el orden de la ruta de hoy (`task_ids` por coma) y responde JSON con el mapa recalculado (para repintar sin recargar). |
| `GET /visar/field/task/<id>` | http | Detalle del servicio: fotos, worksheet dinámica, firma. |
| `POST …/task/<id>/photo` | http (csrf) | Sube fotos como `ir.attachment` sobre `project.task`. |
| `GET …/task/<id>/image/<att_id>` | http | Sirve una foto de la tarea (acotada al técnico). |
| `POST …/task/<id>/worksheet` | http (csrf) | Guarda campos de la worksheet **nativa** (modelo dinámico `x_...`). **[16-jul-2026]** Solo en fase `en_ejecucion`/`cerrado`; al guardar pasa la etapa a *Pendiente de firma*. **[17-jul-2026]** Valida obligatoriedad (`_worksheet_validation_errors`); si falta algo no escribe y vuelve con `?ws_error=1`. |
| `POST …/task/<id>/track` | http (csrf) | **[16-jul-2026]** Traza en el chatter de los botones Llamar / WhatsApp / Google Maps (`sendBeacon`). |
| `POST …/task/<id>/close` | http (csrf) | Cierra: firma nativa + `state='1_done'` + atribución (`visar_field_closed_by_id`). |
| `GET …/task/<id>/report` | http | Renderiza el PDF **nativo** `industry_fsm.worksheet_custom`. **[17-jul-2026]** incluye el bloque "Tiempo en sitio" (llegada → última guarda de la hoja). |

### Filtro de tareas (`_employee_tasks`)

```python
domain = [('visar_technician_ids', 'in', employee.ids)]
if not include_closed:
    domain.append(('state', 'not in', ('1_done', '1_canceled')))
```

> ⚠️ ~~**No hay filtro por fecha.** La UI dice "Mis servicios de hoy" pero el dominio muestra
> **todas** las tareas abiertas asignadas al técnico, no solo las de hoy.~~
> **[RESUELTO 16-jul-2026]** — `_employee_tasks(employee, scope='today'|'all')`: `today` (por
> defecto) filtra por el día del técnico y muestra también los cerrados (al final); `all` muestra
> todo. Ver "🆕 Actualización — 16-jul-2026" arriba.

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
| `static/src/js/field_app_reorder.js` | **[16-jul-2026]** Arrastrar y soltar las tarjetas (Pointer Events + auto-scroll en los bordes) → POST del orden → repinta el mapa vía `window.visarFieldMap.refresh`. |
| `static/src/js/field_app.js` | Pad de firma sobre `<canvas>` en **vanilla JS** (sin OWL). Al enviar el form de cierre, vuelca la firma a un `data-URL` en input oculto. |
| `static/src/css/field_app.css` | Estilos mínimos del canvas de firma y las fotos. |

## Seguridad — `security/ir.model.access.csv`

| Modelo | `base.group_user` | `hr.group_hr_user` |
|---|---|---|
| `visar.field.session` | solo lectura | CRUD completo |
| `visar.field.route.order` | solo lectura | CRUD completo |

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
| Worksheet dinámica (reflexión) | 🟢 **Reusar (ya extendida 07-jul)** | Ya soporta o2m (tarjetas), m2m (casillas), imágenes por línea, widgets, pestañas invisibles y ayuda. ~~Queda pendiente: requerido/condicional.~~ **[RESUELTO 17-jul-2026]** requerido/condicional/min-uno aplicados. |
| Frontend QWeb + POST/redirect (recarga completa) | 🔴 **Candidato a revamp** | Sin offline, sin validación cliente, forms separados. Mayor brecha para un app de *campo*. **Pero:** migrar a PWA/SPA **contradice** la decisión documentada (`40-decisions.md`); tratarlo como desviación consciente, no rewrite silencioso. |
| Seguridad (público + sudo) | 🟡 **Endurecer antes de prod** | PIN texto plano, sin throttling, `/report` sin límite. |
| Vistas/menús backend | 🟢 **Reusar** | Superficie admin fina, bajo riesgo. |

Todo lo verde es reusable **sea cual sea** la decisión sobre el frontend: un frontend reescrito
seguiría llamando al mismo contrato de controlador.

## Deuda técnica conocida (no bloquea hoy)

- ~~**"Mis servicios de hoy" es inexacto** — `_employee_tasks` no filtra por fecha; muestra todas las
  abiertas.~~ **[RESUELTO 16-jul-2026]** — alcance Hoy/Todos (`?scope=`) en el huso del técnico.
- **Worksheet y cierre son forms separados** — ~~cerrar sin guardar la worksheet pierde lo escrito.~~
  **[MITIGADO 17-jul-2026]** — la firma/cierre no aparece hasta **guardar** la hoja (Req 6), así que ya
  no se puede cerrar sin haberla guardado; siguen siendo forms separados, pero el orden está forzado.
- **Cierre sin validación** — **[PARCIAL 08-jul-2026]** ahora exige **firma + nombre** (JS + servidor,
  Req 2). ~~sigue **sin** validar worksheet/fotos ni `required`/condicional (I-05).~~ **[RESUELTO 17-jul-2026]** la hoja valida requerido/condicional/min-uno.
- ~~**Sin campos relacionales en worksheet** (o2m/m2m)~~ — **[RESUELTO 07-jul-2026]** o2m (tarjetas) + m2m (casillas) + imágenes por línea. ~~Nuevo pendiente: la app no aplica `required`/condicional.~~ **[RESUELTO 17-jul-2026]**
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

---

## 🆕 Actualización — 10-ago-2026 (v19.0.1.16.0) — Fumigación: áreas obligatorias + taxonomía de plagas de 2 niveles

> Reestructura **solo** la plantilla "Fumigación interior o exterior (App v2)" (Jardinería y Visita
> no se tocan) y, de paso, **cierra dos huecos del sembrador** que hacían que los cambios de
> catálogo no llegaran nunca a una BD ya instalada. Requiere **`-u`** (cambia `views/` y hay
> migración) + reinicio.
>
> **Se modificó la plantilla existente, NO se creó una nueva.** Un template nuevo obligaba a
> re-declarar los ~12 campos de línea (el modelo de línea ata su FK a UN modelo de worksheet), a
> una rama nueva en la maqueta del PDF (que despacha por NOMBRE de plantilla) y a re-apuntar el
> proyecto — más trabajo y un duplicado permanente en la configuración. Todo lo pedido es
> aditivo: ningún campo cambia de tipo, así que no hay migración de datos.

### Huecos del sembrador que se cerraron (aplicaban a las TRES plantillas)

`_ensure_field` no toca campos existentes y `_sel`/`_ensure_tag` solo poblaban al crear, así que
**agregar una opción a un catálogo de `hooks.py` no llegaba a QA/prod**: el campo ya existía y el
sembrador pasaba de largo. Sin arreglar esto, la taxonomía nueva no habría aparecido.

| Helper nuevo | Qué converge |
|---|---|
| `_sync_selection(env, modelo, campo, opciones, prune=False)` | opciones de un `selection` existente: agrega las que falten y reordena al orden canónico |
| `_sync_tag_records(env, modelo, registros, prune=False)` | catálogo de un modelo-etiqueta (m2m): agrega las que falten. `_ensure_tag` ahora lo llama SIEMPRE |

Ambos son **aditivos por defecto** (`prune=False`): no borran, porque el valor almacenado de una
opción **es la cadena** y el id de una etiqueta vive en los m2m ya capturados. `prune=True` es
opt-in y solo se usa donde el código manda el contenido. Se aplicaron a los catálogos de las tres
plantillas (`AREAS`, `PLAGUICIDAS`, `ACCION`, `NIVEL`, `TIPO_SERVICIO`, `ESTADO_EQUIPO`,
`TIPO_INMUEBLE`, `COMPLEJIDAD`), así que de aquí en adelante editar una lista en `hooks.py` basta.

> ⚠️ **Renombrar** una opción de `selection` NO es un cambio de catálogo: huérfana los registros que
> la tenían (el valor guardado es la cadena). Eso es una migración de datos aparte.

### 1. Áreas de inspección obligatoria (Cocina / Baño / Área de basura)

Se resolvió **con líneas reales pre-sembradas**, no con tres grupos de campos aparte: así la tabla
del PDF, el `min_one` y la sincronización o2m siguen funcionando sin tocarse.

- `AREAS_FIJAS` (en `hooks.py`) + dos campos nuevos en `x_visar_area_tratada_v2`:
  `x_fija` (marca interna, **a propósito fuera del arch**) y `x_cliente_no_permitio`.
- `_seed_fixed_lines(record)` siembra las que falten **por valor de área, no por conteo** →
  idempotente, no duplica, y una hoja ya abierta recoge un área nueva del catálogo al re-abrirla.
  `x_sequence` negativo las deja **antes** de cualquier área que agregue el técnico.
- Se siembra **solo cuando la hoja es capturable** (`create=True` ⇔ disponible y no bloqueada): las
  rutas de lectura (servir una foto, pintar una hoja cerrada) **no mutan** la hoja.
- La tarjeta fija: encabezado con el área + insignia "Obligatoria", **sin "Eliminar"**, y el campo
  `x_area` viaja en un `hidden` en vez de pintarse como control (ya está decidido).
- `_sync_one_o2m` **nunca** borra una línea `x_fija`, ni ante un POST manipulado que omita su fila.

### 2. Taxonomía de plagas de 2 niveles + gate de presencia activa

El m2m principal pasó de lista plana de plagas a **CATEGORÍA**; cada categoría revela su lista de
especies (m2m companion, indentada). Categorías tomadas del wizard de reserva
(`¿Qué estás viendo en casa?`) para que cotización y campo hablen el mismo idioma.

| Categoría | Especies |
|---|---|
| Rastreros | Cucarachas, Alacranes, Hormigas, Arañas |
| Voladores | Moscas, Mosquitos o zancudos |
| Roedores | Ratas, Ratones |
| Otras plagas | Termitas, Chinches de cama, Polilla |
| Otra plaga no en las opciones | → campo de texto libre |

- `x_infestacion_activa` se reetiquetó a **"¿Se detectó presencia activa de plaga?"** y ahora
  **abre un bloque**: foto de evidencia (obligatoria) + categorías. Antes la foto era
  condicional-obligatoria pero siempre visible; se retiró de `WORKSHEET_REQUIRED_IF` porque ahora
  su `required="1"` vive en el arch y la **visibilidad** hace el trabajo.
- **`WORKSHEET_CONDITIONAL`** (nuevo): la visibilidad condicional ya no depende solo de la
  convención `{base}_otro`. Se declara `{campo: (controlador, kind, trigger)}` con
  `kind ∈ {truthy, many2many, selection}`; para `many2many` el trigger se declara por **NOMBRE de
  etiqueta** y se resuelve al id vigente (los ids de catálogo no son estables entre BDs).
- **Por qué la declaración explícita era obligatoria aquí:** la escotilla se llama "Otra plaga no
  en las opciones" y la categoría "Otras plagas" — la convención (`startswith("otro")`) tenía dos
  candidatas y habría elegido la equivocada.
- **`conditional_chain`** — un campo condicional puede traer `required="1"` del arch, así que
  "oculto ⇒ no obligatorio" tiene que valer también en el **servidor**. Y como la taxonomía anida
  dos niveles (especies → categoría → presencia activa), se evalúa la condición propia **y las de
  los ancestros**: un POST viejo con la categoría marcada y la presencia apagada ya no exige un
  campo que el técnico nunca vio. En el cliente lo resuelve la **cascada del DOM**
  (`evalCondFields` oculta un campo cuyo controlador está oculto).
- `evalCondFields` ganó `kind="truthy"` (antes solo `many2many`/`selection`).

### 3. Dispensa por tarjeta — "Cliente NO permitió que se fumigara en esta área"

- **`WORKSHEET_REQUIRED_UNLESS_LINE = {modelo_de_línea: campo_disparador}`** — se declara por
  MODELO, no campo por campo, para que agregar un campo a la tarjeta quede cubierto solo.
- Al marcarlo, **ningún** campo de esa área es obligatorio — salvo el que la **identifica**
  (`x_area`), que si no dejaría una tarjeta anónima.
- Orden de resolución, idéntico en cliente y servidor: **oculto → no; dispensado → no;** luego
  `required` / `required_if`. El asterisco de un campo con dispensa pasa a ser condicional (lo
  alterna `refreshStars`) para que refleje el estado real.

### 4. PDF del cliente

La tabla "Áreas tratadas" saca una columna por `<field>` de la **sublista**. Cuatro columnas de
especies no caben (el PDF es vertical y la tabla ya lleva 8), así que **no van en la sublista** y en
su lugar `_visar_fumigacion_areas_table` reescribe la celda de "Tipo de plaga" con
`Categoría (especie, especie)`; la escotilla se sustituye por el texto que escribió el técnico.
`_visar_ws_table_descriptor` ahora devuelve también `field_names` para que una maqueta dedicada
reescriba una columna **sin casar por etiqueta** (que se renombra). `x_cliente_no_permitio` sí es
columna de la sublista → sale en el PDF solo.

### Archivos tocados

- `hooks.py` — `_sync_selection` / `_sync_tag_records` (nuevos), `_ensure_tag(prune=)`, catálogos
  `PLAGAS` / `PLAGA_ESPECIES` / `AREAS_FIJAS`, campos nuevos de línea, `FUMIGACION_ARCH`.
- `controllers/main.py` — `WORKSHEET_CONDITIONAL`, `WORKSHEET_NESTED`,
  `WORKSHEET_REQUIRED_UNLESS_LINE`, `LINE_FIXED_*`, `_conditional_chain`, `_declared_conditional`,
  `_seed_fixed_lines`, `_line_fixed_title`, `_field_is_required_now` (orden nuevo), guarda de
  borrado en `_sync_one_o2m`.
- `views/field_app_templates.xml` — tarjeta fija (título/insignia/hidden/sin Eliminar), indentación,
  `data-req-unless`, `req_star` con dispensa.
- `static/src/js/field_app.js` — `truthy` en `evalCondFields` + cascada, `data-req-unless` en
  `isActive`, `refreshStars` ampliado.
- `static/src/css/field_app.css` — `.o_visar_nested`, `.o_visar_o2m_card_fixed`.
- `models/project_task.py` — `_visar_fumigacion_areas_table`, `_visar_fumigacion_plaga_text`,
  `field_names` en el descriptor de tabla.
- `migrations/19.0.1.16.0/post-migrate.py` (**nuevo**), `__manifest__.py` → v19.0.1.16.0.

### Pendiente de esta tanda

- **Verificación en BD**: la lógica de condicional/obligatoriedad/dispensa se probó **aislada**
  (tabla de casos, incluida la cascada), pero el sembrador y el render **no** se han corrido contra
  una BD. Probar `-u` en QA antes de prod.
- **Cámara obligatoria (sin galería) y envío del PDF por WhatsApp** — acordados, aún **no**
  implementados. Ver "Pendientes" abajo.

---

## 🆕 Actualización — 10-ago-2026 (v19.0.1.17.0) — cámara obligatoria + reporte por WhatsApp

> Dos cosas que NO son de las plantillas: (3) toda foto se toma **en vivo**, y (4) el reporte
> firmado se le manda al cliente **por WhatsApp** desde la app. Requiere `-u` (cambia `views/` y
> assets) + reinicio. **No** hay migración: no se agregan campos.

### 3. Captura por cámara (no galería) — `field_app_camera.js`

**El punto de partida ya estaba a medias:** los dos inputs de foto ya eran `multiple` y el servidor
ya leía `files.getlist(...)` en ambos caminos (galería principal y tarjetas o2m), así que **subir
varias fotos ya funcionaba** — no había nada que hacer ahí.

Lo que **no** funcionaba es "solo cámara": `capture="environment"` es una **pista**, no una
garantía. Android Chrome abre la cámara; **iOS Safari la ignora** (más aún junto a `multiple`) y
sigue ofreciendo "Fototeca". Por eso la foto ahora se toma con **`getUserMedia`**:

- Panel a pantalla completa (vista previa + obturador + tira de lo capturado + Listo/Cancelar).
  Se pueden tomar **varias** fotos por sesión y se **acumulan** con lo capturado antes.
- El `<input type="file">` **se conserva, oculto**, y el widget lo rellena con los `File`
  capturados vía **`DataTransfer`**. Así el servidor **no cambia**: mismo multipart, mismo
  `files.getlist`. Es lo que permitió que esto no toque ni una ruta.
- El frame de vídeo **no trae EXIF**, así que no hay orientación que corregir (a diferencia de las
  fotos del carrete, que sí la traían — ver el fix de `_visar_ws_report_image`).
- Se reescala a **1920 px** de lado mayor y se comprime JPEG 0.85 antes de subir.
- Miniaturas "pendientes" con "×" **siempre visible** (lo no guardado se descarta sin confirmar
  nada contra el servidor, a diferencia de las fotos ya guardadas).
- `initWsPhotos` **ya no abre el selector de archivos** cuando no hay fotos: eso reintroducía el
  carrete por la puerta de atrás. Ahora avisa "Tome al menos una foto con la cámara".

**Límites, dichos claramente:** esto cierra el **camino fácil**, no vuelve imposible falsificar una
foto (una cámara virtual seguiría pasando). Garantizarlo de verdad pide verificación del lado del
servidor (sello de tiempo/geo contra la ventana del servicio), que es otra tarea.

**Requisitos y escotilla:**
- **HTTPS obligatorio** (`getUserMedia` solo existe en contexto seguro). En HTTP se avisa en
  pantalla en vez de fallar en silencio. Probar en local por HTTP **no** ejercita este camino.
- `DataTransfer` + asignar `input.files` pide navegador moderno (iOS Safari ≥ 14.5, Chrome, Firefox).
- **`visar_field.allow_gallery_fallback`** (parámetro de sistema, por defecto **NO**) revela un
  enlace "Usar la galería (excepción autorizada)" que quita `capture` y abre el carrete. Existe
  para no dejar a una cuadrilla sin trabajar por un dispositivo donde la cámara falle, sin un
  cambio de código. **No** encenderlo por comodidad: la evidencia pierde su valor.

> ⚠️ **Trampa resuelta:** los botones del widget se **renderizan siempre y se deshabilitan**, nunca
> se omiten con `t-if`. La tarjeta-plantilla que clona `initO2M` se pinta con `card_disabled` y el
> clon solo hace `removeAttribute("disabled")` — omitir el botón dejaba las áreas que **agrega** el
> técnico sin cámara.

### 4. Enviar el reporte firmado por WhatsApp

Botón **"💬 Enviar reporte al cliente por WhatsApp"** bajo la firma (solo `t-if="is_signed"`; el
servidor lo revalida). Envía por AJAX para no perder la pantalla, y deja el resultado en línea.

**El envío NO lo hace Odoo.** El access token de Meta vive en el `.env` del runtime
(`visar_fastapi`), no en la BD — misma decisión que `visar.llm.config` / `visar.whatsapp.config`.
Odoo renderiza el PDF y se lo pasa al runtime:

```
Técnico → Odoo (/report/whatsapp) → loopback 127.0.0.1:8000/internal/send-report
        → pywa → Cloud API → cliente
```

- **Dirección invertida:** hasta ahora el runtime era cliente de Odoo (RPC de solo lectura). Este es
  el **primer** camino Odoo → runtime. Config en Odoo: `visar_field.agent_base_url`
  (def. `http://127.0.0.1:8000`) y `visar_field.agent_token`.
- **Se manda el PDF en base64, no un enlace público.** Así no hay que exponer el reporte en una URL
  con token ni confiar en que Meta lo descargue — y el documento llega **adjunto**, que es lo pedido.
- **Seguridad en tres capas** (detalle en `visar_fastapi/.context/`): loopback (nginx solo proxea
  `/whatsapp/webhook`), token compartido `X-Visar-Token` con `compare_digest`, y superficie mínima
  (el tipo de mensaje y la plantilla los fija la config del runtime, no el llamador).
- **Nunca revienta la pantalla:** sin teléfono, sin firma, sin token, runtime caído o rechazo de
  Meta vuelven como aviso al técnico, y **todo intento queda en el chatter** (`_visar_log_report_sent`)
  para que oficina pueda reintentar.

> ⚠️ **Ventana de 24 h de Meta.** Un mensaje LIBRE con documento solo se entrega si el cliente
> escribió en las últimas 24 h — y el cliente que agendó por la web **nunca escribió**. En
> producción hay que configurar `WA_REPORT_TEMPLATE` (plantilla **aprobada** con cabecera de tipo
> DOCUMENT) en el runtime; sin ella se manda libre, que sirve para probar y falla en campo. La
> aprobación de la plantilla es **tiempo de Meta**, no trabajo de código.

**Bug corregido de paso:** `_upsell_whatsapp_url` trataba el **2-tuple** `(display, e164)` de
`_visar_client_phone()` como si fuera un string, así que el enlace de cobro del upsell armaba un
número basura cuando el teléfono venía sin espacios. La lada la resuelve ahora
`_visar_phone_e164` (10 dígitos ⇒ prefijo 52), y `_visar_client_phone` la usa.

### Archivos tocados

- `static/src/js/field_app_camera.js` (**nuevo**) + alta en `__manifest__.py`.
- `views/field_app_templates.xml` — plantilla compartida `photo_capture` (reemplaza los dos inputs
  de archivo), botón de WhatsApp + avisos.
- `static/src/js/field_app.js` — `initWaReport`, `initWsPhotos` ya no abre el selector, evento
  `visar:photos-uploaded`.
- `controllers/main.py` — ruta `POST …/report/whatsapp`, `_json_err`, `_camera_fallback_allowed`,
  contexto `wa_sent`/`wa_error`/`camera_fallback`, fix del teléfono del upsell.
- `models/project_task.py` — `_visar_send_report_whatsapp`, `_visar_report_whatsapp_config/caption`,
  `_visar_log_report_sent`, `_visar_http_detail`, `_visar_phone_e164`.
- `static/src/css/field_app.css` — panel de cámara, "×" de pendientes.
- **`visar_fastapi`**: `app/outbound.py` (**nuevo**, `/internal/send-report`), alta en `app/main.py`,
  `INTERNAL_TOKEN` / `WA_REPORT_TEMPLATE*` en `config.py` + `.env.example`, aviso en
  `deploy/nginx-whatsapp-location.conf`, `tests/test_outbound.py` (13 pruebas).

### Pendiente / cómo verificar

- **Nada de esto se ha probado en dispositivo ni contra Meta.** El runtime sí: 13 pruebas nuevas
  (puerta, saneo, modo libre vs plantilla, error → 502) y las 153 de la suite pasan; los helpers
  puros de Odoo se probaron extraídos por AST.
- En **teléfono real**: que el panel abra la cámara **trasera**, que iOS **no** ofrezca Fototeca,
  varias fotos por sesión, y las miniaturas pendientes.
- **Extremo a extremo de WhatsApp**: pide `INTERNAL_TOKEN` en ambos lados, `WHATSAPP_ENABLED=true`
  y la plantilla aprobada. Probar primero dentro de la ventana de 24 h (mensaje libre) para aislar
  el transporte de la aprobación de la plantilla.

---

## 🆕 Actualización — 10-ago-2026 (v19.0.1.18.0) — avisos al cliente por WhatsApp (buzón de salida)

> Los avisos **"voy en camino"**, **"ya llegué"** y **"hay que reagendar"** dejan de ser
> simulación en el chatter y se mandan de verdad. Requiere `-u` (modelo, cron, vistas y menú
> nuevos) + reinicio. Sin migración de datos.

### Lo que ya estaba bien y no se tocó

`_visar_notify_client` era desde el principio el **único punto de envío**, y su docstring decía
"cuando se conecte WhatsApp, solo cambia este método". Se cumplió: **los disparadores y los textos
no cambiaron de sitio**, y las guardas de idempotencia que ya existían
(`if not task.visar_enroute_at` / `visar_arrived_at`) siguen evitando el doble aviso. Al de
reagenda se le añadió la suya (`already_flagged`), que no existía.

### Por qué un BUZÓN y no un envío en línea (como el reporte)

El reporte lo manda el técnico pulsando un botón y mirando un spinner. Estos avisos son **efecto
secundario de un cambio de etapa**: si se mandaran en línea, un WhatsApp lento colgaría el toque de
"Voy en camino" y un WhatsApp caído podría tumbar la transición. Así que se **encolan** en
`visar.wa.message` y los manda el cron.

- **El cron se dispara al encolar** (`ir.cron._trigger()`), no solo en su intervalo de 5 min: el
  aviso sale en segundos sin que el técnico espere. El intervalo es la red de seguridad de los
  reintentos. Mismo patrón que la cola de correo nativa.
- **Todo aviso CADUCA** (`expire_at`). Es lo que separa una cola de *mensajes* de una cola de
  *avisos*: reintentar "su técnico va en camino" una hora después es **peor** que no mandarlo.
  TTL por tipo, derivado de para qué sirve el mensaje: `arrived` 15 min (acompaña una ventana de
  espera de ~10), `enroute` 30 min, `reschedule` 24 h.
- **Los dos finales malos avisan en el chatter** — caducidad *y* intentos agotados. Solo avisar al
  caducar dejaba los `failed` en silencio (lo detectó la prueba de la máquina de estados).
- **Reintento manual, no automático, desde la vista de oficina**: reenviar un aviso viejo solo tiene
  sentido si alguien confirma que sigue siendo verdad.
- Menú **App de Campo Visar → Avisos por WhatsApp**, que abre filtrado por **No entregados**: esa
  lista son los clientes a los que hay que llamar.

### El texto vs. la plantilla aprobada

Los textos siguen en `_visar_msg_enroute` / `_arrived` / `_reschedule`, pero ahora cada builder
devuelve **`(texto, params)`**:

- el **texto** es lo que se registra en el chatter y lo que se manda mientras no haya plantilla;
- los **params** van en el orden de los placeholders `{{1}}`/`{{2}}` de la plantilla aprobada.

> ⚠️ **Consecuencia a tener presente:** en producción la redacción vive en el registro de plantillas
> de **Meta**, no en el repo. Cambiar el texto que recibe el cliente será una **re-aprobación**, no
> un commit. El texto del código se queda como registro interno y respaldo.

`visar.wa.message` manda al runtime solo una **CLAVE** (`enroute`/`arrived`/`reschedule`); el
mapeo clave → plantilla vive en el `.env` del runtime. Odoo no puede pedir "manda esta plantilla".

### ⚠️ Esto NO llega al cliente todavía

Las tres plantillas **no están aprobadas** (el reporte se está probando en modo libre). Y a
diferencia del reporte, estos avisos **no tienen camino libre viable**: van siempre a un cliente que
agendó por la web y **nunca escribió**, así que están **siempre fuera de la ventana de 24 h** de
Meta. Hasta que Meta apruebe `WA_TEMPLATE_ENROUTE` / `_ARRIVED` / `_RESCHEDULE`:

- el aviso se encola, se intenta, recibe **502**, se reintenta y **caduca**;
- queda registrado en el buzón y con nota de "no se pudo entregar" en el chatter;
- **el chatter sigue teniendo el texto completo**, así que oficina no pierde información respecto a
  como estaba antes — solo que ahora sabe que el cliente no fue avisado.

Es el comportamiento correcto (fallar visible, no en silencio), pero **no confundirlo con "ya
funciona"**. Las tres plantillas son prerrequisito de negocio.

### Archivos tocados

- `models/wa_outbox.py` (**nuevo**) — `visar.wa.message`: encolar, cron, caducidad, reintento.
- `models/project_task.py` — `_visar_notify_client` encola (ya no simula), builders devuelven
  `(texto, params)`, `_visar_msg_reschedule` (nuevo), aviso en `_visar_flag_reschedule`.
- `controllers/main.py` — los dos call sites pasan `params`.
- `views/wa_outbox_views.xml` (**nuevo**), `views/menus.xml`, `data/wa_outbox_cron.xml` (**nuevo**),
  `security/ir.model.access.csv`, `__manifest__.py` → v19.0.1.18.0.
- **`visar_fastapi`**: `/internal/send-notification` + `WA_TEMPLATE_*`, 10 pruebas nuevas
  (23 en el archivo, 163 en la suite).

### Cómo verificar

- La máquina de estados del buzón se probó **aislada** (TTL por tipo, fallo transitorio →
  recuperación, intentos agotados → `failed` y nunca `sent`, caducidad, y el escenario actual
  "sin plantilla → 502 → caduca"). El endpoint del runtime tiene 10 pruebas nuevas.
- **Nada se ha corrido contra la BD.** Al hacer `-u`: confirmar que aparece el menú, que el cron
  `visar_wa_outbox_cron` existe y está activo, y que al pulsar "Voy en camino" se crea un
  `visar.wa.message` en `pending` que pasa a `sent`/`failed` en segundos.
- Con plantillas aprobadas, comprobar que el buzón registra `mode = template`.
