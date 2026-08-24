# Encargo: el tiempo de viaje se pide con hora de salida (`depart_at`)

> Continuación de
> [`2026-08-20-valoracion-y-factibilidad-de-ruta.md`](./2026-08-20-valoracion-y-factibilidad-de-ruta.md),
> cuyo **V0 ya pasó**: el token de Mapbox sirve. Esto NO toca la rama de
> valoración; es solo el predicado de traslado.
>
> Contexto: [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md) §5.3.3.

**Módulos a reiniciar (Python puro, NO hace falta `-u`): `visar_base`,
`visar_appointment`.** No hay campo, vista, dato, ACL ni cron nuevos — solo
métodos. **Si algo te pide `-u`, avísame: significa que se me coló algo.**

Runtime (`visar_fastapi`): **sin cambios**. No hace falta redesplegar.

Reglas de siempre: escrituras **solo sobre la copia** de `visar-db`, nunca
`commit()` en shell, no modifiques código.

---

## ⛔ RESULTADO (ejecutado el 21-ago-2026): W1 FALLA, y hay un arreglo encima

**`depart_at` en Matrix es BETA con alta previa y esta cuenta no la tiene.** No es
que Mapbox lo ignore: **rechaza la petición entera** con `422 Request too large for
custom parameters ["depart_at"]` — mensaje engañoso, salta con una matriz de 2
puntos. El token está sano; la **Directions** API sí lo honra (501.8 s sin, 486.0 s
con). Alta en <https://www.mapbox.com/contact/matrix-api-depart-at>.

**Consecuencia, que es lo grave:** como todas las llamadas llevaban `depart_at`,
`_visar_mapbox_matrix` devolvía `None` siempre, el predicado marcaba `degraded` y
**el filtro quedó inerte en silencio**. Los cuatro casos del §10.10(c) —token bueno,
token inválido, destino sin coordenadas, flag apagado— daban 5 slots → 5 slots, con
un viaje real de 59.7 min contra un presupuesto de 20.

**Resuelto el 24-ago-2026 APAGÁNDOLO POR DEFECTO** (`visar.travel.depart_at = 0`),
no dejándolo en `auto`. El default tiene que ser el camino que se ha visto podar
horarios de verdad, no uno que depende de que un apagado automático salga bien —
`auto` paga un 422 por llamada antes de caer al camino bueno, y si la escritura del
interruptor falla (petición web de solo lectura) lo paga siempre.

**Apagado, el comportamiento es el de `825d536`, exacto:** sin franjas, una llamada
por (día, técnico), clave de caché sin franja, tope de llamadas de vuelta en 12.

El reintento-sin-hora sigue ahí para quien ponga `auto`. Ver **W7**, que ahora es
más corta.

> **El día que Mapbox conceda la beta:** `visar.travel.depart_at = 1` **y**
> `visar.travel.matrix_max_calls = 30`. Los dos, o con 12 y franjas un calendario
> mensual se queda a medio filtrar en silencio.

**Sigue vigente lo que ya pasó** y no hace falta repetir: W2 (una llamada por franja,
no por slot), W4.2 (claves con franja y direccionales), W5 (degrada sin bloquear), W6
(24 casos verdes; los 2 de `test_partner_dedupe` son ajenos). **W3 y W4.3 siguen
bloqueados** hasta que haya llamadas que respondan 200 — y hoy además no hay ni una
parada agendada en los próximos 30 días, así que un mes cuesta 0 llamadas.

**Dato que corrige el diseño (V5 del encargo anterior):** el pico real es de **10
paradas** en un día-técnico (Pedro Martínez, 11-ago-2026), no 9. Refuerza el descarte
de `driving-traffic`: 10 paradas + destino son 11 coordenadas, por encima de su tope
de 10. Ya corregido en §5.3 y en el código.

---

## Qué cambió, y por qué

Se pedía la matriz de tiempos **sin hora de salida**. Mapbox responde entonces con
velocidades típicas, sin hora del día — y ese error es **del mismo tamaño que la
magnitud que estamos midiendo**: el presupuesto son 20 minutos, y en Monterrey un
trayecto de 15 min a mediodía pasa de 20 en hora pico. Se estaba midiendo con una
regla cuyo error es tan grande como la cosa medida.

Ahora cada llamada lleva `depart_at`, y Mapbox responde con el tráfico **previsto**
para esa fecha y hora según 90 días de histórico. Es el dato correcto para una cita
de mañana o de dentro de tres semanas, y **no cuesta ni un elemento más**.

Tres cosas se movieron con ello:

1. **La hora que se manda es la de la PARADA, no la del slot candidato.** Es lo que
   evita que el costo se multiplique por slot. Se usa el punto medio de la parada.
2. **Las paradas del día se agrupan por franja horaria** (`depart_at` es de la
   petición, no de la coordenada: una matriz se cotiza a una sola hora). Ancho por
   defecto **3 h**, en `visar.travel.depart_bucket_hours`.
3. **La clave de caché lleva la franja.** Sin ella se serviría el tiempo de una hora
   ajena.

Y en consecuencia, **el tope de llamadas subió de 12 a 30**
(`visar.travel.matrix_max_calls`): con 12 y las franjas nuevas, un calendario
mensual se quedaba a medio filtrar **en silencio**.

> **Descartado a propósito: matrices asimétricas.** Se leen solo la fila 0 y la
> columna 0 de una matriz cuadrada, así que pedir solo eso ahorraría elementos —
> pero cuesta **dos peticiones en vez de una**, y lo que escasea son las peticiones
> (60/min) y la latencia de la página, no los elementos (100.000 gratis al mes).

### Antes de empezar: limpia la caché de viajes

Las entradas viejas tienen la clave sin franja y **no se van a encontrar** (no es un
fallo; el cron las barre solo). Para que las mediciones de abajo no salgan
contaminadas, en la copia:

```python
env['visar.travel.cache'].sudo().search([('kind', '=', 'travel')]).unlink()
```

---

## Qué verificar

### W1 — `depart_at` llega a Mapbox y cambia la respuesta

Desde `odoo shell` sobre la copia, la misma matriz a dos horas distintas de un día
laboral **futuro**:

```python
from datetime import datetime
S = env['visar.mapbox.service']
pts = [(25.6866, -100.3161), (25.7000, -100.3000)]
pico  = S._visar_mapbox_matrix(pts, depart_at=datetime(2026, 9, 1, 14, 0))  # 08:00 local
valle = S._visar_mapbox_matrix(pts, depart_at=datetime(2026, 9, 1, 18, 0))  # 12:00 local
print(pico, valle)
```

**Reporta los dos números.** Lo que se busca es que **no sean idénticos**: si lo
son, o `depart_at` no está llegando, o Mapbox no lo está honrando en esta cuenta —
y en ese caso todo este cambio es decorativo y hay que saberlo.

> `depart_at` está marcado **BETA** en Mapbox. Esta prueba es la que decide si se
> puede confiar en él.

Comprueba también que una salida en el pasado **no** manda el parámetro:

```python
S._visar_mapbox_depart_at(datetime(2020, 1, 1))   # -> None
```

### W2 — Una llamada por franja, no por slot

Es lo único que puede convertir esto en una factura. Sobre un día con paradas,
cuenta llamadas parcheando `_visar_mapbox_matrix`:

- un día con **una** parada → **1** llamada;
- un día con **dos paradas consecutivas** (p. ej. 9-10 y 10-11) → **1** llamada
  (misma franja de 3 h);
- un día con paradas de **mañana y tarde** (9-10 y 17-18) → **2** llamadas;
- **el número de slots del día no cambia ninguno de esos números.** ← *si sube con
  los slots, la hora de salida se está tomando del horario candidato y no de la
  parada; está mal implementado.*

### W3 — Coste real de un mes (repite V7 con la caché fría)

Pinta un mes del calendario **web** y una `agent_available_days`, con la caché
recién limpiada. Reporta:

- cuántas llamadas a Matrix se hicieron **en total**;
- cuánto tardó cada una;
- si se topó con el nuevo límite de 30 (sale un INFO en el log al topar).

> Esto es lo que decide si 30 es el número bueno o si hay que subirlo / acortar el
> horizonte / ensanchar la franja. **El número de este encargo es este.**

### W4 — La caché acierta y no mezcla horas

1. Repite W3 **sin** limpiar la caché: las llamadas deben caer a **casi cero**.
2. Comprueba que las claves llevan franja y que son direccionales:

```python
C = env['visar.travel.cache'].sudo()
print(C.search([('kind','=','travel')], limit=5).mapped('key'))
```

Deben verse como `25.6866,-100.3161>25.7000,-100.3000|D1H09`. **Reporta la tasa de
acierto** de la segunda corrida.

3. **Prueba el ancho de franja.** Pon `visar.travel.depart_bucket_hours` a 1 y a 6,
   limpia la caché y repite W3 en cada uno. Reporta llamadas y latencia de los tres
   anchos (1, 3, 6). Es el dato que fija el default.

### W5 — Degradar, nunca bloquear (sin cambios de expectativa)

Con token inválido, con dirección sin geocodificar y con
`visar.travel.enabled = 0`, **ningún horario puede desaparecer** y nada puede
lanzar. Con el flag apagado el árbol tiene que salir **idéntico** al de hoy.

### W6 — Pruebas

`visar_appointment`: `test_travel_feasibility` (5 casos nuevos de franja y
`depart_at`). Deberían pasar todos.

> ⚠️ **Ojo con esta suite:** el `@tagged('post_install', '-at_install')` estaba
> decorando un helper en vez de la clase, así que **hasta ahora corría con las
> etiquetas por defecto**. Ya está corregido. Si aparecen fallos que no habías visto
> antes, es porque ahora corre donde debía, no porque el cambio los haya causado —
> pero **repórtalos igual**.
>
> Los **2 fallos de `test_partner_dedupe` (`assertLogs`) siguen siendo preexistentes
> y ajenos**: no los investigues.

### W7 — El default apagado poda como `825d536`

Es lo único que hay que verificar. **Sin tocar ningún parámetro** (el default es
`visar.travel.depart_at = 0`), con la caché de viajes vacía, repite el escenario del
§10.10(c) — parada en el Centro, destino en García a ~59 min:

1. Los slots **pegados** a la parada tienen que **desaparecer**, como en `825d536`.
   Si siguen saliendo los 5, para y dímelo.
2. **Ninguna** petición a Mapbox debe llevar `depart_at`, y **ninguna** debe dar 422.
3. Un día con paradas de mañana y tarde tiene que costar **1** llamada, no 2.
4. Las claves de caché tienen que salir **sin** franja:
   `25.6866,-100.3161>25.8100,-100.5900` (sin el `|D1H09`).

Y la comprobación de contraste, que confirma que el interruptor sirve: con
`visar.travel.depart_at = auto`, ese mismo día debe dar **un** 422, un aviso en el
log con el enlace del alta, y acabar podando **igual** que en el punto 1.

---

## Datos que quiero de vuelta

**Ronda 2 (lo único pendiente):**

1. **W7: ¿vuelven a podarse los slots pegados a la parada?** Es la pregunta. Si
   siguen saliendo los 5, el arreglo no sirvió y hay que revertir a `825d536`.
2. Cuántas peticiones llevan `depart_at` después del primer 422 (debe ser **0**).
3. Confirmación de que el aviso del log sale **una vez**, no una por llamada.

**Bloqueado hasta que Mapbox conceda la beta** (o hasta que haya paradas reales
agendadas, porque hoy un mes cuesta 0 llamadas): W3, W4.1 y W4.3 — latencia,
tasa de acierto y el ancho de franja bueno.

---

## Lo que NO es de este encargo

- **La rama de valoración** — es del encargo anterior; aquí no se tocó.
- **Matrices asimétricas** — descartado arriba, con razón.
- **Google Routes API** — evaluado y descartado por ahora: mismo tráfico histórico,
  pero 5× el precio por elemento y 5.000 elementos gratis al mes contra 100.000. Se
  reconsidera si W1 sale mal o si los tiempos resultan visiblemente equivocados en
  campo.
- **El CP temprano** (§4.0 / decisión 6), **Stripe**, **I-11**, **I-15** y
  `min_cancellation_hours = 720` — todo sigue igual que en el encargo anterior.
