# Pólizas (suscripciones) — `visar_subscription`

> Módulo que faltaba en este índice. Cubre la venta de servicios como **póliza**
> (suscripción) y la generación de visitas FSM por periodo facturado.

## La regla de negocio

Una póliza se paga **dos meses por adelantado**. El precio de la póliza es menor que
el de una compra única, y ese pago inicial doble es lo que evita el abuso (contratar
póliza, recibir el servicio barato y cancelar).

> **La póliza es lo ÚNICO cancelable** (confirmado con Visar el 4-sep-2026). Una **cita**
> no se cancela: se **reagenda** — el servicio ya está pagado, y por eso
> `min_cancellation_hours = 720` con horizonte de 30 días deja la cancelación fuera de
> alcance **a propósito** (no es un error de captura; ver §5.3.1 del doc 33). La póliza sí
> se puede cancelar, y **tampoco hay reembolso**: lo cobrado por adelantado no vuelve.

Se configura por plan en `sale.subscription.plan.visar_first_invoice_periods`:

| plan | periodo | periodos adelantados | efecto |
|---|---|---|---|
| Póliza Mensual | 1 mes | **2** | cobra meses 1 y 2 de entrada; siguiente cargo en el mes 3 |
| Póliza Bimestral | 2 meses | 1 | su propio periodo ya cubre 2 meses |
| Póliza Trimestral | 3 meses | 1 | igual |

⚠️ En planes **anuales** nunca pongas 2: cobraría **dos años** por adelantado. La
migración `19.0.1.3.0` bajó a 1 los planes anuales que lo tenían mal, pero su filtro es
`billing_period_unit = 'month' AND billing_period_value >= 12`: **no alcanza a un plan
configurado como "1 año"** (`unit='year'`, `value=1`), que hay que revisar a mano.

## Visitas incluidas (independientes del cobro)

`sale.subscription.plan.visar_included_visits` declara **cuántas visitas incluye el plan
por factura**, sin tocar precio, impuestos, totales ni el calendario de facturación. Se
propaga a `sale.order.visar_included_visits`, que es editable por póliza (compute con
`store=True, readonly=False`) para poder ajustarlo sin cambiar el plan.

Existe porque antes las dos cosas iban atadas: el nº de visitas se derivaba de los
periodos cobrados por adelantado, así que la única forma de dar más visitas era cobrar
más de entrada. Con esto se puede vender un **plan anual de un solo pago con 12 visitas**:
periodos adelantados en 1, visitas incluidas en 12.

| valor | efecto |
|---|---|
| `0` (default) | comportamiento histórico: 1ª factura → tantas visitas como periodos pagados de entrada; siguientes → 1 |
| `N > 0` | **cada** factura genera N visitas por línea de servicio, sin importar los periodos cobrados |

Las N visitas del lote nacen sin fecha (salvo la primera, que hereda la cita del wizard)
y se numeran en el título — `Visita póliza 2026-08-10 — Fumigación (3/12)` — porque si no
quedarían N tareas idénticas en el tablero.

## Cómo está implementado (y por qué cambió)

**Antes:** el cobro doble era un multiplicador aplicado **al facturar**
(`_get_invoice_line_parameters` devolvía `ratio = 2`). El sitio web nunca pasa por ahí:
`website_sale` cobra exactamente `order.amount_total`, que es la suma de las líneas —
un mes. Resultado: el carrito enseñaba y cobraba 1 mes, la factura salía por 2, y como
la factura nunca quedaba pagada del todo, **`_invoice_paid_hook` no disparaba y no se
generaba ninguna visita**.

**Ahora:** el segundo mes es una **línea real del pedido** (producto `VISAR-ANTICIPO`,
no recurrente), añadida en el carrito antes de pagar. Así `amount_total` es lo que se
cobra, y el pago completo se aplica bien.

Piezas clave:

- `sale.order._visar_sync_anticipo_lines()` — una línea de anticipo **por cada** línea
  de servicio recurrente (no una sola sumada): así el IVA y el descuento de combo se
  reproducen exactos por línea, se pueden contar los periodos pagados de cada servicio
  en una póliza combo, y al quitar un servicio se va su anticipo (ondelete cascade).
- `sale.order.line._prepare_invoice_line()` — le pone al apunte del anticipo el
  `deferred_start/end_date` del **mes 2**. De ahí `sale_subscription` avanza
  `next_invoice_date` al mes 3 **solo**, sin tocar `_update_next_invoice_date`
  (hacer ambas cosas lo adelantaría el doble). De paso el ingreso diferido queda bien.
- `sale.order._get_update_prices_lines()` — **excluye** las líneas de anticipo del
  recálculo de precios. `_recompute_prices()` se dispara al escribir la dirección en el
  checkout y pone `price_unit` desde la lista y `discount` a 0; sin este filtro el
  anticipo caía a 0 y el cliente pagaba de menos **sin ningún aviso**.
- `sale.order._visar_visits_for_line()` — decide cuántas visitas genera una factura para
  una línea de servicio. Si la póliza declara visitas incluidas manda ese número; si no,
  cae al comportamiento histórico de la línea siguiente.
- `sale.order._visar_prepaid_periods_for_line()` — comportamiento por defecto (sin
  visitas incluidas): el nº de visitas del primer ciclo sale de las líneas de anticipo
  reales (lo que se vendió), no de la config del plan, que pudo cambiar después de
  firmar.

Se eliminaron `_get_invoice_line_parameters`, `_visar_should_extend_first_invoice` y
`_visar_is_first_poliza_invoice`. Este último dependía de `last_invoice_date`, que **no
está almacenado** (se calcula desde `next_invoice_date`, que la propia factura mueve):
por eso S00087 y S00088 recibieron dos facturas de 2 meses. Ver abajo.

## Precios: listas (zona × plan)

El precio de la póliza es el de la zona **menos el descuento del plan**. Como un pedido
solo puede tener UNA lista de precios, hay una lista por **(zona × plan)** con
exactamente **dos reglas globales**, ninguna con precio propio:

```
Zona C (periferia) — Póliza Mensual
  regla 1  sin plan → precio de Zona C, −0%    (add-ons, extras, roedores)
  regla 2  plan 3   → precio de Zona C, −5%    (el servicio recurrente)
```

**Los precios siguen viviendo solo en VISAR Zona A/B/C.** Cambiar un precio de zona
mueve a la vez el precio de compra única y el de póliza; no hay nada que sincronizar.
La regla 1 es la que hace que los extras cuesten exactamente lo mismo que en un carrito
de compra única.

### Cambiar el descuento de la póliza (consultores)

Ventas → Listas de precios → `<Zona> — <Plan>` → la regla **con plan** → *Descuento*.
Son **3 reglas por plan** (una por zona). Las listas viejas quedaron marcadas
`(heredada) …` y no seleccionables: siguen activas solo porque hay pedidos que las
referencian y sus renovaciones cotizan bien desde ellas. **No las borres.**

⚠️ Toda la cadena depende de que las plantillas 30 y 31 tengan `allow_one_time_sale`.
Si se apaga, los precios caen al `list_price` en silencio (mal por hasta 210 en zonas
A y C). Hay un test que lo fija.

## Qué precio se anuncia (y cuál no)

El precio de una póliza es **solo el servicio recurrente**. Los add-ons y extras
(estaciones antirroedores, etc.) son **cargo único en la primera factura**: no se
repiten cada periodo, así que meterlos en el precio "al mes" lo infla y no es lo que
se va a cobrar en el mes 3. Fue un bug real del primer corte del paso.

`_visar_quote_booking` separa siempre —con o sin plan— y devuelve:

| clave | qué es |
|---|---|
| `recurring_total` | servicio por periodo → el precio "al mes" de la póliza |
| `addons_total` | extras, cargo único |
| `upfront_service_total` | servicio × periodos adelantados |
| `upfront_total` | lo que se cobra HOY, extras incluidos |
| `total` | la reserva completa a precio de un solo pago |

Ejemplo real (fumigación interior 1-250, Zona B, + 3 estaciones a 100):
compra única 600 servicio + 300 extras = 900. Póliza Mensual: **570 al mes**,
hoy 1440 (= 1140 de servicio por dos meses + 300 de extras), siguiente cargo 570 en
el mes 3. El ahorro se calcula solo sobre la parte recurrente, que es la única que la
póliza abarata.

## Contratación desde el sitio web

La póliza ya **no** se contrata desde `/shop/...` (los productos 30 y 31 están sin
publicar). Es un paso del wizard de reservas, justo después de *¿Deseas agregar algo
más?*: `/appointment/visar/booking/wizard/poliza`. Al aceptar, el carrito lleva el
servicio con el plan, los extras como cargo único, y la línea de mensualidad
adelantada. La **primera visita hereda fecha y técnico** de la cita que el cliente
acaba de elegir; las demás nacen sin agendar.

## Una póliza combo es UNA visita, no dos (31-ago-2026, v19.0.1.5.0)

Una póliza de fumigación **+** mantenimiento de áreas verdes generaba **dos visitas por
periodo**, en dos proyectos distintos y a la misma hora: dos tarjetas para el técnico, dos
hojas, dos firmas, dos PDF y dos avisos de WhatsApp para el cliente. La venta puntual ya
consolidaba desde el 13-ago (`visar_fsm`); la póliza no, porque
`_visar_generate_period_visit` es otro camino — `visar_subscription` saca las líneas de
póliza de `_timesheet_service_generation` a propósito, para no crear la visita al confirmar
sino al **pagar** cada periodo.

**No era un problema de canal.** Parecía "por la web sale mal y por WhatsApp bien" porque
ninguna orden del agente había sido póliza todavía; el agente sí puede venderlas
(`visar_agent_tools` pasa `poliza_plan_id`) y le pasaba exactamente lo mismo.

Ahora ambos caminos agrupan por **proyecto efectivo** con el mismo primitivo,
`project.project._visar_effective_projects`, y con la misma configuración de siempre
(`visar_fsm_combined_project_id` en cada proyecto de servicio). Sigue sin haber nombres ni
ids de proyecto en el código.

| pieza | qué hace |
|---|---|
| `sale.order._visar_visit_groups()` | agrupa las líneas que generan visita por proyecto efectivo y decide cuántas visitas toca crear a cada grupo |
| `project.task.visar_source_line_ids` | todas las líneas que cubre la visita. Manda sobre el m2o `visar_source_line_id`, que queda como **representante** |
| `sale.order._visar_visit_service_label()` | título de la visita consolidada: *"Visita póliza 2026-08-30 — Fumigación + Mantenimiento de áreas verdes (1/6)"* |
| migración `19.0.1.5.0` | rellena el m2m de las visitas ya existentes desde el m2o |

Detalles que no son obvios:

- **La hoja de trabajo sale sola.** La visita nace en el proyecto anfitrión y Odoo copia de
  ahí `worksheet_template_id` (la plantilla del combo, ver `25-field-app.md`).
- **Guardia:** si las dos líneas piden **distinto** nº de visitas por factura (12 podas y 6
  fumigaciones al año), no se consolidan. Media consolidación dejaría al cliente sin las
  visitas de la diferencia.
- **La garantía no se consolida**: se repite el servicio que falló, no los dos. Va al
  proyecto de la primera línea; si reincidió el otro, se cambia a mano.
- **Siniestralidad:** una póliza combo cuenta ahora **la mitad** de servicios ejecutados y su
  tasa de garantía sube en consecuencia. Es lo correcto —la métrica cuenta visitas y la
  visita es una— pero las pólizas anteriores no se recalcularon: no son comparables.
- **Lo ya creado no se fusiona.** S00246 conserva sus 12 tareas; a partir de la siguiente
  factura pagada generaría 6. Mezclar hojas ya capturadas no tiene respuesta limpia.

## Pendiente / para revisar

### Bug B — S00087 y S00088 tienen dos facturas de 2 meses cada uno

Ambos pedidos recibieron **dos** facturas cubriendo 2 meses (periodos 07-01→08-31 y
07-20→09-19), es decir se facturó de más. Causa: `_visar_is_first_poliza_invoice`
leía `last_invoice_date`, un campo **calculado no almacenado** derivado de
`next_invoice_date` — que la propia facturación mueve. Cualquier cosa que empujara esa
fecha (una edición manual, una renovación, un churn/reopen) volvía a encender el flag
de "primera factura".

También se observó lo contrario en **S00084 y S00085**: su primera factura cobró **1
solo mes** cuando debía cobrar 2.

- **La causa está eliminada:** ningún pedido nuevo puede caer en esto.
- **Los pedidos existentes NO se tocaron.** Son hechos contables ya posteados;
  corregirlos es una decisión de finanzas (nota de crédito / ajuste), no de una
  migración. La migración los omite a propósito.
- **Acción:** revisar con finanzas los 4 pedidos y decidir nota de crédito o ajuste.

### Despliegue — YA APLICADO en producción (3-ago-2026)

Backup previo en `/var/lib/odoo/backups/visar-db-pre-polizas-20260803.dump`
(`pg_dump -Fc`, 22 MB). Se corrió sobre `visar-db`:

```
-u visar_base,visar_subscription,visar_appointment,visar_field_app
```

Se incluyeron `visar_base` (traía un delta pendiente de antes) y `visar_field_app`
(su fix del PDF estaba en disco pero **sin subir versión**, así que no se aplicaba).
Resultado: 6 listas creadas, 2 heredadas renombradas, 14 pedidos con anticipo, **41
omitidos** por tener factura posteada. Sin errores.

Versiones instaladas tras el despliegue: `visar_base` 19.0.1.4.0,
`visar_subscription` 19.0.1.3.0, `visar_appointment` 19.0.2.4.1.

**Cuatro pedidos cambiaron de total** al añadirles el anticipo: S00159, S00140
(ambos `3_progress`) y S00056, S00061 (`6_churn`, inertes). Si alguien iba a cobrar
los dos primeros, el importe ya no es el de antes.

### Sigue pendiente — manual, no es código

Repuntar el botón *Contratar póliza mensual* de la portada. Está en `ir_ui_view` id
**1186** (`website.homepage`), es contenido del editor web **sin fuente en git**, y
una actualización de módulo NO lo cambia. Debe apuntar a
`/appointment/visar/booking?restart=1` (los otros tres botones *Contratar ahora* de
la misma página ya usan esa URL). Mientras tanto sigue llevando a la página de
producto sin publicar.

### Verificación

```bash
# Nunca contra visar-db: es la base de producción de este host.
sudo -u odoo createdb visar-test
sudo -u odoo bash -c "pg_dump visar-db | psql -q -d visar-test"
sudo -u odoo /opt/odoo/venv/bin/odoo -c /tmp/odoo-test.conf -d visar-test \
    -u visar_subscription --test-enable --test-tags=/visar_subscription \
    --stop-after-init --http-port=8199 --gevent-port=8299 --log-level=test
```

Los tests cubren, entre otros, los dos fallos que costarían dinero en silencio:
`test_09` (el anticipo sobrevive a `_recompute_prices`) y `test_12` (un pago completo
no se clasifica como parcial — si el anticipo saliera de las líneas facturables, no se
crearía factura, no habría visitas y el dinero quedaría sin aplicar, sin ningún error).

## Las visitas que nadie agenda (paso 1, 16-sep-2026, v19.0.1.6.0)

De una póliza **solo la primera visita nace con fecha**: la que el cliente eligió en el
wizard. Las demás nacen sin agendar y, hasta esta versión, **nada ni nadie las agendaba**:
no había recordatorio, ni asignación automática, ni pantalla donde verlas. En una copia
exacta de producción del 16-sep había **150 visitas abiertas sin fecha** y **ninguna
visita posterior a la primera había recibido fecha jamás** — el caso más grave del corpus
de clientes es exactamente este (tres visitas sin rendir que nadie rastreaba tras la
salida de un empleado).

El paso 1 **no le escribe a ningún cliente**. Hace visible lo que se le debe a cada uno y
deja el dato que necesita el paso 2 (la invitación por WhatsApp para que el cliente elija
día, con el motor de horarios que ya existe).

### Una póliza no genera un solo tipo de visita

`project.task.visar_visit_kind`:

| tipo | qué es | cuenta en la serie | siniestralidad |
|---|---|---|---|
| **preventiva** | las visitas que el cliente compró, numeradas | sí | no |
| **correctiva** | refuerzos para erradicar una plaga activa, incluidos en el precio | **no** | **no** |
| **garantía** | reincidencia dentro de los 30 días | no | **sí** |

La correctiva es nueva y no es un lujo: una póliza correctiva necesita **varias visitas
el primer mes**, cuántas lo dice el técnico al ver el domicilio. Sin un tipo propio solo
había dos formas de registrarlas y las dos mienten: como garantía **inflan
`visar_warranty_rate`** (con la que se ajusta el precio en la renovación) aunque no haya
fallado nada, y como visita normal **le consumen al cliente una de las que pagó**.

`visar_is_warranty` no desaparece —lo usan el botón del pedido, la siniestralidad y las
búsquedas— pero pasa a **derivarse** del tipo (compute con inverse, almacenado), para que
no haya dos campos que puedan contradecirse. La migración llena el tipo en **pre-migrate**:
si el ORM cargara el cómputo con el tipo vacío, escribiría `False` en todas y Visar
perdería de golpe qué visitas fueron de garantía.

Botón nuevo en el pedido: **Visita de refuerzo** (`action_visar_add_corrective_visit`), al
lado del de garantía. No exige que la póliza sea correctiva: se pudo vender como
preventiva y el técnico encontrar plaga.

### La fecha propuesta

`visar_visit_due_date` contesta *para cuándo le toca*, mientras no haya fecha agendada.
Reglas, decididas con Visar el 15-sep:

- **El ancla es la fecha REAL de la visita anterior**, no la factura ni el pago. El
  cliente que eligió el día 20 espera que le toque cerca del 20, no el día que su banco
  liquidó el cargo.
- **Una visita al mes** por defecto, en todos los planes, desde
  `sale.subscription.plan.visar_visit_interval_months` (0 = este plan no propone fechas).
  No es una constante en Python: es un campo del plan, editable sin desplegar.
- **Se recorre de una en una**: agendar una visita tarde mueve la siguiente, no el
  contrato entero de golpe.
- **Correctivas y garantías no entran ni recorren la serie.** Una póliza con tres
  refuerzos el primer mes no queda tres meses adelantada.
- **Una serie por servicio, no por póliza.** Se agrupa por la línea representante, la
  misma que usa la consolidación: una póliza combo que va en una sola vuelta comparte
  serie, pero las 12 podas y 6 fumigaciones que el guardia de `_visar_visit_groups` deja
  aparte son **dos series de verdad**. Mezclarlas proponía 22 meses seguidos a un contrato
  de un año (salió en la copia de producción, no en un test).
- **La serie no retrocede.** Si una visita de más adelante se agenda antes de que le
  toque, el ancla avanza con `max`: sin eso dos visitas acababan propuestas para el mismo
  mes (también salió en la copia de producción).
- **Lo que no cabe en la vigencia se marca, no se fecha**
  (`visar_visit_due_out_of_term`). Proponer enero para una póliza que terminó en diciembre
  es inventar. Son las **visitas acumuladas**: por decisión de negocio **no caducan por
  ahora**, y cuando caduquen será una ventana configurable, no un despliegue.
- **Sin ancla no se propone nada.** Si ninguna visita de la póliza tiene fecha real, la
  serie no tiene de dónde colgar y la pantalla lo enseña como tal.
- **Una fecha escrita a mano manda** (`visar_visit_due_manual`) y ancla a las siguientes:
  el recálculo no la pisa.

El recálculo **no es un compute con `depends`**: dependería de las OTRAS visitas de la
misma póliza (una cadena, no un campo) y cualquier escritura en el lote recalcularía el
contrato entero. Se llama desde donde la serie cambia de verdad — se agenda una visita,
cambia su tipo, nace otra — y desde el botón *Recalcular fechas propuestas* del pedido.

> **Trampa de Odoo:** `planned_date_begin` **se descarta sin avisar** si se escribe sin su
> fecha de fin. Los tests tienen que escribir las dos (`_agendar`), y costó una ronda de
> fallos entenderlo.

### Arranque correctivo: de dónde sale

El par preventivo/correctivo **no es un campo en ninguna parte**: vive como respuesta del
guión en la cita (`appointment.answer.input`), en texto y con dos vocabularios según la
pregunta por la que se pasó ("Correctivo (plaga activa)" en la actual, "Plaga activa" en
la vieja). En producción, de las **25 pólizas con respuesta, las 25 son correctivas**.

`sale.order.visar_corrective_start` lo lee **una sola vez** y de ahí en adelante manda el
campo, editable: quien va al domicilio a veces encuentra otra cosa de la que el cliente
contó por teléfono. Las pistas de texto viven en el parámetro
`visar.poliza.pistas_correctivo` porque son **etiquetas** que se editan desde la interfaz.
El módulo **no depende de `appointment`** (una póliza existe igual sin cita): se comprueba
`'appointment.answer.input' in self.env`.

### La pantalla

*Field Service → Planning → **Visitas de póliza por agendar*** (gerentes de FSM):
preventivas abiertas sin fecha, con cliente, póliza, «N de M», fecha propuesta, días de
retraso y fin de la póliza. Vencidas en rojo, las de la semana en ámbar, fuera de vigencia
en gris. Filtros: vencidas, próximos 30 días, sin fecha propuesta, fuera de vigencia;
agrupable por cliente, póliza y mes.

### Lo que falta (negocio, no código)

1. **¿Cuántas visitas debe Suscripción Mensual?** Cobra 3 meses por adelantado y genera
   **1 visita por factura**, así que o el cliente recibe menos de lo que pagó o el campo
   de visitas incluidas está mal. **59 pedidos** con ese plan.
2. **El margen** de días con el que el cliente puede pactar otra fecha para una
   correctiva (el paso 2 lo necesita).
3. **Quién marca el tipo**: técnico en la app de campo, oficina, o por defecto según la
   póliza.

---

## Cómo se ofrece en el chat (3-sep-2026)

El paso de póliza del cuestionario era, en palabras del recorrido, *"demasiada
información junta y mal presentada"*. Y lo era por una razón concreta: los
cuatro planes **se llaman igual** en el catálogo (I-15), así que lo único que
los distinguía era una línea que encadenaba tres cifras —precio del periodo,
primer cobro, ahorro en pesos— en el orden en que se calculan. Cuatro filas
iguales y doce números.

Ahora cada plan dice, en este orden:

```
• *Póliza mensual*: Ahorro del 22% · $570.00 al mes · hoy pagas $1,710.00 por 3 meses
```

1. **El ahorro, en porcentaje.** Va primero porque es lo único de la línea que
   contesta la pregunta que el cliente se está haciendo, que no es "cuánto
   cuesta" sino "por qué me conviene". En porcentaje y no solo en pesos: un
   *"ahorras $150"* no se puede juzgar sin saber sobre qué, y obliga a dividir
   de cabeza en mitad de una conversación. Los pesos quedan de respaldo para
   cuando no hay base con la que comparar (`saving_percent` ausente).
2. **Cuánto y cada cuánto.**
3. **Lo que se paga hoy**, solo si el primer cobro va adelantado. Sin esto el
   cliente elige "570 al mes" y se encuentra 1 710 en la liga de pago.

Y el paso lleva **pista**: qué *es* una póliza, en una línea. Sin ella cada
opción tenía que explicarse a sí misma y la pregunta llegaba como un muro de
cifras antes de decir qué se estaba ofreciendo.

Los planes que **colisionan de nombre** llevan su periodicidad entre paréntesis
—y solo esos—: ponérsela a todos es ruido cuando los nombres ya se distinguen.
Es un parche mientras I-15 siga abierto; el arreglo de verdad es renombrarlos en
el catálogo.

> El **precio** no cambia con nada de esto: sigue saliendo de
> `_visar_quote_booking` con `plan`. Lo único que se tocó es qué se dice y en
> qué orden.
