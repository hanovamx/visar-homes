# Encargo: la rama de valoración cierra + factibilidad de ruta (20/40)

> Contexto: [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md)
> §10.7 (I-17) y §5 (decisiones 7, 9 y 14). Son **dos cambios independientes** en
> el mismo commit: el primero desbloquea a clientes reales **hoy**; el segundo
> construye una regla que casi nunca cambiará la respuesta **mientras haya un solo
> técnico**, y que se vuelve importante en cuanto entre el segundo.
>
> Se pueden verificar por separado, y si el segundo sale mal el primero **no**
> hace falta revertirlo.

**Módulos a reiniciar (Python puro, NO hace falta `-u`): `visar_appointment`,
`visar_whatsapp_agent`.**
**Módulo que SÍ necesita `-u`: `visar_base`** (19.0.1.6.0 → **19.0.1.7.0**) —
modelo nuevo `visar.travel.cache`, su ACL, un cron y dos campos en
`visar.zone.cp`. **Si algo más te pide `-u`, avísame: significa que se me coló
algo que no debía.**

Runtime (`visar_fastapi`): commit nuevo en `main`, hace falta redesplegar.

Reglas de siempre: escrituras **solo sobre la copia**, nunca `commit()` en shell,
no modifiques código. La base es **`visar-db`** (`visar_prod` no existe); trabaja
sobre una copia desechable.

---

## Qué cambió

### 1. La rama de valoración llega a horarios (I-17)

`valuation` era un paso **terminal**: nunca se preguntaba la dirección, así que no
había zona, no había técnicos y no había ni un día que ofrecer. El runtime
escalaba a un humano con *"No encontré fechas disponibles"*. Los tres cortes a
valoración —**termitas, chinches y "no sé qué es"**— no podían agendar por
WhatsApp.

Ahora el aviso es **un paso que se acusa**: se le dice al cliente el precio y el
porqué, él confirma, y sigue por el paso de dirección que ya existía.

Al construirlo aparecieron **dos bloqueadores más** que ni §10.7 ni I-17 nombraban:

- **`_visar_wizard_answer_address` devolvía `no_items`.**
  `_visar_resolve_wizard_items` solo emite items para dimensiones con un tramo
  elegido (`tier_*`), y el corte por calificación **nunca elige tramo** — el corte
  existe justamente para no medir. Ahora los items de la rama salen de
  `_visar_wizard_valuation_items()` (un item, precio fijo, `is_valuation: True`).
- **Sin técnicos de valoración en la zona no había error tipado**, así que se
  volvía a cero días en silencio. Ahora devuelve `no_resources`, como el web.

Y **un bug de cobro** que solo se ve con la rama abierta: en el corte **mixto**
(`cobertura='ambos'` + banda de exterior con `is_valuation`), `booking['items']`
guardaba el item de *interior*, así que el resumen cotizaba el servicio de interior
mientras `agent_prepare_booking` cobraba la valoración. **La pantalla de revisión
mentía sobre el total.**

**Acotado al chat.** Hacerlo global movía el web: `_visar_wizard_next(selections)`
devolvería `/wizard/direccion`, que hace 302 a `/wizard/valoracion-aviso` — misma
página, un salto de más. Se acota con `booking['valuation_inline']`, que **solo**
pone `agent_booking_step`, igual que `needs_name`.

**Sobre el `mode`:** I-17 dice que falta que **el runtime** mande
`mode: 'valuation'`. Se hizo **al revés — lo deriva Odoo** de `selections`
(`_agent_booking_mode`). El runtime ya lleva `requires_valuation`; hacerle llevar
además un modo son dos representaciones del mismo hecho, justo donde este proyecto
ya se quemó dos veces (I-11, `6999839`). Un `mode` explícito sigue ganando.

### 2. Factibilidad de ruta (§5)

No existía **ni una línea**. Hoy se ofrece cualquier horario con capacidad, sin
mirar si el técnico puede llegar.

- **`visar_base`** gana `visar.mapbox.service` (token, Matrix, geocode) y
  `visar.travel.cache`. Va aquí porque `visar_appointment` y `visar_field_app` son
  **hermanos** y no se conocen: el único antepasado común es `visar_base`.
- **`visar_appointment`** gana el predicado: `presupuesto = 20 + (T − E)`.
- Se engancha en **dos** sitios. El §5.5 dice "un predicado más dentro de
  `_visar_filter_slots_multi_service`", y **eso no basta**: la rama de valoración no
  pasa por ahí. El propio repo ya lo tenía escrito en `visar_slot_hold.py`.

**Perfil `driving`, no `driving-traffic`:** este último admite 10 coordenadas por
Matrix, y 9 paradas (el pico medido) + 1 destino son exactamente 10. Además, con
`min_schedule_hours = 24` el tráfico de ahora es ruido.

**El flag `visar.travel.enabled` nace ENCENDIDO** (§10: *"para desplegarlo con
cuidado, no para dejarlo apagado"*).

---

## Qué verificar

### V0 — ¿El token de Mapbox SIRVE? **Esto va primero.**

Nunca se ha hecho una llamada en vivo (§13, I-07 punto 1). El token existe y tiene
101 caracteres; que funcione está **sin comprobar**.

Desde `odoo shell` sobre la copia, una llamada de Matrix real entre dos puntos de
Monterrey:

```python
env['visar.mapbox.service']._visar_mapbox_matrix(
    [(25.6866, -100.3161), (25.7000, -100.3000)])
```

Debe devolver una matriz 2×2 de segundos. **Si falla, para aquí y repórtalo:** V4
a V8 quedan sin sentido y la factibilidad queda inerte-pero-segura (degrada a
ofrecer todo, que es el comportamiento de hoy). **V1–V3 siguen siendo válidos** y
son los que desbloquean clientes.

### V1 — El web, intacto. Es lo único que puede romper clientes hoy.

1. Una reserva web **completa**: fumigación interior+exterior, total de siempre
   (1,400 = una línea combinada), dirección de servicio creada, anticipos de
   póliza.
2. **Y el corte a valoración POR WEB:** correctivo → termitas debe seguir cayendo
   en `/wizard/valoracion-aviso`, con su "Volver", y continuar a
   `/appointment/<id>/visar/valoracion?from_wizard=1`.
   > **Cualquier redirect de más es un fallo del acotado.** Si el web ahora pasa
   > por la dirección antes del aviso, `valuation_inline` se está filtrando donde
   > no debe.

### V2 — La rama de valoración, de punta a punta por RPC

Recorre `agent_booking_step` eligiendo **correctivo → termitas**:

1. Sale el paso `valuation` con `kind: 'single'`, **una** opción, y un título que
   dice **el precio y el motivo**.
2. Se contesta y el paso siguiente es **`address`** (no `valuation` otra vez, no
   `schedule`).
3. Se contesta la dirección: `zone_id` resuelto, `items` con **un** item
   `is_valuation`, y **sin error**.
4. `agent_available_days` devuelve **días ≠ vacío**. ← *esto es I-17 cerrado.*
5. **No** se ofrecen extras ni póliza.

Pruébalo **por JSON-RPC de verdad**, no solo en shell: un recordset colado en
`options` revienta ahí y no en el shell.

### V3 — Se cobra la valoración, no el servicio

`agent_prepare_booking` completo: el pedido lleva el **producto de valoración** a
`_visar_valuation_price()`, el `motivo_valoracion` llega al chatter de la cita
(paridad con `from_wizard=1`), y la dirección de servicio se crea.

**Y el caso mixto:** `cobertura='ambos'` + banda de exterior `is_valuation`. El
total del **resumen** (`summary`) tiene que ser **el mismo** que el del pedido. Antes
no lo era.

### V4 — Zona sin técnicos de valoración

Quita los técnicos de valoración de una zona (en la copia) y contesta la dirección
en esa zona: debe salir un error **tipado** (`no_resources`) con `missing_services`,
no cero días en silencio.

### V5 — La carga sale de `appointment.booking.line`

Cuenta las paradas de **Pedro Martínez** en su día más ocupado, por los dos
caminos, y **reporta los dos números**:

```python
env['appointment.booking.line'].search_count([...])   # el bueno
env['project.task'].search_count([('user_ids','in',...)])  # el que NO se usa
```

§5.3.2: 83 tareas activas están asignadas a *admin*, 4 a `__system__` y 61 a nadie.
Si el predicado leyera de ahí, diría que el técnico tiene el día libre.

### V6 — El predicado filtra de verdad (y por las razones correctas)

Sobre la copia, fabrica una parada lejana para el técnico en un día y comprueba:

- el slot **pegado** a esa parada (empieza cuando ella acaba) **desaparece**;
- el slot con **una hora de hueco** por delante **sobrevive**, aunque el trayecto
  sea el mismo. ← *esto es la decisión 14; si desaparecen los dos, alguien lo
  implementó como un radio.*
- el **primer** slot del día sobrevive aunque el trayecto sea largo (decisión 9).

### V7 — Coste

Pinta un mes del calendario **web** y una `agent_available_days`, con el flag
apagado y encendido. Reporta:

- cuánto tarda cada una;
- **cuántas llamadas a Matrix** se hicieron (sale un INFO en el log al topar);
- si un mes entero supera `visar.travel.matrix_max_calls` (12 por defecto).

> El "~10 llamadas por reserva" del §5.3 se calculó para el **chat**. El web pinta
> un **mes**. El corto-circuito de días sin paradas es lo que debería mantenerlo
> barato — si no lo hace, ese número decide si sube el tope o se acorta el horizonte.

### V8 — Degradar, nunca bloquear

Pon un token inválido (en la copia) y repite V6: **ningún horario puede
desaparecer** y nada puede lanzar. Igual con una dirección sin geocodificar.

Y con `visar.travel.enabled = 0`, el árbol tiene que salir **idéntico** al de hoy.

### V9 — Pruebas

- `visar_appointment`: `test_wizard_flow` (~11 casos nuevos de valoración),
  **`test_travel_feasibility` (nuevo)**, más `test_booking_partner`,
  `test_slot_hold`.
- `visar_whatsapp_agent`: los de siempre + `test_agent_booking_step`.
- Baseline: 174. Debería quedar en ~200.

> Los **2 fallos de `test_partner_dedupe` (`assertLogs`) son preexistentes y
> ajenos**: no los investigues.
>
> Ninguna prueba nueva se ha ejecutado contra una BD — aquí no hay uno. Si alguna
> sale roja, mira **primero** si llegó a ejecutar su aserción: en la ronda 2 de
> agosto una prueba que reventaba antes del `assert` pareció culpa del código.

### Datos que quiero de vuelta

1. **Qué fracción de las paradas resuelve a coordenadas**, y por cuál de los tres
   caminos de `_visar_travel_stop_coords` (pedido → tarea FSM → asistente). Es la
   única forma de saber si el orden que elegí es el correcto.
2. **Latencia de Matrix** (p50 y p95) y **tasa de acierto de la caché** en una
   segunda corrida.
3. Si el centroide de CP se usó alguna vez (cuántos `visar.zone.cp` acabaron con
   `visar_centroid_lat`).

---

## Lo que NO es de este encargo

- **El CP temprano** (§4.0 / decisión 6): sigue sin construirse. Es la fase
  siguiente y es lo que haría barato el precalentado.
- **Stripe**: el pago sigue simulado (decisión 13).
- **Poner líder y miembros al equipo de CRM de WhatsApp** — sigue pendiente, es
  dato, y sin ello el hand-off escala **a nadie**.
- **Aprobación de plantillas por Meta.**
- **I-11** (el web cobra 2,400 donde la cotización dice 1,900).
- **I-15** (cuatro planes de póliza llamados igual) — es dato, no código, pero en
  WhatsApp son cuatro botones idénticos en el paso de mayor valor del flujo.
- **`min_cancellation_hours = 720`** con horizonte de 30 días, que hace la
  cancelación imposible. Hay que hablarlo con Visar.
- **Re-validar el traslado al apartar o al cobrar.** Es deliberado que NO se haga:
  rechazar un horario ya pagado porque el presupuesto cambió desde que se listó es
  el fallo T3f otra vez. Está dicho en el docstring del módulo.
