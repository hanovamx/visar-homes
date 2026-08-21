# WhatsApp — Agendado completo en el chat

> ## Estado: **IMPLEMENTADO Y EN PRODUCCIÓN** (19/20-ago-2026), con dos huecos
>
> La cabecera original decía *"DISEÑO, no implementado"* y se quedó ahí mientras las §10.1–§10.9
> documentaban cuatro rondas de verificación en servidor, tres fallos del primer uso real y un
> despliegue. Hoy **un cliente puede reservar escribiendo por WhatsApp**, de punta a punta, sin
> salir del chat salvo para pagar.
>
> **Lo que funciona:** cuestionario por RPC, dirección, nombre, extras, póliza, días y horarios en
> hora local, apartado de 10 min, pantalla de revisión con corrección por paso, liga de pago que
> vive y muere con el apartado, avisos salientes y hand-off humano.
>
> **Lo que NO cierra:**
> - ⛔ **§10.7 / I-17 — la rama de valoración no llega a horarios.** `valuation` es terminal.
>   Termitas, chinches y "no sé qué es" **no pueden agendar por WhatsApp**.
> - ⛔ **§5 — la factibilidad de traslado no existe en código.** Decisiones 7 y 14 son prosa. Hoy
>   se ofrece cualquier horario con capacidad sin mirar si el técnico llega. Sostenible solo
>   mientras haya **un** técnico usable (§5.3.2).
> - **§4.0 — el CP temprano** está marcado "decidido" y **sin construir**.
>
> **Cómo leer este documento:** §1–§9 son el diseño; §10.1–§10.9 son el diario de a bordo, en
> orden cronológico, y son la fuente de verdad sobre qué está hecho. §12 son las 15 decisiones,
> §13 lo abierto.

> **Estado original: DISEÑO, no implementado.** Escrito 17-ago-2026. Retoma las etapas E/F
> que quedaron **en pausa** el 31-jul (ver `visar_fastapi/.context/50-status-roadmap.md`),
> cuando se decidió que agendar fuera solo una **liga al wizard web**.
>
> **Qué cambia respecto a [`29-whatsapp-agent-routing-design.md`](./29-whatsapp-agent-routing-design.md):**
> el umbral de hand-off se lleva a su límite declarado —"el último momento
> responsable"— y ese límite es **el pago**. Todo lo demás (servicio, calificación,
> medidas, dirección, extras, póliza, **fecha y hora**) se recoge en WhatsApp.
> Eso obliga a **revisar la decisión 10 del doc 29** (hand-off por deep link,
> opción A) — ver §7.
>
> El diseño 29 sigue vigente en todo lo demás: enrutado por menú, escapes,
> rewind-and-replay, generación constreñida, límites de mensajes interactivos.

## 1. Objetivo

Que un cliente pueda reservar un servicio de principio a fin por WhatsApp y solo
salga del chat para **pagar**, con una liga. Incluye:

- Compra única (fumigación y/o mantenimiento de áreas verdes).
- **Extras / add-ons** (estaciones antirroedores, etc.).
- **Póliza** (upsell de suscripción) — el paso de mayor valor del wizard.
- **Visita de valoración**, cuando la calificación o el tramo cortan a valoración.

## 2. Punto de partida: la tubería ya existe

Esto es lo más importante del análisis. Lo que hoy pasa al reservar en el web:

```
pasos del wizard (sesión HTTP)
  → página nativa de horarios, filtrada por _visar_filter_slots_multi_service
  → _visar_create_calendar_booking      → calendar.booking (PENDIENTE, sin pagar)
  → _visar_fill_wizard_cart_and_redirect → líneas de sale.order
                                           (lista por zona, combo, add-ons, anticipo)
  → checkout de website_sale → pago
  → _make_event_from_paid_booking       → calendar.event
  → visar_fsm / _invoice_paid_hook      → project.task (FSM) + visitas de póliza
```

**De "horario elegido" hacia la derecha no hay nada que reconstruir.** WhatsApp no
sustituye esa tubería: la **alimenta**. El trabajo se parte en (a) un front-end
conversacional que produzca los mismos insumos, y (b) **dos piezas nuevas de
servidor**: el *hold* de slot y la factibilidad de ruta.

| Pieza | Dónde vive | ¿Reusable tal cual? |
|---|---|---|
| Catálogo, tramos, zonas, CP | `visar_base` | Sí — el agente ya cotiza con paridad al peso |
| Cotización (combo, add-ons, póliza) | `appointment.type._visar_quote_booking` | Sí — ya la usa `agent_quote_service` |
| Pools de técnicos por zona+servicio | `_visar_service_resource_pools` | Sí |
| Generación de slots | `appointment.type._get_appointment_slots` | Sí — es método de modelo, **no** necesita sesión web |
| Filtro multi-técnico | `_visar_filter_slots_multi_service` | Sí — y es el punto donde entra la factibilidad |
| Reserva pendiente | `calendar.booking` (nativo) | Sí |
| Pago → cita → tarea FSM | `_make_event_from_paid_booking`, `_invoice_paid_hook` | Sí |
| Buzón de avisos WhatsApp | `visar.wa.message` + `/internal/send-notification` | Sí |

## 3. Hallazgo crítico — hoy se puede cobrar dos veces el mismo horario

Verificado en el código nativo, no supuesto:

- Un `calendar.booking` pendiente **no consume capacidad**.
  `_get_resources_remaining_capacity` (enterprise/`appointment/models/appointment_type.py`)
  solo cuenta `appointment.booking.line`, que cuelgan de `calendar.event`
  **confirmados**. Las `calendar.booking.line` de la reserva pendiente son
  invisibles para la disponibilidad.
- La única limpieza es `_gc_calendar_booking`: 6 meses de antigüedad, o terminadas
  hace 2 meses. **No existe ningún hold corto.**
- Al pagar, `_make_event_from_paid_booking` llama a `_filter_unavailable_bookings`
  y **descarta en silencio** la reserva que perdió el horario, dejando solo una
  nota en la factura: *"not confirmed due to insufficient availability"*.

**Consecuencia:** hoy, en el web, dos clientes pueden llegar al checkout del mismo
horario; el segundo **paga y se queda sin cita**, y nadie se entera salvo por el
chatter. WhatsApp no crea el problema: lo ensancha, porque el intervalo entre
"te mando la liga" y "el pago entra" es más largo que un checkout web.

> **Matiz honesto (verificado en el servidor, 17-ago-2026).** El mecanismo está
> confirmado en el esquema —69 `appointment.booking.line`, todas con
> `calendar_event_id`; la capacidad solo la consumen citas confirmadas— pero
> **el fallo nunca ha ocurrido en estos datos**: de 72 `calendar.booking`, 70
> tienen cita, y las 2 sin cita son carritos abandonados (pedidos en borrador
> jamás pagados) cuyo horario **nadie tomó después**. Cero coincidencias buscando
> la nota de "insufficient availability" en `mail_message`. La columna
> `calendar_booking.not_available` existe y está en `false` en todos lados.
>
> La lectura correcta: **el peligro es real y estructural, pero con un solo
> técnico y 72 reservas no ha habido concurrencia que lo exponga.** Ausencia de
> evidencia no es evidencia de ausencia — y WhatsApp sí introduce la concurrencia
> (un cliente con la liga abierta 10 minutos mientras otro reserva).
>
> Esto **baja el tono** de la justificación original ("arregla un fallo vivo"):
> sigue habiendo que construirlo, pero como **requisito del canal nuevo**, no como
> incendio del actual. Ver §10 para el efecto en el orden de fases.

## 4. Los pasos, y su equivalente en WhatsApp

Orden real del wizard, según `_visar_wizard_next_step` y el grafo `_VISAR_STEP_CLEARS`:

| # | Paso | Clave en `selections` | En WhatsApp |
|---|---|---|---|
| 1 | `services` | `group_ids` | Lista: Fumigación / Áreas verdes / Ambos |
| 2 | `motivo` (solo si hay fumigación) | `motivo` | Botones (≤3) |
| 3 | `plagas` | `servicio_plaga`, `requiere_valoracion`, `motivo_valoracion` | Lista (ojo al tope de 10) |
| 4 | `cobertura` | `cobertura` | Botones: interior / exterior / ambos |
| 5 | `group_<id>`, `dimensiones` | dimensiones por grupo | Lista |
| 6 | `interior` / `exterior` | tramo por eje | Lista de bandas de m² (**paginar**) |
| — | `valuation` | `requiere_valoracion` | **Corte.** Ver §4.1 y ⛔ §10.7 |
| 7 | `address` | `delivery_address`, `zone_id` | Varios campos, guiados de uno en uno |
| 8 | **`nombre`** | `nombre` | Texto libre. **Solo si el teléfono no resuelve a un `res.partner`** — ver §10.6(c) |
| 9 | `extras` | `extras_ids` (marca) / `extras_accepted` (lo que se compra) | Lista multi-selección |
| 10 | `poliza` | `poliza_plan_id` | Lista de planes con comparativa |
| 11 | `schedule` | — | §5 |

> **Nombres reales, porque esta sección usaba los viejos.** Los métodos son
> `_visar_wizard_next_step` (no `_visar_wizard_next`) y `_visar_wizard_requires_valuation` (no
> `_visar_selections_require_valuation`). Las constantes son `VISAR_STEP_*` en
> `visar_appointment/models/appointment_wizard_flow.py`.

> **El paso `nombre` no estaba en esta tabla** y se añadió en §10.6(c): un cliente nuevo no tenía
> forma de dar su nombre y la reserva moría al final. Va **después** de la dirección a propósito:
> es el único dato que se pide por gusto del sistema y no del cliente, así que se cobra cuando ya
> hay algo que agendar, no en la puerta.

> **`extras_ids` y `poliza_plan_id` marcan "contestado" por PRESENCIA de la clave**, no por su
> valor. *"No quiero extras"* y *"no me lo han preguntado"* tienen que ser estados distintos, o al
> corregir cualquier cosa se vuelve a preguntar todo (§10.9).

### 4.0 El CP se pide TEMPRANO (decidido)

El web pide la dirección casi al final (paso 7), pero la zona sale del CP y de la
zona dependen **precio y pools de técnicos**. En WhatsApp el CP se pide **justo
después del servicio**, y la dirección completa se sigue capturando en su paso
normal (7). Dos razones, y la segunda es la que más pesa:

1. **Cortar temprano.** Fuera de cobertura se rechaza en la segunda pregunta, no
   después de seis. Hoy el web hace todo el cuestionario y rechaza al final.
2. **Precalentar mientras el cliente responde.** El CP es lo que destraba casi
   todo el trabajo caro, y el cliente todavía tiene 4-5 preguntas por delante:
   ese tiempo es gratis. Con el CP ya se puede resolver, en segundo plano:
   - `visar.zone.cp` → zona → **pools de técnicos elegibles**;
   - la agenda de esos técnicos para los próximos días (paradas ya reservadas);
   - la **geocodificación** del CP (centroide) como respaldo, para no depender de
     que la dirección exacta geocodifique bien más tarde;
   - las **matrices de viaje** del §5.3 para los días candidatos.

   Cuando el cliente llega al paso de fecha, la respuesta ya está caliente en vez
   de arrancar una cascada de llamadas a Mapbox con el cliente esperando.

Reglas del precalentado, para que no se vuelva un problema:

- Es **oportunista**: si no terminó, el paso de fecha lo calcula en vivo. Nunca
  bloquea ni cambia el resultado, solo la latencia.
- Se **invalida** si el cliente cambia el CP en el paso de dirección (o si la
  dirección final cae en otra zona) — el CP temprano es una *pista*, la dirección
  del paso 7 sigue siendo la fuente de verdad.
- El resultado va a la misma caché de viajes del §5.3, así que no hay una segunda
  ruta de datos que mantener.

El resto del grafo de dependencias se respeta igual.

**El grafo de dependencias es obligatorio.** `_VISAR_STEP_CLEARS` dice qué se
invalida al cambiar un paso (cambiar `cobertura` borra los tramos, etc.). El
rewind-and-replay del doc 29 tiene que respetarlo, o quedarán estados
inconsistentes que el web ya sabe podar y el chat no.

**Nombre del cliente:** en el web viene del formulario nativo de la cita. En
WhatsApp el teléfono ya identifica al cliente (`_agent_find_partner`), así que solo
hay que pedir el nombre cuando el teléfono **no** resuelve a un `res.partner`.
Ojo con la política de ambigüedad (ver R-06 en el backlog del runtime): dos
partners con el mismo número devuelven "no encontrado".

### 4.1 Rama de valoración

Se dispara cuando `_visar_selections_require_valuation(selections)`: por
calificación (termitas, chinches, plaga no identificada) o porque un tramo trae
`is_valuation` (área fuera del tabulador).

- Es un **modo propio** (`mode='valuation'`), no un paso más: tipo de cita propio,
  pool propio, producto de precio fijo (`_visar_valuation_price()`), y un solo
  `item` con `is_valuation: True`.
- **No pregunta medidas** — el corte es justamente para no medir.
- **No ofrece póliza:** `_visar_poliza_context` devuelve `None` si la reserva
  requiere valoración. La rama se salta el paso 9.
- Arrastra el contexto de calificación (`motivo_valoracion`) a la cita, igual que
  `visar_valoracion_submit` con `from_wizard=1`.

El agente lo trata como el web: al detectar el corte, **avisa** (precio de la
valoración y por qué) y continúa por la rama de valoración hasta el mismo paso de
horarios.

> ⛔ **Esto es la intención, no lo que hace el código.** Hoy `valuation` es **terminal** y la rama
> no llega a horarios — ver §10.7 e I-17.

> ⚠️ **Corrección (20-ago-2026).** Esta sección decía *"la factibilidad de ruta (§5) aplica
> idéntica"*. **No aplica en absoluto, a ninguna de las dos ramas**: el predicado del §5 no está
> construido. Y cuando se construya **no** saldrá gratis en valoración: el §5.5 dice que se
> engancha en `_visar_filter_slots_multi_service`, y la rama de valoración **no pasa por ahí**
> (`_agent_slot_tree` llama a `_get_appointment_slots` directo). Es exactamente el mismo tropiezo
> que ya documenta `visar_slot_hold.py` en su docstring. Hacen falta **dos** enganches — ver la
> corrección del §5.5.

### 4.2 Extras y póliza

- **Extras:** `_visar_offered_addons(items, zone, include_roedores)` ya devuelve la
  oferta. En chat son varias preguntas sí/no o una lista multi-selección; el
  resultado es la misma lista `extras_accepted = [{product_id, quantity}]`.
- **Póliza:** solo se ofrece si `_visar_poliza_context` da `(zone, master, plans)`
  — es decir, hay lista (zona × plan) configurada y alguna línea recurrente. El
  mensaje debe separar, como el web, **precio recurrente vs. lo que se cobra hoy**
  (`recurring_total` / `upfront_total`, ver `35-polizas.md`): meter los extras en
  el "al mes" fue un bug real del primer corte del paso.

## 5. Selección de fecha — la lógica nueva

### 5.1 Los tres casos son un solo algoritmo

El planteamiento original distingue (a) día sin servicios, (b) día con servicios y
(c) nada cabe. En realidad:

- **(a) es (b) con la lista de paradas vacía** — todos los slots pasan trivialmente.
- **(c) es (b) sobre otro conjunto de días** — se ensancha la ventana y, si el CP
  queda estructuralmente lejos, se relaja la restricción a "días sin servicios".

Conviene implementarlo como **un predicado**: *¿cabe esta parada en el día de este
técnico?* Una sola cosa que probar, tres presentaciones distintas.

### 5.2 El predicado de inserción

Para un día `D`, un técnico `R` (ya filtrado por zona y servicio) y un slot
`S = [inicio, fin]`:

1. Paradas existentes de `R` en `D`, ordenadas por `event_start`
   (`appointment.booking.line` → `calendar.event` → partner → `partner_latitude/longitude`).
2. `S` cabe entre las paradas *i* e *i+1* si
   `viaje(i → nuevo) ≤ presupuesto antes` **y**
   `viaje(nuevo → i+1) ≤ presupuesto después` (ver la fórmula abajo).
3. Primera y última posición: ver §5.2.1 — el borde exterior **no** se restringe
   por viaje.

Sin paradas, cualquier slot con capacidad pasa (caso a).

#### La hora se parte en 20 + 40 (confirmado con Visar, 19-ago-2026)

El bloque de una hora **no** es una hora de servicio: son **20 min de traslado +
40 min de servicio**. El técnico sale de la parada anterior, conduce hasta 20 min
y trabaja 40. Eso deja la aritmética así, para un slot candidato que empieza en
`T` cuando el compromiso anterior termina en `E`:

```
presupuesto de viaje = 20 min + (T − E)
```

- **Pegados** (`T = E`): 20 min justos. Es el caso que aprieta.
- **Con hueco** (`T > E`): el hueco se suma al presupuesto. Un trayecto de 40 min
  es perfectamente ofrecible si el técnico tiene la mañana libre antes.

Lo mismo hacia adelante: la parada siguiente tiene su propio presupuesto, y meter
una parada nueva no puede romperlo.

> **Es un presupuesto, no un radio de servicio** (decisión 14). No existe un tope
> duro de "nunca a más de 20 min": lo que no se puede es comerse el traslado de
> otra cita. La consecuencia buscada es que **la disponibilidad dependa de quién
> reservó antes** — el primero que llega, se lo lleva; el siguiente simplemente no
> ve ese horario entre las opciones. No hay nada que explicarle al cliente,
> porque nunca ve la opción que no cabe.

**Duración.** Los dos números salen de configuración, no del código: el bloque de
`appointment_type.appointment_duration` (hoy 1 h, que es de donde ya lo toma
`_visar_filter_slots_multi_service`) y el reparto 20/40 dentro de él. Lo que
**no** está resuelto es si la parte de servicio debería variar por `items` (una
fumigación de 800 m² no es una de 80); hoy no varía, y si algún día varía, lo que
cambia es el reparto, no el predicado.

#### 5.2.1 Los bordes del día

La pregunta "¿contra qué se compara la primera y la última parada?" tiene dos
mitades, y solo una sigue abierta:

- **Los límites de horario ya están resueltos por Odoo.** Los slots los genera
  `_get_appointment_slots` dentro de las ventanas de `appointment.slot`
  (día de la semana + hora inicio/fin, con posible restricción por recurso) y del
  `resource_calendar_id` del técnico. No hay que leer ninguna jornada a mano: si
  un slot existe, ya está dentro del horario laboral.
- **De dónde SALE el técnico sigue abierto.** Para la primera parada del día
  habría que medir el viaje desde su punto de origen (¿la oficina? ¿su casa?), y
  eso no está modelado en ningún lado.

**Decisión: no restringir el borde exterior.** Solo se valida el viaje *entre*
paradas:

- si el slot cae **antes** de todas las paradas → solo se exige `viaje(nuevo → primera) ≤ presupuesto`;
- si cae **después** de todas → solo `viaje(última → nuevo) ≤ presupuesto`;
- en medio → las dos condiciones, como en §5.2.

> **Con el modelo de presupuesto (decisión 14), esto deja de ser una concesión y
> pasa a ser lo correcto.** Un tope duro de 20 min sí habría obligado a modelar el
> origen del técnico —y el primer trayecto del día es justo el más largo—, pero un
> *presupuesto* mide lo que una parada nueva le quita a la siguiente, y la primera
> parada del día no le quita el traslado a nadie: no hay cita anterior que
> proteger. El técnico sale de donde salga y llega cuando llega.
>
> Efecto práctico: **no hace falta geocodificar "Visar Home"** para esta fase.
> Sigue en el backlog (§13) por otras razones, pero ya no bloquea nada.

Es conservador en la dirección correcta: nunca rechaza un horario por un dato que
no tenemos. Si más adelante Visar quiere acotar también el primer trayecto, la
dirección de la compañía como origen es un cambio de una línea en el predicado.

### 5.3 Control de costo

Ingenuo, esto serían miles de llamadas a Mapbox. Dos medidas, y la primera es la
que hace el trabajo:

1. **Una llamada de Matrix por (día, técnico), no por slot.** Se pide de una vez la
   matriz entre las paradas del día y la dirección nueva; después **todos** los
   slots del día se evalúan con aritmética. La Matrix API de Mapbox admite hasta
   25 coordenadas por petición — de sobra para la jornada de un técnico (pico
   medido: 9 paradas). Con ~10 días candidatos por conversación, son ~10 llamadas
   por reserva.
2. **Caché de tiempos de viaje** por par de coordenadas redondeadas. Entre dos
   direcciones fijas el tiempo casi no cambia; no vale la pena volver a pagarlo.

> ⚠️ **Corrección (19-ago-2026).** La primera versión de esta sección decía "zona
> primero, geometría después: `visar.zone.cp` → técnicos elegibles poda casi todo
> el espacio gratis". **Eso era falso, y por una razón de fondo:** las zonas de
> Visar son una **métrica de precio**, no de distancia ni de tiempo. No están
> trazadas por cercanía, así que dos direcciones de la misma zona pueden estar a
> 45 min una de otra, y la zona **no aproxima el presupuesto de viaje**.
>
> La zona sigue sirviendo para lo suyo —qué técnicos atienden esa dirección
> (`_visar_eligible_resources`) y qué lista de precios aplica—, pero con **un solo
> técnico usable** ese filtro casi no poda nada. El control de costo descansa
> entero en la medida 1, que de todos modos era la buena.

### 5.3.1 Los datos reales (verificado en el servidor, 17-ago-2026)

Todo lo de §5.3 se diseñó a ciegas. Lo que hay de verdad:

| Dato | Realidad | Efecto en el diseño |
|---|---|---|
| Token de Mapbox (`web_map.token_map_box`) | **Existe** y no está vacío (101 chars). `base_geolocalize.geo_provider = 1` | La nota I-07 del backlog ("falta un token real") está **obsoleta**. Ya no bloquea. *No se hizo llamada en vivo: la validez del token sigue sin comprobarse.* |
| Direcciones geocodificadas | **77.6%** de las tareas abiertas con partner (97/125); 68.8% de los partners de esas tareas; solo 20.8% del padrón general | Viable, pero **1 de cada 4** tareas necesita geocodificar bajo demanda |
| Técnicos (`appointment.resource`) | **2 registros, 1 usable.** *Pedro Martínez* (zonas A/B/C, 4 servicios, empleado ligado); *Jose Gonzalez* sin zonas, sin servicios y sin empleado → `_visar_eligible_resources` **nunca** lo devuelve | Ver §5.3.2 |
| Duración y ventana | `appointment_duration = 1.0 h`; slots **L-V 08:00–18:00**, **Sáb 09:00–12:00** | Confirma el supuesto de ≤1 h (decisión 7) |
| Carga diaria | Pedro: mediana **2.5** paradas/día, **máx 9** (24 días con trabajo en 60). Tareas FSM: pico 12 | El día más ocupado cabe de sobra en **una sola** llamada Matrix (tope 25) |

Dos parámetros del tipo de cita que hay que tener presentes:

- **`min_schedule_hours = 24`** → **no se puede reservar para hoy**. Un cliente que
  pregunte por WhatsApp *"¿pueden venir hoy?"* no tiene respuesta posible por esta
  vía; el flujo debe decirlo de frente y ofrecer lo más cercano válido.
- **`min_cancellation_hours = 720`** (30 días) con `max_schedule_days = 30`: hace
  la cancelación **imposible en la práctica**. Casi seguro es un error de captura;
  conviene revisarlo con Visar (no es parte de este diseño, pero se cruza).

### 5.3.2 Un solo técnico — qué significa

Con **un** técnico elegible, buena parte de la maquinaria multi-técnico
(`_visar_pick_resources_for_slot`, intersección de pools) queda inerte: no hay a
quién elegir. Dos consecuencias opuestas que conviene no confundir:

- **Baja la urgencia.** Con mediana de 2.5 paradas al día, los huecos son enormes y
  casi cualquier horario cabe. La factibilidad de ruta rara vez cambiará la
  respuesta **hoy**.
- **Sube el valor unitario.** Con un solo técnico no hay plan B: si la ruta no da,
  no da. Y el valor crece de golpe en cuanto entre el segundo técnico real.

Recomendación: el predicado se construye igual (es barato y el web lo aprovecha),
pero **detrás de un flag** y sin bloquear el resto del flujo. Ver §10.

> **Dato aparte, útil para el backlog:** la asignación de tareas no es dato
> confiable — 83 tareas activas están asignadas a *admin*, 4 a `__system__`, 3 a
> una cuenta `@tec.mx` y 61 no tienen a nadie. La carga por técnico **solo** es
> fiable por `appointment.booking.line → appointment.resource`, nunca por
> `project.task.user_ids`. El predicado debe leer de ahí.

### 5.4 Degradar, nunca bloquear

Si la dirección no está geocodificada, no hay token o Mapbox falla → **caer a
disponibilidad por zona** (el comportamiento de hoy). Es el mismo patrón que ya
sigue `_visar_enroute_eta_minutes` con su ETA fija. Una falla de geocodificación
**no puede costar una reserva**.

Con 77.6% de cobertura (§5.3.1) esta rama **no es excepcional: se usa en ~1 de
cada 4 casos**. De ahí que el precalentado del CP (§4.0) incluya geocodificar el
centroide del código postal: aunque la dirección exacta no resuelva, el centroide
da una aproximación **mucho** mejor que rendirse a zona pura, y ya está calculado
para cuando llegue el paso de fecha.

### 5.5 Dónde se enchufa (y por qué el web gana igual)

Como un predicado más dentro de `_visar_filter_slots_multi_service`, que ya recorre
el árbol de slots y ya decide qué recursos sirven. Consecuencia: **el wizard web
hereda la factibilidad sin tocarlo**. Es el argumento para construir esto primero,
del lado del servidor, con independencia de WhatsApp.

> ⚠️ **Corrección (20-ago-2026) — un solo enganche NO basta.**
>
> `_visar_filter_slots_multi_service` cubre el web y el agente en `mode='wizard'`. **La rama de
> valoración no pasa por ahí:** `_agent_slot_tree` llama a `_get_appointment_slots` directo con
> `_visar_eligible_resources(zone)`. El repo ya se tropezó con esto y lo dejó escrito en
> `visar_appointment/models/visar_slot_hold.py`:
>
> > *"Filtrar solo en `_visar_filter_slots_multi_service` no habría bastado — la rama de
> > valoración no pasa por ahí (`_visar_wizard_active()` solo es cierto con `mode == 'wizard'`)."*
>
> El predicado se construye como **una pasada aparte** llamada desde **los dos** sitios.
>
> **Y las dos tienen semántica distinta, que es la trampa de verdad:**
>
> | Árbol | Lista de recursos | Regla | Por qué |
> |---|---|---|---|
> | Multi-servicio | `available_resources`, ya **elegidos** para cubrir todos los servicios | **todos** deben ser factibles; la lista **no se poda** | podarla rompería la cobertura y obligaría a reescribir `url_parameters` |
> | Valoración | `available_resource_ids`, **candidatos** | basta **uno**; los no factibles **se podan** | el runtime toma `resource_ids[0]` y si no apartaría un técnico que no llega |
>
> Con un solo técnico las dos reglas coinciden, así que la realidad no las distingue todavía. De
> ahí que cada una lleve su prueba.
>
> **Cómo llega la dirección: parámetro explícito, no clave de contexto.** `visar_hold_cache` y
> `visar_hold_owner` son contexto porque su consumidor se alcanza desde código **nativo** de
> Odoo y no hay firma que extender. Aquí los dos llamadores son código de Visar y **ya tienen el
> booking en la mano**. Habiendo parámetro, parámetro: se prueba sin `env` y no puede filtrarse a
> una llamada que no le toca.
>
> **Un ahorro que esta sección no menciona:** un día **sin paradas** no necesita ninguna llamada a
> Mapbox — es el caso (a) del §5.1, pasa trivialmente. Con mediana de 2.5 paradas/día, la mayoría
> de los días candidatos cuestan **cero**.
>
> **Y un aviso de perfil:** para la Matrix conviene `mapbox/driving`, **no** `driving-traffic`.
> Este último está limitado a **10 coordenadas** por petición, y 9 paradas + 1 destino son
> exactamente 10 — el pico medido quedaría justo en el límite. Además, con
> `min_schedule_hours = 24` nunca se reserva a menos de un día vista, así que el tráfico *de
> ahora* es ruido. `_visar_enroute_eta_minutes` se queda con `driving-traffic` porque **ahí** el
> técnico sale ahora mismo.

### 5.6 Cómo se pide la fecha

Las dos formas, como se acordó:

- **Lista de los próximos N días factibles** (rápido, fiable, un tap). Es el camino
  principal; el tope de 10 filas obliga a paginar ("Ver más fechas").

> **Cómo se redacta la hora** (decisión 15): al cliente se le ofrece una
> **ventana**, no una hora exacta — "entre 3 y 4 de la tarde", no "3:00 pm". El
> bloque son 20 min de traslado + 40 de servicio, así que prometer una hora en
> punto es prometer algo que la calle no respeta. `agent_day_slots` ya devuelve
> `start` y `stop`: la ventana se redacta con esos dos, sin tocar el RPC.
- **Texto libre** ("el jueves", "mañana", "el 20") con un parser acotado de fechas
  en español y **confirmación explícita** antes de consultar. Si no se entiende, se
  cae a la lista — nunca se adivina una fecha.

Cuando nada cabe: 3 alternativas cercanas (ventana de 7 días) y, si el CP está
estructuralmente lejos, los 3 días más cercanos sin servicios. Si el cliente
rechaza todo → hand-off humano (ver §9, dependencia abierta).

## 6. Hold de slot (10 minutos)

**Modelo nuevo `visar.slot.hold`:** recurso, ventana `[start, stop]`, `expire_at`,
y de quién es (teléfono / `calendar.booking`). Se consulta **restando capacidad**
en el camino que Visar ya controla — `_visar_resource_free_at` /
`_visar_resource_load` — así que protege **los dos canales** con un solo cambio.

Ciclo de vida:

1. El cliente elige horario en WhatsApp → se crea el hold (**10 min**) y se prepara
   el pago (§7).
2. Pago confirmado → `calendar.event` por la vía nativa → el hold se libera.
3. Sin pago a los 10 min → caduca. El slot vuelve a ofrecerse y al cliente se le
   avisa que su apartado venció (el buzón `visar.wa.message` ya sabe caducar
   avisos; mismo patrón).

### 6.1 La liga de pago vive y muere con el hold (decidido)

Modelo mental: **butaca de cine / Ticketmaster**. El horario se aparta al elegirlo,
la liga sirve mientras el apartado viva, y al caducar la liga deja de pagar — no
se cobra algo que ya no se puede entregar.

- La liga **no** es un enlace de pago suelto: cuelga del `calendar.booking`, y
  este del hold. Si el hold caducó, el destino de la liga **rechaza el pago** y
  ofrece volver a elegir horario, en vez de cobrar y fallar después.
- Esto **cierra el hueco que hoy existe** y que nadie atiende: sin esta regla, un
  pago tardío pasa, `_filter_unavailable_bookings` descarta la reserva en silencio
  y el cliente se queda pagado y sin cita.
- Al caducar se avisa al cliente por WhatsApp ("tu apartado venció, ¿elegimos otro
  horario?") y se le puede devolver directo a la lista de días — el buzón
  `visar.wa.message` ya sabe encolar y caducar avisos.
- **Renovar el apartado** (el cliente sigue en el chat y pide más tiempo) es
  simplemente crear un hold nuevo si el slot sigue libre. Conviene definirlo
  explícitamente para no dejarlo al azar.

> ⚠️ Punto fino de implementación: hay que asegurarse de que **no queda ninguna
> vía de pago viva** cuando el hold muere. Si la liga es un portal URL con
> `access_token`, el rechazo tiene que ocurrir del lado del servidor al procesar
> el pago, no solo escondiendo el botón.

### 6.2 Otros detalles

- **Limpieza:** cron corto (o `@api.autovacuum`) que borre los vencidos; la
  disponibilidad debe filtrar por `expire_at > now` de todos modos, para no
  depender del cron.
- 10 min es cómodo para capturar una tarjeta en el celular; el costo es tener
  inventario apartado. Ajustable por `ir.config_parameter`.
- **Doble hold del mismo cliente:** si vuelve a elegir horario, se libera el
  anterior. Un teléfono no debe poder apartar tres slots a la vez.

## 7. Hand-off de pago — cambia la decisión 10 del doc 29

El doc 29 eligió **opción A** (deep link que **siembra la sesión** del wizard) y
dejó la **opción B** (endpoint que prepara y devuelve una liga) documentada como
fallback *"solo si A da problemas en pruebas"*.

**La premisa cambió, no falló A.** Aquel diseño suponía un hand-off a media
captura. Ahora todo se recoge en el chat y la liga aparece **en el pago**:

- Una liga de WhatsApp abre un navegador **sin sesión**, quizá en otro dispositivo,
  quizá dos veces. Sembrar una sesión para volver a recorrer el wizard hasta el
  final es el camino torcido.
- Lo que se necesita es una URL **independiente de sesión y reabrible**:
  `portal_mixin.get_portal_url()` con `access_token`, o `payment.link.wizard`
  (ambos existen en esta versión).

**Recomendación: opción B**, mediante un método RPC acotado que resuelve/crea el
partner, crea el `calendar.booking`, arma el pedido y devuelve la liga de pago.

> ✅ **Validado en el servidor (17-ago-2026)** sobre una copia `visar-scratch`
> (todo dentro de `savepoint` + `rollback`, sin `commit`). El riesgo que estaba
> abierto aquí **queda cerrado**: se replicaron los cinco pasos del controlador en
> `odoo shell` y funcionan sin petición HTTP.
>
> - `_cart_add` **no toca `request`** (en el shell, `odoo.http.request` lanza
>   `RuntimeError: object is not bound`, así que si lo usara reventaría a gritos).
> - **Paridad de precio confirmada**, incluido el caso que más importa: interior
>   (tramo 501–1000) + exterior (tramo 101–500) en Zona B salen como **una sola
>   línea de variante combinada a 2,000.00**, no como la suma de 1,000 + 1,600.
> - **Ojo con qué total se compara:** los precios son **IVA incluido**, así que la
>   cotización cuadra contra `amount_total`, **no** contra `amount_untaxed`
>   (2,000.00 vs 1,724.14 + impuesto). Es la misma confusión `price_total` /
>   `price_subtotal` que ya mordió el PDF del upsell de campo.
> - **Descuento de combo** llega y sobrevive en la línea guardada (50% verificado),
>   incluso agregando otra línea después de la descontada.
> - **Póliza headless** funciona: lista (zona × plan) aplicada, línea recurrente +
>   `_visar_sync_anticipo_lines()` generando los periodos adelantados.
>   Detalle: `order.plan_id` lo fija **`_cart_add`**, no `_visar_apply_zone_pricelist`
>   (justo después de aplicar la lista sigue en `False`).

### 7.1 ⛔ Los `items` NUNCA se arman a mano

**La trampa más peligrosa de todo este diseño**, encontrada al validar lo anterior.

`_visar_interior_exterior_pair` decide quién es interior y quién exterior por
`dimension.measure_type`, pero `_visar_combined_variant_for_tiers` lee los ejes de
tamaño **de los tramos que le pasaste**. Son dos fuentes independientes, y el
`measure_scope` del tramo es **contraintuitivo respecto a su nombre**:

```
tier 3  '51 – 100 m²'    scope=exterior   int_axis='1-250'      ext_axis='51 - 100'
tier 5  '251 – 500 m²'   scope=interior   int_axis='251 - 500'  ext_axis='0 - 50'
```

Emparejar la dimensión *Interior* con el tramo 3 (cuyo scope es *exterior*) —una
suposición razonable leyendo los nombres— **no da error**: devuelve una línea
combinada a la variante base, **600.00 en vez de 2,000.00**. Un tercio del precio,
en silencio.

El wizard web nunca cae en esto porque resuelve los items con
`_visar_resolve_wizard_items(selections)`, que elige el tramo vía
`dimension._visar_tier_field_name()`. **No hay ninguna validación que atrape un
emparejamiento mal hecho.**

> **Regla dura del diseño:** el flujo de WhatsApp arma `selections` (las respuestas
> del cliente) y **siempre** obtiene los items con
> `_visar_resolve_wizard_items(selections)`. Ni el runtime ni el LLM construyen
> dicts de `items` por su cuenta. Cualquier método RPC nuevo recibe `selections`,
> no `items`.
>
> Vale la pena, además, **añadir una validación** en
> `_visar_combined_variant_for_tiers` que rechace un tramo cuyo `measure_scope` no
> corresponda al eje al que se le está asignando. Hoy un error de programación ahí
> se cobra en dinero, no en excepción.

### 7.2 La liga de pago: qué API usar

Ambas existen en esta instalación y funcionan:

| API | Devuelve |
|---|---|
| `order.get_portal_url()` (tras `_portal_ensure_token()`) | ruta **relativa** `/my/orders/<id>?access_token=…` |
| `payment.link.wizard` | URL **absoluta** con `web.base.url` |

**Para WhatsApp hay que usar la absoluta** (`payment.link.wizard`): un enlace
relativo no es tocable en un chat.

Verificado con `curl` anónimo, sin cookies, contra un pedido real:

- con token → **200**, la página renderiza y muestra el importe;
- sin token → 200 pero redirige a `/web/login`, correctamente cerrado;
- el mismo token en una segunda sesión limpia → **200**, es **reabrible** (justo
  lo que hace falta cuando el cliente abre la liga en otro dispositivo).

### 7.3 El pago hoy es SIMULADO, y eso está decidido

**Ningún proveedor de pago real está habilitado, y es deliberado.** Todo está en
`test` o `disabled` (Stripe `disabled`, PayPal `disabled`; solo *Wire Transfer*,
*Cash on Delivery* y *Demo* en `test`, y solo *Demo* publicado). **Stripe se
implementará más adelante**; por ahora los pagos se simulan y no comprueban nada.

Dos consecuencias, y la segunda es la que hay que diseñar bien:

**(a) Buena noticia: el flujo completo SÍ se puede construir y probar hoy.** El
proveedor *Demo* marca la transacción como pagada, así que la cadena entera
—`calendar.booking` → pedido → pago → `_make_event_from_paid_booking` →
`calendar.event` → tarea FSM— se dispara igual. No hay que esperar a Stripe para
validar el diseño de punta a punta. Lo único que falta es el dinero.

**(b) Hay que dejar la UI/UX preparada para cuando el pago sea real.** Un pago
simulado es **instantáneo y siempre exitoso**; uno real no. Si el flujo se escribe
suponiendo lo primero, el día que entre Stripe hay que rehacerlo. Estados que el
diseño tiene que contemplar **desde ahora**, aunque hoy nunca ocurran:

| Estado | Hoy (simulado) | Con Stripe | Qué debe hacer el chat |
|---|---|---|---|
| Pago confirmado | siempre, al instante | lo normal | Confirmar cita y datos |
| Pago **rechazado** | nunca pasa | tarjeta declinada | Ofrecer reintentar **conservando el apartado** |
| Pago **pendiente / asíncrono** | nunca pasa | 3-D Secure, OXXO, SPEI, webhook tardío | **Ni confirmar ni soltar** — ver §7.3.1 |
| Cliente abandona la liga | pasa | pasa | Caduca el apartado (§6.1) |

> **Regla de diseño:** nada en el flujo debe asumir que "mandar la liga" y "el pago
> entra" ocurren en el mismo minuto, ni que el resultado es siempre éxito. El
> estado del pago se **consulta** (o se recibe por webhook), no se supone.

#### 7.3.1 El choque real: pago asíncrono vs. apartado de 10 minutos

Es el punto donde la decisión 8 ("la liga muere con el hold") necesita un matiz, y
conviene resolverlo antes de escribir código, no después.

Con Stripe, una transacción puede quedar **`pending`** (3-D Secure, o métodos
locales tipo SPEI/OXXO que tardan horas o días). Si el hold caduca a los 10 minutos
mientras hay un pago en vuelo, se cae en el peor caso posible: **el cliente paga y
el horario ya se le dio a otro.**

**Regla:** el hold se mide contra el *inicio* del pago, no contra su final. En
cuanto existe una transacción **no cancelada** para ese `calendar.booking`, el
apartado deja de contar el reloj y queda **congelado hasta que la transacción
resuelva** (`done` → confirma; `error`/`cancel` → libera y ofrece reintentar).

Es lo mismo que hace un cine: la butaca no se suelta mientras el cobro está en
proceso. Y es exactamente el matiz que **no** hace falta hoy —con Demo todo
resuelve al instante— pero que sí hay que dejar contemplado en el modelo del hold,
porque añadirlo después significa reabrir la parte más delicada.

Para métodos que tardan **días** (SPEI/OXXO), congelar el slot indefinidamente no
es viable: ahí la política tiene que ser explícita (¿se apartan 24 h? ¿se acepta
solo tarjeta por WhatsApp?). **Decisión de negocio pendiente**, no técnica — pero
solo aparece cuando el pago sea real, así que puede esperar sin bloquear nada.

## 8. Contrato RPC propuesto

Siguiendo la disciplina de `visar.agent.tools`: tipado, mínimo, sin nombres de
modelo ni dominios.

| Método | Tipo | Devuelve |
|---|---|---|
| `agent_booking_options(payload)` | lectura | Opciones válidas del paso actual (grupos, plagas, tramos, extras, planes) — para que el LLM **no invente** opciones |
| `agent_available_days(payload)` | lectura | Próximos N días con slots factibles (zona + capacidad + ruta) |
| `agent_day_slots(payload)` | lectura | Horarios factibles de un día concreto |
| `agent_hold_slot(payload)` | **escritura** | Crea el hold de 10 min; devuelve `expire_at` |
| `agent_prepare_booking(payload)` | **escritura** | `calendar.booking` + pedido + liga de pago (atada al hold) |
| `agent_request_handoff(payload)` | **escritura** | Lead + nota en chatter + actividad asignada (§9.1) |

Las tres escrituras cruzan la regla "el runtime no escribe en Odoo" **a
propósito**, igual que ya la cruzó `agent_track_lead`: `sudo()` acotado al método,
payload tipado, sin superficie genérica.

## 9. Lo que esto fuerza en el runtime

- **Motor de flujos (etapas E/F).** Es la pieza grande, pero está **diseñada** en el
  doc 29: estado por paso, escapes, rewind-and-replay, pantalla de revisión antes
  del hand-off.
- **Persistencia de conversación deja de ser diferible.** El estado del flujo *es*
  el payload del hand-off; no puede vivir en un store en memoria con TTL de 3 h.
  (Ver R-07 en `visar_fastapi/.context/90-improvements-later.md`.)
- **Límites de WhatsApp:** 3 botones o 10 filas. Tramos de m², listas de plagas y
  horarios los rozan o los superan → secciones y paginación.
### 9.1 Hand-off humano: mecanismo (decidido)

Varios finales de este flujo desembocan en un humano — *"ninguna fecha me
acomoda"*, *"la liga falló"*, *"mi apartado venció"*, CP fuera de cobertura,
teléfono ambiguo. Hoy el agente contesta *"en seguida te contacta un asesor"* y
**no pasa nada más**: nada se crea en Odoo y nadie se entera. Es la falla del
sistema manual reproducida en el sistema nuevo, con el agravante de una promesa
explícita.

**Mecanismo:** el hand-off aterriza en el **lead de CRM**, que es donde ya vive el
rastro de este cliente. No hace falta inventar nada:

1. **Resolver o crear el lead** con la misma lógica de `agent_track_lead`:
   pipeline de WhatsApp (`team_id`), lead abierto por
   `(visar_wa_phone_norm, visar_service_group_id)`, o uno nuevo en *Nuevo*.
2. **Nota en el chatter** (`message_post`) con el motivo del hand-off y el
   contexto ya recogido (servicio, m², CP, fechas ofrecidas y rechazadas). Es lo
   que evita que el asesor tenga que volver a preguntar todo — exactamente la
   pérdida de contexto que el proyecto viene a eliminar.
3. **Actividad asignada** (`activity_schedule`) sobre el lead, con vencimiento
   corto, para que aparezca en la bandeja de alguien y no solo en un historial que
   nadie mira. El responsable sale del equipo (o de un usuario configurable).

Ventajas de anclarlo aquí: reusa el pipeline, las etapas y el chatter que ya
existen; hereda la vista de CRM sin construir tablero nuevo; y el mismo lead sirve
después si la venta se cierra.

> Esto **cierra la nota transversal** "el hand-off es una promesa sin mecanismo"
> del backlog del runtime (`visar_fastapi/.context/90-improvements-later.md`), y
> deja de ser dependencia bloqueante de R-03 y R-06.

**Método RPC:** `agent_request_handoff(payload)` — tercera escritura acotada,
mismo patrón que `agent_track_lead` (`sudo()` acotado, payload tipado, sin
superficie genérica).

## 10. Fases sugeridas (revisadas tras la verificación en servidor)

**El pago real (Stripe) NO bloquea nada de esto** (§7.3): con el proveedor *Demo*
la cadena completa se dispara y el flujo se puede construir y probar de punta a
punta hoy. Lo único innegociable es que la UI/UX se escriba contemplando pago
rechazado y pago pendiente **desde el principio** (§7.3.1), aunque hoy nunca
ocurran — meterlos después obliga a reabrir el hold, que es la pieza más delicada.

1. **`agent_prepare_booking` + liga de pago.** Sube al primer lugar: el riesgo que
   la frenaba (§7) **ya está validado**, la liga funciona y es reabrible, y es la
   pieza sin la cual el flujo de WhatsApp no entrega nada. Incluye la regla dura
   de §7.1 (`selections` → `_visar_resolve_wizard_items`, nunca `items` a mano).
2. **Hold de slot.** Baja a segundo: sigue siendo necesario —WhatsApp introduce la
   concurrencia que hoy no existe— pero ya no se justifica como incendio (§3).
   Va **junto con** la fase 1, porque la liga muere con el hold (decisión 8).
3. **Motor de flujos + persistencia en el runtime.** La pieza grande. Con 1 y 2
   listas, es plomería contra partes terminadas.
4. **Predicado de factibilidad.** Baja al final: con un técnico y mediana de 2.5
   paradas al día (§5.3.2) rara vez cambia la respuesta. Se construye igual —es
   barato y el web lo aprovecha—, detrás de un flag, cuando entre el segundo
   técnico o suba el volumen.

   > **Revisado el 19-ago-2026.** Con el 20/40 confirmado, esto **sube de
   > categoría**: deja de ser una optimización de ruta y pasa a ser la regla que
   > decide qué horarios se pueden ofrecer. Sigue en cuarto lugar —con 2.5 paradas
   > al día el presupuesto casi nunca aprieta, y el chat es lo que falta para
   > entregar algo—, pero ya no es opcional, y el flag es para desplegarlo con
   > cuidado, no para dejarlo apagado.
   >
   > Lo que **abarata** este trabajo: Mapbox ya funciona en esta instalación
   > (`visar_field_app._visar_enroute_eta_minutes` llama a Directions con perfil
   > `driving-traffic` y cae a un fijo si falla). Hay token vivo y camino probado;
   > lo que falta es la Matrix API y el predicado, no la integración.
   >
   > Lo que lo **encarece**: las zonas no aproximan distancia (§5.3), así que no
   > hay pre-filtro gratis. Y el 22.4% de direcciones sin geocodificar (§5.3.1)
   > cae a la rama de degradar (§5.4), que con esta regla significa **ofrecer el
   > horario igual**: nunca rechazar por un dato que no tenemos.

> El cambio de orden respecto a la primera versión de este doc sale de dos datos:
> la concurrencia todavía no existe (§3) y la ruta casi nunca aprieta (§5.3.2).
> Si mañana entran técnicos o volumen, 4 sube.

## 10.1 Estado de implementación (17-ago-2026)

> **Escrito, compila, SIN correr contra una BD.** Las pruebas nuevas no se han
> ejecutado todavía: hace falta la corrida en el servidor sobre una copia
> (`visar-scratch`), que es la verificación que vale.

Hecho en `visar_appointment` (**v19.0.2.6.0**, necesita `-u`):
- [x] `visar.slot.hold` + ACL + cron de limpieza.
- [x] Override de `_get_resources_remaining_capacity` que descuenta apartados,
      con exclusión del dueño (`visar_hold_owner`) y por ids.
- [x] Liberación del apartado al confirmarse la reserva (`calendar.booking`).
- [x] Congelado/descongelado del apartado por estado de `payment.transaction`.
- [x] **Refactor:** `sale.order._visar_apply_delivery_address` y
      `_visar_fill_from_booking`, y `calendar.booking._visar_create_for_booking`
      bajados del controlador; el controlador ahora delega.
- [x] `_visar_selections_has_roedores` en el modelo (era un literal `== 'si'`
      duplicado; comparar por verdad booleana habría añadido roedores a toda
      reserva donde el cliente dijo que NO).

Hecho en `visar_whatsapp_agent` (**sin bump**: solo Python → basta reiniciar):
- [x] `agent_available_days`, `agent_day_slots` (lectura).
- [x] `agent_hold_slot`, `agent_prepare_booking` (escritura acotada).
- [x] `agent_request_handoff` + `_agent_open_lead` compartido con `agent_track_lead`.

Pruebas escritas (sin ejecutar): `test_slot_hold.py` (11 casos),
`test_agent_handoff.py` (5), `test_agent_prepare_booking.py` (guardias siempre;
paridad de precio se **salta** si la BD no trae catálogo real, para no dar un
falso verde).

Hecho en `visar_base` (**sin bump**: solo Python + pruebas → basta reiniciar):
- [x] **Guardia en `_visar_combined_variant_for_tiers`** (§7.1): si le cruzan los
      ejes lanza `ValidationError` en vez de devolver la variante base en
      silencio (600 en vez de 2,000). Solo rechaza el desajuste **definitivo**;
      `measure_scope = 'all'` sigue siendo legítimo en cualquiera de los dos ejes.
      Pruebas en `visar_base/tests/test_combined_variant_guard.py`.

## 10.2 Primera verificación en servidor (18-ago-2026) y correcciones

Corrida sobre `visar-scratch`. Encargo en
[`briefs/2026-08-18-verificacion-agendado-whatsapp.md`](./briefs/2026-08-18-verificacion-agendado-whatsapp.md).

**Lo que aguantó:** el refactor **no rompió nada** — `test_booking_partner` y
`test_poliza` verdes, y el wizard web sigue emitiendo UNA línea combinada con el
total correcto (1,400 = `_visar_quote_booking`), creando la dirección de servicio
y los anticipos de póliza. La liga de pago funciona anónima y reabrible. El
apartado sí esconde el horario a otros clientes y sí caduca sin cron.

**Tres fallos propios, corregidos:**

| # | Qué pasaba | Arreglo |
|---|---|---|
| **T3f** | 🔴 **Se cobraba y NO se creaba la cita.** El nativo `_filter_unavailable_bookings` consulta capacidad **sin contexto**, así que el override restaba el apartado **del propio cliente**, declaraba el horario sin cupo y descartaba la reserva — con el pago ya adentro. En **todas** las reservas por WhatsApp. El apartado provocaba justo el desastre que existe para evitar. | Override de `_filter_unavailable_bookings` que excluye los apartados de las reservas que se están filtrando |
| **T3h** | `agent_request_handoff` **lanzaba** con lead nuevo: escribía `visar_source='whatsapp_handoff'` y la Selection solo aceptaba `'whatsapp'`. Solo sobrevivía el caso que REUSA un lead — el único que las pruebas cubrían | Valor añadido a la Selection (`visar_crm` **v19.0.1.3.0**, necesita `-u`) + `try/except` para cumplir el "nunca lanza" del docstring |
| **T3e** | El dueño **no veía su propio horario apartado** en el listado: `agent_day_slots` nunca ponía `visar_hold_owner`. Reservar sí funcionaba, pero el listado es lo que el cliente ve | El teléfono del payload siembra el contexto en `_agent_slot_tree` |

**Dos molestias, corregidas:** el override costaba **~1 s por página de calendario
(+57%)** por consultar una vez por slot → ahora `_get_appointment_slots` precarga
una foto y se filtra en memoria; y comparar recordset contra cadena emitía un
`UserWarning` por slot → se filtra por tipo.

**Una prueba mal calibrada, no un error de precio:**
`test_interior_mas_exterior_es_una_linea_combinada` tomaba el primer tramo
exterior, que es el **incluido** (0–50 m²), donde el diseño emite dos líneas a
propósito. Ahora exige `is_free = False`.

**Hallazgo ajeno, más caro que todo lo anterior:** el web cobra **2,400** donde la
cotización dice **1,900** en fumigación + áreas verdes (pierde el descuento de
combo al pasar por `_update_address`). Es preexistente y del canal web. Detalle y
causa exacta en [`90-improvements-later.md`](./90-improvements-later.md) **I-11**.

## 10.3 Segunda verificación (18-ago-2026) — los tres fallos, cerrados

Encargo en [`briefs/2026-08-18b-reverificacion-correcciones.md`](./briefs/2026-08-18b-reverificacion-correcciones.md).

**Confirmado arreglado:** T3f (ya hay `calendar.event`, la tarea FSM aparece y el
apartado se libera — los cuatro síntomas invertidos), T3h (lead nuevo sin
excepción, `visar_source = whatsapp_handoff`, nota y actividad) y T3e. Y los
arreglos **no rompieron nada nuevo**: la generación de slots sigue viva (R4a), la
detección de indisponibilidad **real** sigue funcionando —un apartado ajeno sí
descarta la reserva— (R4b), y la foto de apartados es fresca por petición (R4c).
`test_booking_partner` 6/6 y `test_poliza` 21/21.

De regalo, R4c dejó ver algo que no se había probado: **un apartado hecho por
WhatsApp esconde el horario en el wizard web de inmediato**.

**Cinco cosas más, corregidas en esta tanda:**

| Qué | Por qué importaba |
|---|---|
| La prueba de regresión de **T3f no ejecutaba su aserción** — el `create()` omitía `product_id`, que es NOT NULL | El arreglo del fallo crítico pasó una ronda entera **sin cobertura**, y la prueba en rojo parecía culpa del arreglo |
| **Rendimiento a medias** (+31% en vez de +58%): la foto solo cubría la generación nativa; la segunda pasada (`_visar_filter_slots_multi_service` → `_visar_resource_free_at`) seguía consultando 221 veces | Ahora esa pasada también toma foto |
| **`agent_hold_slot` no comprobaba disponibilidad**: dos clientes apartaban el mismo horario y **quedaban fuera los dos** (a cada uno le estorbaba el del otro) | `agent_prepare_booking` sí validaba; el hueco era el RPC suelto |
| **La actividad del hand-off se asignaba al propio bot** (CRM auto-asigna al creador, que es el usuario RPC) | Rastro perfecto que no convoca a nadie: justo lo que el hand-off existe para evitar |
| El `try/except` del hand-off **no cubría** `message_post` ni `activity_schedule` | El "nunca lanza" del docstring no estaba cerrado |

> ⚠️ **Decisión de datos pendiente, no de código:** el equipo de CRM de WhatsApp
> **no tiene líder ni miembros**. El código ya nunca asigna al bot, pero si no hay
> humanos en el equipo tampoco hay a quién asignar (queda la nota y un `warning`
> en el log). Ponerle líder o miembros al equipo es lo que cierra el bucle.

## 10.4 Tercera verificación (18-ago-2026) — ✅ el slice de servidor queda VERIFICADO

Encargo en [`briefs/2026-08-18c-tercera-pasada.md`](./briefs/2026-08-18c-tercera-pasada.md).
Las cinco correcciones, confirmadas. **93 pruebas, 0 errores**, y los únicos 2
fallos son los preexistentes de `assertLogs`.

**El rendimiento cerró del todo.** Trayectoria del sobrecosto del calendario web:

| | ronda 1 | ronda 2 | ronda 3 |
|---|---|---|---|
| vs. baseline | +0.97 s (**+57%**) | +0.61 s (+31%) | **+0.04–0.20 s (+2–12%)** |
| consultas a BD por render | 1 por slot | 221 de 444 | **0** |

La segunda foto capturó exactamente las 221 que se escapaban. El sobrecosto que
queda está dentro del ruido de medición.

**Lo demás, confirmado:** el doble apartado ya se rechaza (`slot_taken`) y el
dueño puede **renovar** el suyo; el hand-off no agenda nada cuando no hay humano
(y deja `warning`), y salta al humano en cuanto el equipo tiene líder; el flujo
crítico de la ronda 2 sigue en pie (cita creada, tarea FSM, apartado liberado); y
el wizard web sigue dando UNA línea combinada a 1,400 = cotización.

**Un borde más, corregido en esta tanda:** `agent_hold_slot` resolvía el tipo de
cita con `resource.appointment_type_ids[:1]` — arbitrario si el técnico cuelga de
varios tipos, y **sin validar nada** si no cuelga de ninguno. Ahora se resuelve
por **modo** (igual que el resto del flujo) y, si no se puede determinar, se
rechaza: apartar sin comprobar es justo el bug que esa validación vino a cerrar.

**Lo que falta (ya no es de este slice):**
1. **Poner líder o miembros al equipo de WhatsApp en Odoo.** Es **dato, no
   código**: sin humanos en el equipo el hand-off deja la nota pero no agenda a
   nadie. Confirmado en la copia: con un líder, la actividad se asigna bien.
2. Decidir qué hacer con **I-11** (el web cobra 2,400 donde la cotización dice
   1,900). Dinero real, canal web, decisión aparte.
3. `test_partner_dedupe`: 2 fallos **preexistentes y ajenos** — `assertLogs` no
   funciona en este Odoo 19 (el logger queda en nivel 25 = TEST, así que INFO se
   filtra). La conducta es correcta, verificada a mano. No tocar sin arreglar el
   harness.
4. **Siguiente fase, plan aparte:** el motor de flujos del runtime + persistencia
   (**SQLite**) + el render de pasos en WhatsApp. Todo lo de Odoo que necesita ya
   está en pie y verificado.

## 10.5 El flujo del cuestionario baja al modelo (19-ago-2026)

> **Escrito, compila, SIN correr contra una BD.** Encargo de verificación en
> [`briefs/2026-08-19-verificacion-motor-de-flujos.md`](./briefs/2026-08-19-verificacion-motor-de-flujos.md).

Al empezar a diseñar los pasos del runtime (§2.3 del plan del runtime) apareció
que **faltaba más de lo que decía el plan**. El plan pedía dos cosas —
`agent_booking_options` y exponer `_VISAR_STEP_CLEARS` (§4.1)—; en el código hay
**cuatro** reglas del cuestionario, y las cuatro estaban en el controlador web:

| Regla | Dónde estaba | Por qué no se puede duplicar |
|---|---|---|
| **Podar** | `_VISAR_STEP_CLEARS` + `_visar_clear_downstream` | La tabla no lo dice todo: `_VISAR_CLEARS_TIERS` añade una regla de **prefijo** (`tier_*`) |
| **Secuenciar** | `_visar_wizard_next` | *No estaba en el plan.* Saber que plagas va tras motivo, y que un corte a valoración se salta las mediciones, es tan duplicable como los precios |
| **Normalizar** | inline en cada handler POST | *No estaba en el plan.* "Protección general" activa las tres categorías; "termitas" corta a valoración **solo en la rama correctiva** |
| **Ofrecer** | repartido entre handlers y plantillas | Es lo que el plan llamaba `agent_booking_options` |

Las cuatro viven ahora en `appointment.type`
(`visar_appointment/models/appointment_wizard_flow.py`). El controlador delega y
se queda con lo suyo —sesión HTTP, formularios, URLs—: **1961 → 1664 líneas**.

**Decisión: se expone la operación, no la tabla.** Publicar `_VISAR_STEP_CLEARS`
por RPC obligaría al runtime a reimplementar la regla de prefijo de los tramos,
que es exactamente la divergencia que esto viene a cerrar. Lo mismo con la
secuencia y con la normalización: el runtime **pregunta**, no deriva.

**Decisión: un solo RPC, no dos.** `agent_booking_step(payload)` sustituye al
`agent_booking_options` del plan y le añade la secuencia y la normalización.
Recibe `{booking, step, answer}` y devuelve `{selections, zone_id, items,
delivery_address, extras_accepted, step, options, sequence, requires_valuation,
done, error}`. Es **lectura**: no escribe nada en Odoo, el estado se lo queda el
runtime. Sin `step` solo consulta —para retomar una conversación estacionada sin
tocar nada—. Con `error` lleno, el paso **no se mueve**: se vuelve a preguntar lo
mismo con el motivo.

Esto **cierra la decisión abierta §4.1** por la opción (a), que era la
recomendada.

Un fallo encontrado al escribirlo, y corregido: la cadena posterior a la
dirección (extras → póliza → horario) **rebotaba**. Contestar los extras no hace
desaparecer la oferta, así que arrancar la cadena desde el principio devolvía al
cliente al paso que acababa de contestar, para siempre. Ahora se recorre **desde
el paso contestado** (`_visar_wizard_step_after`), y el web usa la misma cadena.

Pruebas escritas (sin ejecutar): `visar_appointment/tests/test_wizard_flow.py`
(poda por prefijo, independencia interior/exterior, cortes por motivo, secuencia,
la cadena que no rebota, serializabilidad de las opciones) y
`visar_whatsapp_agent/tests/test_agent_booking_step.py` (round-trip del estado,
que el RPC no decide nada por su cuenta, que nunca lanza con payload basura).

**Sin bump de versión en ninguno de los dos módulos:** es Python puro —ni campos,
ni modelos, ni ACL, ni vistas, ni datos—, así que basta reiniciar.

Dos cosas que quedan anotadas para la verificación, no resueltas:

- **Costo.** `_visar_wizard_step_sequence` llama a `_visar_wizard_poliza_context`,
  que cotiza de verdad. Después de la dirección, una respuesta del RPC puede
  correr eso hasta 3 veces. El web ya pagaba algo parecido por render; se mide
  antes de cachear (V7 del encargo).
- **Cosmético.** El contador "Paso X de Y" de la página de error del paso 1 puede
  dar un total distinto al de antes: esa ruta nunca le pasó las selecciones al
  contador. No afecta al `back_url` ni al flujo.

## 10.6 Tres fallos del primer uso real por WhatsApp (19-ago-2026)

Salieron de recorrer el agendado **como cliente**, no de las pruebas. Los tres
estaban en el tramo final del cuestionario, que es el que menos se había
ejercitado.

### (a) Los horarios se ofrecían en UTC

`agent_day_slots` devolvía `start`/`stop` en UTC naive y nada más. El runtime no
puede saber en qué zona está Visar —es configuración de Odoo
(`visar.agent.timezone`)—, así que pintaba esa cadena tal cual: un servicio de
las **4 de la tarde** se ofrecía como *"entre 22:00 y 23:00"*.

Ahora cada slot viaja con **dos relojes**, y cada uno sirve para una cosa:

| Campo | Zona | Para qué |
|---|---|---|
| `start` / `stop` | UTC naive | lo que se le devuelve a Odoo (`agent_hold_slot`, `agent_prepare_booking`) |
| `start_local` / `stop_local` | zona de Visar | lo único que se le puede enseñar a una persona |

La conversión se hace en Odoo a propósito: derivar la zona del otro lado sería
otra regla duplicada (§11).

### (b) La póliza no tenía forma de decir que no

El paso ofrecía los planes y nada más. En el web da igual —hay botón de
continuar—, pero en WhatsApp **el paso ES el menú**: sin una fila de "no", la
única respuesta válida era contratar, y el cliente que no quería póliza se
quedaba atrapado justo antes de elegir horario.

Ahora la última opción es siempre "No, gracias" (`VISAR_POLIZA_NONE = 0`). No
hizo falta tocar la normalización: `_visar_wizard_answer_poliza` ya descartaba
cualquier valor que no fuera un plan ofrecido.

De paso, la descripción decía `billing_period_display_sentence` — *"per month"*,
en **inglés**, porque su fuente es inglesa y se traduce con el idioma del usuario
que hace la llamada (`__system__`, `en_US`). Ahora dice precio y periodicidad en
español, que además es lo único que distingue a cuatro planes que hoy se llaman
casi igual (I-15). Y `agent_booking_step` sirve todo el cuestionario con
`lang='es_MX'`, como ya hacía `_agent_partner_services`.

### (c) Un cliente nuevo no podía reservar

**El caso que el agendado por WhatsApp existe para capturar.** Un teléfono sin
`res.partner` contestaba las diez preguntas, elegía horario, confirmaba la
revisión, y en vez de la liga recibía *"Falta el nombre del cliente."* — sin
salida, y sin escalar (`name_required` no estaba entre los motivos de hand-off).

Ahora `nombre` es **un paso más del cuestionario**, justo después de la
dirección, con `kind: 'free_text'` (una sola respuesta escrita; se distingue de
`text`, que son varios campos guiados). Va ahí y no en la puerta porque es el
único dato que se pide por gusto del sistema y no del cliente.

**Quién sabe si hace falta es el canal, no el flujo.** `agent_booking_step`
recibe `phone`, mira si hay cliente con ese número y pone `needs_name`. El
wizard web nunca la pone —allí la identidad la recoge el formulario nativo del
final—, así que para el web el paso no existe.

> ⚠️ Trampa encontrada al recorrerlo contra la base: `_visar_wizard_answer_address`
> no muta el booking, lo **rehace** desde cero (es el paso que resuelve zona e
> items), así que se llevaba `needs_name` por delante y el paso desaparecía justo
> donde tenía que aparecer. La bandera se repone después de aplicar la respuesta.
> **El Odoo falso del runtime sí conservaba la clave**, así que sus pruebas
> pasaban en verde: es el modo de fallo que avisa `60-conventions-testing.md`.

## 10.7 ⛔ La rama de valoración NO llega a horarios por WhatsApp

Encontrado al verificar lo anterior, **sin corregir todavía**. Contradice la
decisión 3 de §12 ("valoración: SÍ la maneja el agente, hasta el mismo paso de
horarios").

`_visar_wizard_next_step` corta a `valuation` en cuanto `requiere_valoracion`, y
`valuation` es un paso **terminal**: la dirección nunca se pregunta. Verificado
por RPC contra una copia de `visar-db`, eligiendo *correctivo → termitas*:

```
paso: valuation | requires_valuation: True
zone_id: None   | items: []
dias ofrecidos: {'days': [], 'message': 'No hay cobertura o servicio para esa consulta.'}
```

Sin dirección no hay zona; sin zona `_agent_slot_tree` no puede resolver
técnicos, así que **no hay ni un día que ofrecer** y el runtime escala a un
humano ("no encontré fechas disponibles"). Degrada con dignidad, pero el cliente
que reporta termitas, chinches o "no sé qué es" —los tres cortes a valoración—
**no puede agendar por WhatsApp**.

Falta además que el runtime mande `mode: 'valuation'` en `agent_available_days`,
`agent_day_slots` y `agent_prepare_booking`: hoy no lo manda en ninguno, así que
aunque hubiera zona se cotizaría como reserva normal.

Lo que hay que decidir antes de arreglarlo: en la rama de valoración **la
dirección sigue haciendo falta** (el técnico va a ir), pero no hay items que
resolver. O el paso de dirección se vuelve parte de la rama de valoración, o la
zona se pide por CP suelto (que es lo que ya proponía la decisión 6: *"el CP se
pide temprano"*). Ver I-17 del backlog.

## 10.8 Segunda tanda: multi-selección, corrección y avisos (19-ago-2026)

Cuatro cosas más del mismo recorrido como cliente. Las dos primeras son de
conversación; las dos últimas cierran huecos que este diseño ya había decidido y
nunca se implementaron.

### (a) Elegir varias cosas sin repetir la pregunta

WhatsApp **no tiene menús de selección múltiple**: una lista se cierra al primer
toque. Elegir tres plagas eran tres mensajes y tres veces la misma pregunta.

No hizo falta nada nuevo del lado de Odoo: `classify` ya existía desde la etapa B
para mapear texto libre a las opciones del momento. `classify_multi` es el mismo
clasificador **sin el desempate** — en un paso de multi-selección un empate no es
ambigüedad, es lo que el cliente quiso decir. Y contestar por escrito ahora
*contesta* el paso, sin rematar con "Listo".

Dos afinados que salieron de probarlo con el catálogo real:

* **La descripción es la segunda fila.** Nadie escribe "Rastreros"; escriben
  "cucarachas" — y esa palabra ya estaba en la descripción de esa misma opción,
  sin usarse. Va en un campo aparte (`Option.hints`) y **no mezclada** con las de
  la etiqueta, porque la descripción de "Protección general" nombra a las otras
  tres: sumarlas haría que escribir "roedores" eligiera dos cosas.
* **Las raíces se recortan a 5.** El catálogo no trae sinónimos y sus nombres
  están en la forma en que Visar los escribe: *"quiero fumigar"* no encontraba
  "Fumigación", porque la comparación es por prefijo. El menú principal no pasa
  por ahí — sus raíces están escritas a mano contra el corpus real.

### (b) Corregir UN paso, no volver a empezar

"Cambiar algo" en la revisión reiniciaba el cuestionario entero: diez preguntas
otra vez por haberse equivocado en una. Es lo que hace que nadie corrija nada y
confirme cosas mal, justo antes de pagar.

Tres piezas, las tres en el modelo:

| Pieza | Qué es |
|---|---|
| `steps` | la misma secuencia del indicador "Paso X de Y", con etiqueta corta |
| `ask` | volver a **preguntar** un paso sin contestarlo (solo de `steps`) |
| `schedule_key` | cadena **opaca**: si no cambia, el horario apartado sigue valiendo |

No hay modo "corrección" en ningún lado: se contesta como la primera vez y
`_visar_wizard_clear_downstream` tumba lo que dependía de la respuesta vieja.

`schedule_key` resume lo que condiciona la agenda —zona + tipo de cita por
dimensión, que es de donde salen los pools— y se publica opaca a propósito: el
runtime la compara y no sabe qué campos importan. Cambiar de tramo o de plan de
póliza cambia el precio y no la agenda, así que no se le cobran al cliente dos
toques por corregirlos.

> **Y la dirección no se vuelve a pedir.** Corregir un paso de arriba invalida
> los items, y el único sitio donde se recalculan es el paso de la dirección.
> Sin `_visar_wizard_reapply_address`, cambiar "interior" por "ambos" obligaba a
> **reescribir la dirección**: la pregunta más cara del cuestionario, y la que ya
> estaba bien contestada.

### (c) El agendado dejaba de hablar en los dos finales

Pagaba y nadie le confirmaba nada. O pasaban los diez minutos y su apartado moría
en silencio, con una liga que él seguía creyendo buena.

El transporte no se copió: subió a `visar.wa.outbox.mixin` (en `visar_base`), que
es lo que ya sabía hacer el buzón de la app de campo — encolar, cron, reintentar,
caducar, avisar en el chatter. `visar.wa.message` lo hereda sin cambiar un nombre
de campo; el buzón nuevo del agendado (`visar.wa.booking.message`) también.

Tres claves nuevas, y la tercera **dice la verdad sobre la liga**:

| Clave | Cuándo | Qué dice |
|---|---|---|
| `booking_confirmed` | la reserva se volvió cita | día y ventana confirmados |
| `hold_expired` | venció en la revisión, sin liga | "se soltó tu horario, ¿buscamos otro?" |
| `hold_expired_link` | venció con la liga enviada | depende de si el horario sigue libre |

**El `wa_id` exacto, no el nacional.** `owner_key` son los 10 dígitos, que es lo
que hace que "el mismo cliente" signifique lo mismo en todo Odoo; el runtime
identifica la conversación por el número completo, y de 10 dígitos no se
reconstruye. Campos `visar_wa_phone` en `calendar.booking` y `visar.slot.hold`.

Del lado del runtime, `/internal/booking-event` **no es un `send-notification`
más**: primero aplica el aviso a la conversación y luego envía. Un
"¿elegimos otro horario?" sin rebobinar el estado es una pregunta cuya respuesta
cae en el menú principal. El apartado vencido suelta `slot` y `hold` y **conserva
el cuestionario**: el cliente ya contestó diez preguntas.

### (d) La liga deja de cobrar lo que no puede entregar — §6.1, por fin

La decisión 8 estaba tomada desde agosto y **nunca se implementó**. El hueco era
el peor del flujo: la reserva pendiente no consume capacidad, así que un pago
tardío pasaba, `_filter_unavailable_bookings` descartaba la reserva y el cliente
se quedaba **pagado y sin cita**, en silencio.

La regla implementada afina la decisión, y es la que cierra también la pregunta
abierta de §6.1 sobre **renovar el apartado**:

* venció y **nadie tomó el horario** → se vuelve a apartar y el pago pasa. El
  cliente ni se entera; su lugar seguía ahí.
* venció y **el horario ya es de otro** → se rechaza ANTES de cobrar.

Volver a apartar no es cosmética: sin apartado, `_filter_unavailable_bookings` no
tiene qué ignorar y el nativo puede descartar la reserva *después* del cobro.

Se engancha en `payment.transaction.create` y no en el controlador del portal
porque por ahí no pasan todas las rutas de pago. Las reservas sin apartado (el
wizard web) no pasan por la regla.

> ⚠️ **Esta tanda SÍ necesita `-u`**, a diferencia de las anteriores: campos
> nuevos, modelo nuevo, cron, vistas y ACL. `visar_base`, `visar_appointment`,
> `visar_field_app` y `visar_whatsapp_agent`, y reiniciar `visar-fastapi`.

## 10.9 Retomar y corregir: los dos fallos del despliegue (20-ago-2026)

Salieron **de producción**, no de las pruebas, y los dos son la misma familia: el cuestionario
sabía avanzar, pero no sabía contestar *"¿qué me queda por preguntar?"*.

### (a) Retomar devolvía al paso de la dirección

Un cliente toca "Agendar" y lo primero que recibe es *"¿A qué dirección vamos?"*, sin haber
elegido servicio.

`_visar_wizard_next_step` **siempre** termina en la dirección, y no puede saber si ya se
contestó: el tramo posterior —extras, póliza, horario— lo marca `_visar_wizard_step_after`, y ese
solo corre **al contestar** un paso. Así que para cualquier estado con el cuestionario completo,
*"¿en qué voy?"* respondía *"en la dirección"*.

Retomando una conversación estacionada eso significa pedir otra vez **la pregunta más cara del
cuestionario**, que además ya estaba bien contestada. Ahora, si la zona y los items están
resueltos, se sigue por la cadena. **No** se re-aplica la dirección: retomando no ha cambiado
nada que obligue a recalcular items — a diferencia de corregir un paso de arriba, donde sí.

### (b) Corregir un paso re-preguntaba todo desde ahí

Reportado tras el despliegue: *"volver atrás a cambiar algo te hace contestar TODO otra vez desde
esa pregunta"*. La intención era la contraria — cambiar **ese** paso, re-preguntar solo lo que
dependía de él, y si no queda nada pendiente volver derecho al resumen.

**Faltaba la pregunta entera.** Había dos funciones para avanzar y ninguna para saber qué queda:

* `_visar_wizard_next_step` cubre hasta la dirección y nada más;
* `_visar_wizard_step_after` es *"sigue la cadena DESDE el paso que acabas de contestar"* — útil
  yendo hacia adelante, **inservible al corregir**: reanudaba la cadena en `nombre` y volvía a
  ofrecer extras y póliza aunque no dependieran de lo corregido.

`_visar_wizard_next_pending_step` contesta la que hace falta: **el primer paso sin contestar de
TODO el cuestionario**, tramo final incluido. Y con eso la regla que pedía el cliente sale sola,
porque la poda ya hacía su parte: lo que dependía de la respuesta vieja la perdió y aparece
pendiente; lo que no, conserva la suya y se salta.

### La semántica que esto obligó a fijar

Cómo se sabe que un paso del tramo final está contestado:

| Paso | Marca |
|---|---|
| `nombre` | `selections['nombre']` |
| `extras` | la **CLAVE** `extras_ids` existe (aunque esté vacía) |
| `poliza` | la **CLAVE** `poliza_plan_id` existe (aunque sea `False`) |

**Es la presencia de la clave, no su valor.** *"Dije que no"* y *"no me lo has preguntado"* tienen
que ser estados distintos, y la poda borra la clave justo cuando la respuesta deja de valer.

`extras_ids` **no** es lo que se compra —eso es `booking['extras_accepted']`— sino la marca de
"este paso ya se contestó".

> **Quien lea §10.8(b) sin esto se lleva el comportamiento anterior a `1199db5`**, que el propio
> commit describe como lo contrario de la intención.

---

## 10.10 La valoración cierra y la ruta se construye (20-ago-2026)

**Escrito, sin verificar en servidor.** Encargo en
[`briefs/2026-08-20-valoracion-y-factibilidad-de-ruta.md`](./briefs/2026-08-20-valoracion-y-factibilidad-de-ruta.md).

### (a) §10.7 / I-17 — el aviso deja de ser un callejón

`valuation` ya no es terminal **en el chat**: es un paso que se acusa (precio +
motivo, una opción) y de ahí se sigue al paso de dirección que ya existía. En el
web no cambia nada, porque la bandera que lo habilita —`valuation_inline`— la pone
**solo** `agent_booking_step`, igual que `needs_name`.

**Al construirlo salieron dos bloqueadores que este documento no nombraba**, y sin
ellos arreglar la secuencia no habría servido de nada:

1. **El paso de la dirección devolvía `no_items`.**
   `_visar_resolve_wizard_items` solo emite items para dimensiones con un tramo
   elegido (`tier_*`), y el corte por calificación **nunca elige tramo** — el corte
   existe justamente para no medir. Los items de la rama salen ahora de
   `_visar_wizard_valuation_items()`: un item, precio fijo, `is_valuation: True`.
   Es la misma lista que `_agent_booking_context` armaba a mano, subida al modelo
   para que no haya dos definiciones de "qué se vende en una valoración".
2. **Sin técnicos de valoración en la zona no había error tipado.** Se devolvía
   cero días en silencio, que es el mismo síntoma que I-17. Ahora es
   `no_resources`, como en el web.

**Y un bug de cobro que solo se ve con la rama abierta.** En el corte **mixto**
(`cobertura='ambos'` + banda de exterior con `is_valuation`), `booking['items']`
guardaba el item de *interior*: el resumen cotizaba el servicio de interior
mientras `agent_prepare_booking` cobraba la valoración. **La pantalla de revisión
mentía sobre el total.** Se cierra solo al cambiar de dónde salen los items.

De paso, dos cosas que faltaban por coherencia:

- **Los extras tampoco se ofrecen** cuando hay corte. Se ofrecían y luego
  `_visar_build_sale_lines` los tiraba, así que el cliente aceptaba add-ons que
  nunca aparecían en el total. La póliza ya estaba guardada (§4.1); los extras se
  habían quedado fuera.
- **`_visar_wizard_schedule_key` lleva el flag de valoración**: la valoración tiene
  tipo de cita y pool propios, así que corregir *termitas → cucarachas* no puede
  conservar el horario apartado.

**Quién decide el `mode` — desviación deliberada.** §10.7 e I-17 dicen que falta
que **el runtime** mande `mode: 'valuation'`. Se hizo al revés: **lo deriva Odoo**
(`_agent_booking_mode`). El runtime ya lleva `requires_valuation`, y hacerle llevar
además un modo son dos representaciones del mismo hecho, en el sitio donde este
proyecto ya se quemó dos veces (§11). Un `mode` explícito sigue ganando, para el web
y las pruebas. Usado desde `_agent_booking_context` **y** `agent_hold_slot`, ese
único cambio deja correctos los cuatro RPC de golpe.

**El runtime quedó más pequeño.** Desapareció `_valuation_notice` y el atajo de
`app/agent.py` que cortaba toda respuesta de paso al ver la bandera — y que habría
seguido saltándose la dirección aunque Odoo dejara de ser terminal. El aviso lo
pinta `render_step` como cualquier otro paso, y **el texto lo redacta Odoo**, que es
donde vive el precio.

### (b) §5 — la factibilidad de ruta, construida

- **`visar_base`**: `visar.mapbox.service` (token, Matrix, geocode) y
  `visar.travel.cache` (clave direccional, coordenadas redondeadas, TTL y cron).
  Va ahí porque `visar_appointment` y `visar_field_app` son **hermanos** y no se
  conocen: el único antepasado común es `visar_base`. No hizo falta añadir
  `base_geolocalize` — el servicio solo recibe y devuelve coordenadas.
- **`visar.zone.cp`** gana centroide perezoso: es el respaldo del §5.4 para el 22.4%
  de direcciones que no geocodifican, y cuesta **una** llamada por CP en toda la vida
  del sistema.
- **`visar_appointment`**: el predicado, con `presupuesto = 20 + (T − E)`, bordes sin
  restringir (decisión 9) y degradación en cada nivel.
- **Dos enganches**, no uno — ver la corrección del §5.5.
- **Un día sin paradas no gasta ni una llamada.** Es el caso (a) del §5.1 y, con
  mediana de 2.5 paradas/día, el caso más frecuente. Es lo que hace esto barato.
- **Tope de llamadas + interruptor de circuito**: el web pinta un **mes**, no los ~10
  días de una conversación, y un token muerto no puede volverse 30 timeouts.

**Lo que deliberadamente NO se hizo: re-validar el traslado al apartar o al cobrar.**
El filtro es del *listado*. Rechazar un horario ya pagado porque el presupuesto
cambió desde que se listó es el fallo T3f otra vez —dinero dentro, cita fuera— y es
peor que servir una ruta apretada.

> ⚠️ **Sigue sin comprobarse que el token de Mapbox SIRVA** (§13, I-07). Es V0 del
> encargo, y va antes que todo lo demás.

---

## 10.11 Tercera tanda: el final muerto de Información, y lo que se lee (20-ago-2026)

Recorrido completo como cliente por segunda vez. Un fallo de flujo y una tanda de
textos que, juntos, eran la diferencia entre "funciona" y "se entiende".

### (a) El final muerto: cotizar, decir que sí, y que no pase nada

La ruta *Información* cotizaba, remataba con "¿quieres agendarlo o tienes alguna
otra duda?" — y al contestar **"no tengo dudas, quiero agendar"** respondía que
eso *se hace manualmente con un asesor*. Teniendo el cuestionario funcionando en
la ruta de al lado.

Eran dos cosas, y las dos hacían falta:

* **El prompt no lo sabía.** El registro activo de `visar.agent.prompt` decía
  literalmente "Todavía NO puedes agendar citas". El texto vigente vive ahora en
  `.context/34-prompt-agente-informacion.md`, para que se pueda revisar en un
  diff: la base sigue siendo la fuente, pero dejó de ser el único sitio.
* **No tenía cómo hacerlo.** Se añadió la tool `start_booking`
  (`visar_fastapi/app/odoo/tools.py`). No consulta nada: es la forma que tiene el
  modelo de **soltar la conversación**. El runtime la detecta en los turnos
  nuevos del propio historial —igual que ya detectaba `quote_service` para el
  lead de CRM, sin tocar el loop— y cambia la ruta a *Agendar*, que arranca el
  cuestionario **en el mismo mensaje**, encabezado por la frase de enlace del
  modelo.

**Se decidió no prellenar.** El cliente ya dijo su CP, sus metros y su plaga en
la conversación, y el cuestionario se los vuelve a preguntar. Traducir lo que el
modelo entendió a `selections` (grupos, plagas, ids de tramo) sería armar el
estado de venta fuera de Odoo, que es exactamente lo que cobra un tercio del
precio sin dar error (§7.1). Se prefiere repreguntar a cobrar mal; prellenar
queda como mejora, y pasa por el CP temprano de la decisión 6.

**Lo que sigue yendo con un asesor**: CP fuera de cobertura, quejas, facturas y
clientes no residenciales. La valoración técnica se excluía también —era la rama
que no llegaba a horarios— y dejó de estarlo al integrar §10.10(a): ahora se
entrega al cuestionario como cualquier otra.

### (b) Una pista por paso, escrita por Odoo

El runtime ponía **una sola línea** debajo de toda pregunta de multi-selección
—*"Puedes elegir varias"*— y no servía para ninguna:

| Paso | Lo que hacía falta decir |
|---|---|
| Servicios | que se pueden elegir varios **servicios** (con dos, "varias" no se entiende) |
| Plagas, preventivo | que basta **una**: "Protección general" ya cubre las tres |
| Plagas, correctivo | que sí, que puede tener varias cosas a la vez |
| Interior/exterior | no una instrucción: una **recomendación** — "ambos" no cuesta más si el patio mide menos de 50 m² |
| Interior (medidas) | que se miden los metros **construidos**, no los del terreno |
| Extras | que se puede cerrar el paso **sin comprar nada** |

Qué decirle al cliente en cada paso es negocio, no presentación, así que la pista
viaja en `options['hint']` como el resto del cuestionario. El canal solo sustituye
`{done}` por el nombre real del botón, para no tener dos sitios que renombrar.

### (c) "Listo" no se leía como "mándalo"

El botón que cierra una multi-selección se llamaba *"Listo"*, y se leía como
*"ya entendí"*: el cliente marcaba sus opciones y se quedaba esperando. Ahora es
**"Listo, Enviar"** — el verbo va en la etiqueta. Cabe de sobra en los 20
caracteres de un botón, contador incluido ("Listo, Enviar (3)").

### (d) Un subtítulo no puede costar una lista

El canal elegía botones con ≤3 opciones y lista con más. Un reply button **no
tiene subtítulo**, así que en los pasos cortos la descripción no se degradaba:
*desaparecía*. Se veía en dos sitios a la vez:

* **Póliza** — dos planes que en el catálogo se llaman igual y un "No, gracias".
  Tres opciones → botones, y lo único que distinguía un plan de otro era justo
  esa línea (*"$450.00 al mes · ahorras $150.00"*). Llegaban dos botones idénticos
  y ningún precio.
* **Extras** — *"Estación antirroedores"* a secas, sin decir que son **3** ni
  cuánto cuestan. Se aceptaba o rechazaba un cargo a ciegas.

La regla ahora es: botones si caben **y** ninguna opción trae subtítulo. Y el
subtítulo de los extras lo redacta Odoo (`_visar_wizard_extra_description`), con
el desglose separado del total: el add-on se ofrece por paquete, así que sin
desglose el total parece el precio de una pieza.

### (e) Textos que llegaban cortados o incompletos

* **"Más de 1,000 m² (valo…"** — una fila de WhatsApp son 24 caracteres y los
  tramos se llaman *"Más de 1,000 m² (valoración técnica)"*. El paréntesis baja
  al subtítulo, que admite 72. La condición sale del **flag** (`is_valuation` /
  `is_free`), no de parsear el nombre: un consultor puede reescribirlo desde el
  backend.
* **"No estoy seguro de qué es"** llegaba como *"No estoy seguro de qu…"*. Se
  acortó a **"No estoy seguro"** (solo en el chat; el wizard web tiene sitio).
* **"¿De qué tamaño es el área?"** no decía de qué área. `interior` mide la casa
  y `dimensiones` mide lo que toque el servicio, así que dejaron de preguntar lo
  mismo.
* **La ventana de llegada** decía *"te damos una ventana de llegada, no una hora
  exacta"* — cierto y poco útil. Ahora dice el margen real: hasta una hora
  después, y por qué (rutas y servicios previos del día).
* **La confirmación de pago** no decía nada de la política. Ahora la lleva: las
  citas pagadas no se cancelan ni se reembolsan, y se reprograman sin costo con
  24 h. Es el único momento en que el cliente la lee, y es justo cuando acaba de
  pagar; enterarse el día que quiere cancelar es enterarse tarde.

## 11. Riesgo estructural: dos front-ends, un flujo

Esto crea un **segundo front-end sobre el mismo flujo de reserva**. Cada cambio
futuro de preguntas o de precios tiene que aterrizar en los dos, o divergen.

La mitigación es la que ya funciona: **toda la lógica en métodos de modelo** que
ambos canales llaman. Es exactamente por lo que hoy los precios del agente
coinciden al peso con los del wizard (paridad verificada en `visar-db`, ver
`visar_fastapi/.context/50-status-roadmap.md`). Ninguna regla nueva de negocio
debe vivir en el controlador web ni en el runtime: van al modelo.

### El riesgo se materializó dos veces, y conviene tenerlas contadas

1. **I-11** — el web cobra 2,400 donde la cotización dice 1,900.
2. **`6999839` (20-ago-2026)** — el runtime llevaba **duplicada** una regla de *"elige al menos
   una"* que Odoo **no tiene**, y dejaba el paso de extras **sin salida**: a *"¿quieres agregar
   algo más?"* no se podía contestar que no. Visto en producción.
3. **`aec86cf` (20-ago-2026)** — el Odoo falso del runtime espejaba `_visar_wizard_step_after`
   en vez de `_visar_wizard_next_pending_step`. No es una regla duplicada, es su prima: **el
   fake divergió del real y las pruebas siguieron en verde**.

El caso 3 es el modo de fallo del que avisa `60-conventions-testing.md`, y ya había mordido
antes: en §10.6(c) el fake conservaba `needs_name` donde Odoo lo tiraba, así que el paso del
nombre desaparecía **en producción** mientras las pruebas pasaban.

> **La regla operativa que sale de esto:** al tocar el contrato RPC se tocan **los dos lados** —
> método de Odoo **y** protocolo/cliente/**fake** del runtime. Un fake que no espeja al real no
> es una prueba: es una prueba que miente en verde.

## 12. Decisiones tomadas (17-ago-2026)

1. **Póliza: SÍ** se ofrece en WhatsApp (es el paso de mayor valor).
2. **Extras / add-ons: SÍ** se ofrecen en WhatsApp.
3. **Valoración: SÍ** la maneja el agente, como modo propio, hasta el mismo paso de
   horarios (sin medidas y sin póliza).
4. **Fecha: las dos formas** — lista de días factibles *y* texto libre con parser
   acotado + confirmación.
5. **Hold de slot: 10 minutos.**
6. **El CP se pide temprano**, y se usa para **precalentar** zona, pools, agenda y
   matrices de viaje mientras el cliente contesta el resto (§4.0).
7. **El bloque de 1 h se parte en 20 min de traslado + 40 min de servicio**
   (confirmado con Visar el 19-ago-2026). El bloque sale de
   `appointment_duration` (no horneado); el reparto es configuración. La parte de
   servicio puede cambiar a futuro; el predicado no depende de ello (§5.2).
8. **La liga de pago vive y muere con el hold** (modelo butaca de cine): al caducar
   el apartado, la liga deja de pagar (§6.1). Esto cierra el hueco del pago tardío.
9. **Bordes del día: sin restricción de viaje en v1** — el horario laboral ya lo
   impone Odoo; el punto de origen del técnico no está modelado (§5.2.1).
10. **Hand-off humano: lead + chatter + actividad asignada** (§9.1), vía
    `agent_request_handoff`.
11. **Los `items` se resuelven SIEMPRE con `_visar_resolve_wizard_items(selections)`**
    (§7.1). Los métodos RPC nuevos reciben `selections`, nunca `items`.
12. **La liga de pago se genera con `payment.link.wizard`** (URL absoluta), no con
    `get_portal_url()` (relativa) (§7.2).
13. **El pago sigue simulado por ahora; Stripe llega después.** No bloquea: se
    construye y prueba con *Demo*. Pero la UI/UX contempla **pago rechazado** y
    **pago pendiente** desde el primer día, y el apartado **se congela mientras
    haya una transacción en vuelo** (§7.3, §7.3.1).
14. **Los 20 min de traslado son un PRESUPUESTO entre paradas consecutivas, no un
    radio de servicio** (19-ago-2026). Con hueco por delante, el hueco se suma al
    presupuesto y un trayecto más largo sí se ofrece. La disponibilidad depende de
    quién reservó antes —primero que llega, primero que se atiende— y al cliente
    no hay nada que explicarle: **nunca ve la opción que no cabe**.
15. **Al cliente se le da una VENTANA de llegada, no una hora exacta** (19-ago-2026).
    "3 pm" significa *entre 3 y 4*. Es honesto con lo que pasa en la calle y evita
    la conversación de "dijeron a las 3 en punto". El chat redacta la ventana con
    el `start`/`stop` que ya devuelve `agent_day_slots` — no hace falta tocar el RPC.

## 13. Abierto

- ~~Validar que el pedido se arma sin sesión web~~ → **cerrado**, funciona (§7).
- ~~¿Hay token de Mapbox?~~ → **existe** (§5.3.1). Falta comprobar que el token
  **sirve**: nadie ha hecho una llamada en vivo a Mapbox todavía.
- **Stripe** (§7.3): planeado, aún no. Hoy el pago es simulado y no comprueba nada.
  **No bloquea** el desarrollo (Demo dispara toda la cadena), pero el flujo debe
  nacer con los estados de pago rechazado/pendiente contemplados.
- **Política para métodos de pago lentos** (SPEI/OXXO) vs. el apartado (§7.3.1):
  decisión de negocio, aparece solo cuando el pago sea real.
- **Validación defensiva en `_visar_combined_variant_for_tiers`** para que un
  emparejamiento tramo/eje incorrecto lance en vez de cobrar de menos (§7.1).
- ¿La duración debería variar por `items`? Hoy es 1 h fija y confirmada (§5.3.1).
- ~~**Renovación del apartado**: reglas exactas cuando el cliente pide más tiempo
  y el slot sigue libre (§6.1).~~ → **cerrado** (§10.8d): si el slot sigue libre
  se vuelve a apartar solo, al ir a pagar; si ya es de otro, se rechaza el cobro
  antes de que haya dinero de por medio.
- Punto de origen del técnico (§5.2.1). Más barato de lo que parecía: ya existen
  `hr.work.location` (3 registros) y `res.company`, pero **todos apuntan al mismo
  partner "Visar Home" y sin geocodificar (0,0)**, y ningún empleado tiene
  `work_location_id`. Geocodificar ese partner y asignar la ubicación a los
  empleados costaría menos que añadir un campo nuevo.
- `min_cancellation_hours = 720` con horizonte de 30 días: revisar con Visar
  (§5.3.1). No es de este diseño, pero hace la cancelación imposible.
- Presupuesto/tope de llamadas a Mapbox y qué hacer al agotarlo (¿degradar a
  zona?).

## 14. Correcciones a la documentación existente

Salidas de la verificación en servidor que **contradicen** lo que dicen otros docs:

- **La base se llama `visar-db`. `visar_prod` NO EXISTE** (no aparece en `psql -l`;
  `/etc/odoo/odoo.conf` trae `db_name = visar-db`). Existen `visar-db`, `visar-db-2`,
  `visar-db-pres`, `visar-db-rehearsal` y `visar-test`. Varios docs
  (`81-handoff-prod-server.md`, `25-field-app.md`, `27-whatsapp-agent.md`,
  `30-...-routing-implementation.md`, y el `50-status-roadmap.md` del runtime, que
  además indica `ODOO_DB=visar_prod` en el `.env`) usan el nombre equivocado.
- **Los módulos viven en `/opt/custom`**, no en una ruta `visar-homes/` del server.
- **I-07 del backlog está obsoleto** en su punto 1: el token de Mapbox sí existe.
  ⚠️ Pero **sigue sin comprobarse que SIRVA**: nadie ha hecho una llamada en vivo a Mapbox
  todavía. Es la primera tarea antes de encender la factibilidad de ruta.
- **I-11 del backlog no se arregla como propone** — ver la nota añadida ahí: el
  enlace `calendar_booking_ids` cae en **una sola** línea (la última agregada), así
  que filtrar por ese campo no protege la línea descontada.

### Añadidos el 20-ago-2026, al barrer la carpeta entera

- **`91-reunion-2026-06-22.md` contradecía el 20/40, invertido** (L83 y L176: *"40 min = 20 min
  de servicio efectivo + 20 min de traslado"*). Este documento no lo señalaba, así que la carpeta
  tenía **dos repartos opuestos del bloque de cita**. Corregido con una nota de superseded; el
  texto original se conserva porque es un **acta de reunión**.
- **`90-improvements-later.md` también usaba `visar_prod`** (I-08 e I-09) y no estaba en la lista
  de arriba. Corregido.
- **`00-overview.md`, `20-architecture.md` y `27-whatsapp-agent.md` decían "Fase 1: solo lectura,
  no agenda citas"**, falso desde `9e606c9`. `20-architecture.md` además afirmaba que el proyecto
  son **tres módulos**: son **siete**, y no en cadena lineal.
- **`40-decisions.md` no tenía ni una de las 15 decisiones del §12.** Espejadas.
- **`50-status-roadmap.md` no mencionaba el agendado en absoluto**, y su sección de entorno local
  describía la máquina de otra persona.
- **`29-`, `30-`, `31-` y `32-` seguían con cabecera "DISEÑO, no implementado" / "PLAN"** estando
  todos ejecutados. La decisión 10 del doc 29 no advertía de estar superada por el §7 de aquí.
