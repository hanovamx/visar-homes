# Encargo: un día es de una zona — agrupación de servicios por cercanía

> Contexto: [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md)
> **§5.7** (nuevo) y la última entrada de [`40-decisions.md`](../40-decisions.md).
> Se suma al presupuesto entre paradas del §5.2; **no lo sustituye**.
>
> Son **cuatro cambios**, y el orden importa: el **0** es requisito previo de los
> demás y es independientemente útil hoy. Si solo da tiempo a uno, que sea ese.

**Módulos:**
- `visar_base` — **necesita `-u`** (19.0.1.10.0 → **19.0.1.11.0**): campo de ajustes
  nuevo y poblado de centroides.
- `visar_appointment`, `visar_whatsapp_agent` — Python puro, **basta reiniciar**.

Reglas de siempre: escrituras **solo sobre la copia**, nunca `commit()` en shell.
La base es **`visar-db`**; `visar-test` es la copia utilizable. Y el log de Odoo
está en `/var/log/odoo/odoo.log` — **el código de salida de un `-u` que falla puede
ser 0 con la consola en blanco**.

---

## Qué hay que construir

### 0. Que las PARADAS dejen de quedarse ciegas — PRIMERO, y es un bloqueador

⚠️ **Ojo con dónde está el agujero, porque no es donde parece.** El **destino** ya
tiene respaldo: `_visar_travel_destination` cae al centroide del CP, y
`visar.zone.cp._visar_centroid()` lo geocodifica y **se lo guarda en el registro**,
una llamada por CP en toda la vida del sistema. Que la tabla tenga **1080 filas y 0
centroides** **no es un fallo**: es que esa rama no se ha pisado, porque la dirección
exacta viene resolviendo (**22** claves `addr:` en `visar.travel.cache`, todas con
punto). No hay que construir nada ahí.

**El agujero está en `_visar_travel_stop_coords`**, que resuelve las paradas ya
agendadas: pedido → tarea FSM → asistente, y si ninguno tiene `partner_latitude`,
`None`. **No tiene respaldo de CP**, y solo **48 partners** del padrón tienen
coordenadas.

Para el presupuesto entre paradas eso resta filtrado y ya. **Para la agrupación es
autodestructivo:** un día con todas las paradas sin geocodificar se lee como **día
vacío**, y un día vacío lo acepta todo — la regla se apagaría sola justo en los días
que peor conocemos.

Dos cosas, en este orden:

1. **Cuarto escalón en `_visar_travel_stop_coords`**: CP del partner de la parada →
   `_visar_centroid()`. Es el respaldo que el destino ya tiene, en el lado que no lo
   tiene. Sigue devolviendo `None` si tampoco hay CP — degradar, nunca bloquear.
2. **Precalentar los centroides en lote** (cron o script de una pasada), para que ese
   escalón no meta una geocodificación **dentro** del camino que pinta horarios. 1080
   CPs a 60 peticiones/min son ~18 min de reloj.

> No lo metas en el camino de pintar horarios. Geocodificar mientras un cliente
> espera una lista de días es exactamente el error que el §5.3 pasó tres revisiones
> evitando.

### 1. El predicado de agrupación

En `visar_appointment/models/visar_travel_feasibility.py`:

```
visar.travel.cluster_minutes   default = visar.travel.minutes + 10   →   30
```

⚠️ **El default se DERIVA.** No pongas `default=30` en el campo de ajustes: con
`config_parameter` eso hornea el 30 y lo desengancha del presupuesto base. El
parámetro sin poner significa *"derívalo"*, y el código cae a
`_visar_travel_minutes() + DEFAULT_CLUSTER_MARGIN`. `DEFAULT_CLUSTER_MARGIN = 10`.

Función nueva `_visar_travel_day_clustered(stops, durations, cluster_minutes)`,
llamada desde `_visar_travel_keep_slot` **además** de `_visar_travel_slot_fits` —
un slot sobrevive solo si pasa **las dos**:

- día **sin paradas** → pasa (y al reservarlo el día queda tomado por esa zona,
  emergente: sin estado nuevo);
- día **con paradas** → para **cada** parada con duración conocida,
  `max(hacia, desde) ≤ cluster_minutes`;
- parada **sin coordenadas** → no impone nada (§5.4, sin tocar).

**No añadas ni una llamada a Mapbox.** `_visar_travel_durations` ya trae ida y
vuelta de **todas** las paradas; hoy se tiran todas menos dos. Si tu implementación
necesita una llamada más, está mal.

Y el campo de ajustes en **Ajustes → Visar → Agendado**, con su `@api.constrains`
como el de `visar_travel_minutes` (un radio de día **menor** que el presupuesto
entre paradas es una configuración incoherente: recházala).

### 2. El orden de los días

`_visar_filter_slots_travel` marca cada día que sobrevive con su tier — **1** si ya
tiene una parada dentro del radio, **2** si está vacío. La geometría se queda en el
módulo de factibilidad; la capa del agente solo ordena.

Lo consumen **dos** sitios de `visar_whatsapp_agent/models/visar_agent_tools.py`:
`agent_available_days` y el listado de reagendado. En los dos, **el orden va ANTES
del corte de `MAX_AVAILABLE_DAYS = 10`** — ordenar después reordena los 10 primeros
días del calendario y nada más, que es justo no hacer nada.

> **Guarda obligatoria: los 2 días factibles más próximos entran SIEMPRE**, sea cual
> sea su tier. Sin ella, un día agrupado dentro de tres semanas le gana a un día
> vacío mañana y al cliente con prisa se le empuja lejos **en silencio** — no se le
> explica nada, así que no tiene forma de pedir algo antes.

**El web solo poda.** Un calendario mensual no tiene orden que expresar; el día
lleva el tier de más y le es inerte. Confírmalo, no lo supongas.

### 3. La documentación que queda mintiendo

- ~~El §10.10(c) del doc 33 decía que la rama que gasta llamadas de Matrix no llega a
  correr.~~ **Ya corregido el 4-sep** al escribir este encargo: era falso desde hacía
  semanas (10 paradas el 11-ago, 9 el 1-sep, 164 filas de caché hasta el 3-sep).
- La V6 del encargo del 20-ago dice *"si desaparecen los dos, alguien lo implementó
  como un radio"*. Sigue siendo cierta **para el presupuesto**, pero ya no describe
  el comportamiento completo. Acótala.

---

## Qué verificar

### V0 — Las paradas dejan de quedarse ciegas

Cuántos de los 1080 CPs acabaron con centroide, y **cuántos fallaron y por qué**. Toma
tres CPs de municipios distintos y comprueba a mano que el centroide cae donde debe
(no en el centro de Monterrey para todos — eso sería el geocoder rindiéndose).

**Y lo que de verdad mide esta V:** para los días del histórico con paradas, qué
fracción de las paradas resuelve a coordenadas **antes** y **después** del cuarto
escalón. Si no sube, la agrupación va a leer días llenos como vacíos y no sirve de
nada encenderla. El 4-sep el reparto era: 21 de 22 días multi-parada con ≥2 paradas
geocodificadas — mejor de lo que sugieren los 48 partners, porque las coordenadas
llegan por el pedido y la tarea FSM, no por el padrón.

### V1 — El predicado poda por la razón correcta

Sobre la copia, un técnico con **una** parada en el Centro y un destino en García
(~59 min):

- el slot **pegado** desaparece — ya lo hacía (presupuesto);
- el slot con **una hora de hueco** también desaparece **ahora** ← *esto es lo
  nuevo*. Antes sobrevivía, y era correcto que sobreviviera bajo la regla vieja;
- con un destino **a 3 min** del Centro no se poda nada, ni con hueco ni sin él.

### V2 — Las dos reglas se exigen a la vez

Dos paradas a **25 min** una de otra (dentro del radio de 30) con el slot candidato
**pegado** a una de ellas: la agrupación pasa, el presupuesto **no**, y el slot
tiene que desaparecer. Si sobrevive, alguien sustituyó el predicado en vez de
sumarlo.

### V3 — El día vacío pasa, y queda tomado

Día sin paradas → todos los slots. Reserva uno en García y **vuelve a pedir el mismo
día** desde un destino en San Nicolás: no puede quedar ni un horario.

### V4 — El orden, y la guarda

`agent_available_days` con un día agrupado lejano y días vacíos cercanos:

- el día agrupado sube en la lista;
- **y los 2 más próximos siguen estando**, aunque sean tier 2. ← *si esto falla, la
  guarda no se implementó y el cliente con prisa se queda sin opciones cercanas.*
- La lista sigue teniendo como mucho 10 días, y **el corte ocurre después de
  ordenar** (comprueba que aparece un día que antes caía fuera del top-10
  cronológico).

### V5 — Coste: CERO llamadas nuevas

Repite la misma corrida con la agrupación apagada y encendida y **cuenta las
llamadas a Matrix**. Tienen que ser **el mismo número**. Cualquier aumento es un
fallo de implementación, no un coste aceptable.

### V6 — Degradar, nunca bloquear

Token inválido, Mapbox caído, destino sin geocodificar, día con todas las paradas
sin coordenadas: **ningún horario puede desaparecer por la agrupación** y nada puede
levantar. Con `visar.travel.enabled = 0`, el árbol idéntico al de hoy.

> Borra `visar.travel.cache` antes de repetir esto, o la caché contestará por
> Mapbox y la degradación parecerá rota estando bien. Ya pasó el 21-ago.

### V7 — El web poda igual y no se rompe

Una reserva web completa de punta a punta, y el calendario mensual pintado: mismos
días que por WhatsApp (misma poda), sin el orden, y **sin que el tier de más
ensucie la plantilla**.

### V8 — Pruebas

`visar_appointment`: `test_travel_feasibility` gana los casos de agrupación.
`test_el_hueco_por_delante_se_suma_al_presupuesto` **sigue siendo válida** —prueba
el presupuesto, que no cambia— pero necesita un destino dentro del radio del día, o
la agrupación la tumbará por otra razón y la prueba dejará de probar lo que dice.

---

## Datos que quiero de vuelta

1. **Cuántos días de los próximos 30 sobreviven** con la agrupación encendida contra
   apagada, para un destino de cada municipio con volumen. Es el número que dice si
   30 min es el umbral correcto o hay que aflojarlo.
2. **Cuántos CPs quedaron con centroide**, y qué fracción de las paradas resuelve
   ahora a coordenadas, contra el 4-sep como línea base (21 de 22 días multi-parada
   tenían ≥2 paradas con punto; 1 no se podía evaluar).
3. Si algún día del histórico habría quedado **partido** — paradas reales que la
   regla habría mandado a días distintos. Con la mediana en 8.3 km y el p75 en 16.7,
   espero que sean varios; quiero saber cuáles.

---

## Lo que NO es de este encargo

- **Re-validar el traslado al apartar o al cobrar.** Sigue siendo deliberado que no
  se haga (fallo T3f). Con la agrupación el riesgo cambia de forma —dos clientes de
  lados opuestos pueden pagar el mismo día vacío y partirlo— y aun así es preferible
  a rechazar un horario ya pagado.
- **El agendado manual desde el backend.** Se salta el filtro, parte el día, y
  entonces la regla cierra el resto del día para las dos zonas. Es consecuencia
  conocida, no un bug que arreglar aquí.
- **Decirle nada al cliente.** Decidido el 4-sep: el día no aparece y ya.
- **Encender `depart_at`.** Sigue apagado por el 422 de la beta de Mapbox, así que
  los tiempos no llevan tráfico y el umbral de 30 hereda ese error. No lo toques en
  este encargo, pero tenlo presente al leer los números de V1.
- **El punto de origen del técnico** ("Visar Home", sin geocodificar). Los bordes del
  día siguen sin restringirse por viaje (decisión 9).
