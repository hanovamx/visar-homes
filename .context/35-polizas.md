# Pólizas (suscripciones) — `visar_subscription`

> Módulo que faltaba en este índice. Cubre la venta de servicios como **póliza**
> (suscripción) y la generación de visitas FSM por periodo facturado.

## La regla de negocio

Una póliza se paga **dos meses por adelantado**. El precio de la póliza es menor que
el de una compra única, y ese pago inicial doble es lo que evita el abuso (contratar
póliza, recibir el servicio barato y cancelar).

Se configura por plan en `sale.subscription.plan.visar_first_invoice_periods`:

| plan | periodo | periodos adelantados | efecto |
|---|---|---|---|
| Póliza Mensual | 1 mes | **2** | cobra meses 1 y 2 de entrada; siguiente cargo en el mes 3 |
| Póliza Bimestral | 2 meses | 1 | su propio periodo ya cubre 2 meses |
| Póliza Trimestral | 3 meses | 1 | igual |

⚠️ En planes **anuales** nunca pongas 2: cobraría **dos años** por adelantado. La
migración `19.0.1.3.0` bajó a 1 los planes anuales que lo tenían mal.

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
- `sale.order._visar_prepaid_periods_for_line()` — el nº de visitas del primer ciclo
  sale de las líneas de anticipo reales (lo que se vendió), no de la config del plan,
  que pudo cambiar después de firmar.

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

## Contratación desde el sitio web

La póliza ya **no** se contrata desde `/shop/...` (los productos 30 y 31 están sin
publicar). Es un paso del wizard de reservas, justo después de *¿Deseas agregar algo
más?*: `/appointment/visar/booking/wizard/poliza`. Al aceptar, el carrito lleva el
servicio con el plan, los extras como cargo único, y la línea de mensualidad
adelantada. La **primera visita hereda fecha y técnico** de la cita que el cliente
acaba de elegir; las demás nacen sin agendar.

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

### Runbook de despliegue

1. `visar_base` y `visar_appointment` traían una actualización pendiente previa
   (código en disco por delante del instalado). Al actualizar ahora entran **dos**
   deltas juntos — contarlo, no depurarlo a media noche.
2. Orden: `visar_subscription` primero (Fases 1 y 2, sin cambio visible para el
   cliente), verificar, y después `visar_appointment` (Fase 3, sí cambia el flujo).
3. **Manual, no es código:** repuntar el botón *Contratar póliza mensual* de la
   portada. Está en `ir_ui_view` id **1186** (`website.homepage`), es contenido del
   editor web **sin fuente en git**. Debe apuntar a
   `/appointment/visar/booking?restart=1` (los otros tres botones *Contratar ahora* de
   la misma página ya usan esa URL). Una actualización de módulo NO lo cambia.

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
