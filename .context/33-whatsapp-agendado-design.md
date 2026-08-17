# WhatsApp — Agendado completo en el chat (DISEÑO)

> **Estado: DISEÑO, no implementado.** Escrito 17-ago-2026. Retoma las etapas E/F
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

Orden real del wizard, según `_visar_wizard_next` y el grafo `_VISAR_STEP_CLEARS`:

| # | Paso web | Clave en `selections` | En WhatsApp |
|---|---|---|---|
| 1 | `services` | `group_ids` | Lista: Fumigación / Áreas verdes / Ambos |
| 2 | `motivo` (solo si hay fumigación) | `motivo` | Botones (≤3) |
| 3 | `plagas` | `servicio_plaga`, `requiere_valoracion`, `motivo_valoracion` | Lista (ojo al tope de 10) |
| 4 | `cobertura` | `cobertura` | Botones: interior / exterior / ambos |
| 5 | `group/<id>`, `dimensiones` | dimensiones por grupo | Lista |
| 6 | `interior` / `exterior` | tramo por eje | Lista de bandas de m² (**paginar**) |
| 7 | `direccion` | `delivery_address`, `zone_id` | Texto libre + confirmación |
| 8 | `extras` | `extras_accepted` | Lista multi-selección → varios turnos |
| 9 | `poliza` | `poliza_plan_id` | Lista de planes con comparativa |
| 10 | horarios | — | §5 |

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
horarios. La factibilidad de ruta (§5) aplica idéntica.

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
   `viaje(i → nuevo) ≤ hueco antes` **y** `viaje(nuevo → i+1) ≤ hueco después`.
3. Primera y última posición: ver §5.2.1 — el borde exterior **no** se restringe
   por viaje en la v1.

Sin paradas, cualquier slot con capacidad pasa (caso a).

**Duración del servicio.** Se asume **≤ 1 hora** (es lo que permite ofrecer
cualquier hora dentro de la ventana). El número **no se hornea**: sale de
`appointment_type.appointment_duration`, que es de donde ya lo toma
`_visar_filter_slots_multi_service`. Si mañana un servicio dura más, cambia la
configuración y el predicado sigue funcionando. Lo que **no** está resuelto es si
la duración debería variar por `items` (una fumigación de 800 m² no es una de 80);
hoy no varía, y si algún día varía, el hueco a comparar cambia con ella.

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

**Decisión v1: no restringir el borde exterior.** Solo se valida el viaje *entre*
paradas:

- si el slot cae **antes** de todas las paradas → solo se exige `viaje(nuevo → primera) ≤ hueco`;
- si cae **después** de todas → solo `viaje(última → nuevo) ≤ hueco`;
- en medio → las dos condiciones, como en §5.2.

Es conservador en la dirección correcta: puede aceptar un primer servicio lejano
que al técnico le cueste alcanzar desde su origen, pero **nunca** rechaza un
horario por un dato que no tenemos. Si más adelante Visar quiere modelarlo, la
dirección de la compañía como origen es un cambio de una línea en el predicado.

### 5.3 Control de costo

Ingenuo, esto serían miles de llamadas a Mapbox. Tres medidas, en orden:

1. **Zona primero, geometría después.** `visar.zone.cp` → técnicos elegibles ya
   poda casi todo el espacio **gratis**. Solo lo que sobrevive cuesta una llamada.
2. **Una llamada de Matrix por (día, técnico), no por slot.** Se pide de una vez la
   matriz entre las paradas del día y la dirección nueva; después **todos** los
   slots del día se evalúan con aritmética. La Matrix API de Mapbox admite hasta
   25 coordenadas por petición — de sobra para la jornada de un técnico.
3. **Caché de tiempos de viaje** por par de coordenadas redondeadas. Entre dos
   direcciones fijas el tiempo casi no cambia; no vale la pena volver a pagarlo.

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

### 5.6 Cómo se pide la fecha

Las dos formas, como se acordó:

- **Lista de los próximos N días factibles** (rápido, fiable, un tap). Es el camino
  principal; el tope de 10 filas obliga a paginar ("Ver más fechas").
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

**Lo que falta:**
1. Correr las pruebas en el servidor sobre `visar-scratch` (§Verificación del plan).
2. Prueba end-to-end a mano: días → apartar → liga → pagar con *Demo* → cita +
   tarea FSM → apartado liberado.
3. Confirmar que las pruebas existentes (`test_booking_partner`,
   `test_partner_dedupe`, `test_poliza`) siguen verdes **sin tocarlas**: es la
   red de seguridad del refactor.
4. Guardia defensiva en `_visar_combined_variant_for_tiers` (§7.1) — aún no.

## 11. Riesgo estructural: dos front-ends, un flujo

Esto crea un **segundo front-end sobre el mismo flujo de reserva**. Cada cambio
futuro de preguntas o de precios tiene que aterrizar en los dos, o divergen.

La mitigación es la que ya funciona: **toda la lógica en métodos de modelo** que
ambos canales llaman. Es exactamente por lo que hoy los precios del agente
coinciden al peso con los del wizard (paridad verificada en `visar-db`, ver
`visar_fastapi/.context/50-status-roadmap.md`). Ninguna regla nueva de negocio
debe vivir en el controlador web ni en el runtime: van al modelo.

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
7. **Duración del servicio: ≤ 1 h**, leída de `appointment_duration` (no horneada).
   Puede cambiar a futuro.
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
- **Renovación del apartado**: reglas exactas cuando el cliente pide más tiempo y
  el slot sigue libre (§6.1).
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
- **I-11 del backlog no se arregla como propone** — ver la nota añadida ahí: el
  enlace `calendar_booking_ids` cae en **una sola** línea (la última agregada), así
  que filtrar por ese campo no protege la línea descontada.
