# Handoff — Prototipo App Técnicos Visar (Field Service Odoo 19)

**Para:** agente con acceso al código y a la versión preliminar de la app móvil de técnicos
**Objetivo del prototipo:** cubrir el flujo principal del técnico en campo, usable desde la **app móvil de Odoo**, listo para demo la próxima semana.
**Encuadre:** esto es la **Fase 1** (Studio + Config + automatizaciones ligeras) del plan ya dimensionado en la investigación H-05. Todo lo etiquetado *Fase 2* queda **fuera** de este prototipo.

---

## 0. Principio rector (leer antes de tocar nada)

Odoo **Field Service (`industry_fsm`)** ya resuelve de fábrica ~4 de los 5 puntos del flujo. La regla de decisión es:

> **Preferir siempre lo nativo de Odoo sobre lo custom.** Si la app preliminar reimplementó algo que Field Service ya hace (lista de tareas, etapas, cronómetro, firma), eso **NO se preserva por inercia**: se reemplaza por lo nativo, aunque el código preliminar "funcione". Lo custom solo se justifica donde Odoo genuinamente no llega.

**Categorías de verdicto que se usan abajo:**

| Símbolo | Significado |
|---|---|
| ♻️ **REUSAR** | Existe en la app preliminar y sirve; extraer/conservar |
| 🔧 **RECONFIGURAR** | Sustituir lo custom por configuración/Studio nativa de Odoo |
| 🔁 **REHACER** | Existe pero es inadecuado para móvil/demo; rehacer |
| 🆕 **NUEVO** | No existe; construir |
| ⏸️ **DIFERIR** | Fase 2 — **no** construir en este prototipo |

**Nota para el agente:** no tengo visibilidad del código preliminar; tú sí. Donde digo "inspecciona X", es una instrucción de evaluar contra la base real y decidir con los criterios dados, no un supuesto de que X ya existe.

---

## 1. Pantalla — Ruta / Tareas del día

**Objetivo:** el técnico ve sus servicios de hoy como tarjetas (kanban) y opcionalmente en mapa.

**Base nativa Odoo (usar esto):**
- Modelo `project.task` con `is_fsm = True`, filtradas por técnico asignado (`user_ids`) y fecha planeada = hoy.
- **Vista kanban** nativa, agrupada por etapa (`stage_id`).
- **Vista mapa** nativa de FSM (plotea cada tarea en la dirección del `partner_id`; requiere que el partner tenga dirección/coordenadas).

**Qué debe hacer el agente:**
- 🔧 **RECONFIGURAR** si la app preliminar arma su propia lista de tareas: reemplazar por la vista **My Tasks** nativa filtrada + kanban.
- 🔁 **REHACER** la tarjeta kanban para móvil: mostrar **solo** hora planeada, cliente, dirección corta, tipo de servicio y etapa como badge de color. Tarjeta densa = ilegible en teléfono.
- ♻️ **REUSAR** cualquier filtro/dominio "solo mis tareas de hoy" que ya exista y esté bien hecho.
- **Inspeccionar:** ¿la app preliminar ya define el dominio de "hoy + este técnico"? Si sí y está correcto, extraerlo. Si filtra en cliente (JS) en vez de en el dominio de la vista, rehacer en el dominio.

**Criterio de aceptación:** abrir la app móvil de Odoo → ver tarjetas de hoy ordenadas por hora, tocar una entra al detalle.

---

## 2. Pantalla — Detalle de tarea (etapas + cronómetro)

**Objetivo:** avanzar el estado del servicio con botones (no arrastrar) y medir tiempo con play/stop.

**Etapas** — `en camino → ya llegué / en ejecución → informe emitido → completado`
**Base nativa:**
- Etapas = `project.task.type` del proyecto FSM.
- Se renderizan como **statusbar** en el form; el técnico **toca la etapa** para avanzar (esto ya es "botón, no arrastrar" — resuelto de fábrica).

**Cronómetro** — play / stop
**Base nativa:**
- FSM trae **Start/Stop** que registra timesheet (`account.analytic.line`) contra la tarea. **Ese es el cronómetro. No construir uno.**

**Qué debe hacer el agente:**
- 🔧 **RECONFIGURAR** si la app preliminar implementó etapas propias o un cronómetro JS custom: reemplazar por statusbar nativo + Start/Stop nativo.
- 🔁 **REHACER** el form del detalle para móvil: statusbar arriba, campos mínimos, botón(es) primario(s) grandes y a prueba de dedos en el header. Opcional: botón grande "Avanzar etapa" que dispare el siguiente `stage_id` para no depender del tap fino en el statusbar.
- ♻️ **REUSAR** cualquier lógica correcta de transición de estado que ya exista, **solo si** mapea 1:1 a las etapas nativas; si no, descartar.
- **Inspeccionar:** ¿el cronómetro preliminar escribe en `account.analytic.line` o guarda tiempo en un campo custom? Si es custom, migrar a timesheet nativo para que el tiempo fluya a reportes/facturación.

**Decisión de diseño (dejar simple para el prototipo):** Start manual al entrar a "en ejecución", Stop manual al terminar. Auto-acoplar Start/Stop a la etapa vía automation = **DIFERIR**.

**Criterio de aceptación:** tocar etapas avanza el estado y queda registrado; play/stop suma tiempo visible en la tarea.

---

## 3. Pantalla — Reporte / Worksheet (la única pieza con trabajo real)

**Objetivo:** formulario mobile-friendly con datos del tratamiento, comentarios, evidencia fotográfica y firma del cliente.

**Base nativa + Studio:**
- **Worksheet template** de FSM (genera su propio modelo con campos `x_...` vía Studio).
- Para el prototipo: **UNA sola worksheet — "Fumigación"** (la más representativa). Las otras dos del plan (Roedores, Visita Técnica) se clonan después. Hacer las tres ahora **DIFERIR**.

**Contenido de la worksheet (orden = orden en que trabaja el técnico):**

1. **Datos del tratamiento** (varios como `selection` para tap-elegir, no teclear):
   - Tipo de plaga tratada
   - Producto / químico aplicado
   - Dosis / cantidad
   - Áreas tratadas
   - Nivel de infestación observado
2. **Garantía / recomendaciones** (reusar mapeo de H-05): servicio recomendado, urgencia, condiciones de garantía. Alimentan el upsell sin esfuerzo extra.
3. **Comentarios** — campo `Text` libre.
4. **Evidencia fotográfica** — ver nota crítica abajo.
5. **Firma del cliente** — campo Binary con `widget="signature"` (nativo, se dibuja con el dedo) + campo `Char` para el nombre de quien firma.

**Qué debe hacer el agente:**
- 🆕 **NUEVO** la worksheet template de Fumigación en Studio con los campos de arriba.
- ♻️ **REUSAR** la **firma nativa** (`task.x_signature` / widget signature). No construir canvas de firma. Si la app preliminar dibujó su propio canvas, descartarlo por el widget nativo.
- ⏸️ **DIFERIR + evitar** la **galería de fotos one2many**. Ver riesgo abajo.

### ⚠️ Nota crítica sobre las fotos (decisión ya investigada)

La app preliminar probablemente tiene "un espacio para subir archivos". Para el prototipo:

- 🆕 **NUEVO / simple:** usar **2–3 campos de imagen fijos** (`foto_antes`, `foto_despues`, `foto_extra`). La cámara del teléfono sale sola por el webview al tocar el campo imagen.
- ⏸️ **DIFERIR:** la galería one2many (`x_visar.task.evidence.photo`). Motivo documentado en H-05: **el one2many no renderiza bien en el PDF** (bug de alta probabilidad) y **wkhtmltopdf batalla con imágenes base64**. No meter estos bugs al demo. La galería es Fase 2 (módulo `visar_fs_report`).

**Criterio de aceptación:** llenar el form completo en un teléfono real, tomar una foto de verdad y firmar con el dedo, sin que la vista se rompa.

---

## 4. Pantalla — Cierre + PDF

**Objetivo:** cerrar el servicio y ver el reporte en PDF.

**Base nativa:**
- **Mark as Done** de FSM cierra el servicio y genera el PDF de la worksheet (esto es "informe emitido → completado").
- El PDF usa el **reporte QWeb** de la worksheet — si los campos del reporte están bien definidos, el PDF sale casi gratis.

**Qué debe hacer el agente:**
- ♻️ **REUSAR** el flujo nativo Mark as Done + PDF.
- 🔧 **RECONFIGURAR** si la app preliminar tiene botones de cierre propios: mapearlos al flujo de validación nativo.
- ⏸️ **DIFERIR:** el reporte QWeb **branded** (colores/logo Visar, secciones portada/diagnóstico/garantías), envío automático por email, y el fix del one2many en PDF. Para el prototipo basta el **PDF nativo de la worksheet**.

**Criterio de aceptación:** cerrar el servicio genera un PDF visualizable con los datos capturados.

---

## 5. Fuera de alcance del prototipo (DIFERIR a Fase 2 — módulo `visar_fs_report`)

No construir nada de esto ahora, aunque exista tentación o restos en la app preliminar:

- Galería de fotos one2many (`x_visar.task.evidence.photo`)
- Reporte QWeb branded Visar + paper format A4 con logo
- Envío automático del reporte por email/WhatsApp al cerrar
- Las worksheets de Roedores y Visita Técnica (solo Fumigación en el prototipo)
- Auto-acople de Start/Stop del cronómetro a las etapas
- ETA / notificaciones al cliente por Google Maps + WhatsApp
- Comisiones por upsell del técnico

---

## 6. Riesgos técnicos conocidos (de la investigación H-05 — mitigar en el prototipo)

| Riesgo | Mitigación para el prototipo |
|---|---|
| One2many no renderiza en PDF | No usar one2many de fotos; campos de imagen fijos |
| wkhtmltopdf y base64 | Campos de imagen nativos; validar render en PDF |
| **Firma no disponible offline** | Probar firma en teléfono real; validar que sincroniza antes de generar PDF |
| Worksheet no cargada al generar PDF | Verificar que la worksheet existe antes de Mark as Done |
| Bug de worksheet al cambiar de proyecto | No cambiar de proyecto la tarea de demo; (fix AR-03 es Fase 2) |

---

## 7. Checklist de entrega (todo probado en teléfono real, dentro de la app móvil de Odoo)

- [ ] Ver tarjetas de "mis servicios de hoy" (kanban) + mapa
- [ ] Entrar a una tarea y avanzar las 4 etapas con botón/tap (no arrastrar)
- [ ] Play/Stop del cronómetro suma tiempo a la tarea
- [ ] Llenar worksheet de Fumigación: datos de tratamiento, comentarios
- [ ] Tomar 2–3 fotos con la cámara del teléfono
- [ ] Firmar con el dedo + capturar nombre
- [ ] Mark as Done cierra el servicio y genera PDF visualizable
- [ ] Probados los estados feos: sin señal, servicio reagendado, error al subir foto

> **Regla de oro para el agente:** probar en un teléfono real dentro de la app de Odoo desde el día 1, **no** en el navegador de escritorio encogido. La cámara y el canvas de firma son justo donde el webview se porta raro; hay que descubrirlo temprano, no en el demo.
