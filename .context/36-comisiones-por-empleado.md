# Comisiones por empleado (módulo `visar_commission`)

Estado al **18-sep-2026**: la estructura está construida y probada; **la regla de
comisión sigue sin definirse con negocio**. El módulo no trae ningún plan sembrado
ni porcentaje por omisión: calcula lo que se configure.

## Por qué no se usa `sale_commission`

`sale_commission` y `sale_commission_subscription` están **instalados** y su motor
funciona. El problema es a quién le atribuye la venta. Su dependencia de
`res.users` está en cinco lugares, ninguno cosmético:

- `sale.commission.plan.user.user_id` apunta a `res.users` con dominio
  `share = False`. Eso **descarta también a los usuarios de portal**, que sí son
  gratis: no hay atajo por ahí.
- El SQL del reporte cruza las ventas por `sale_order.user_id` y las facturas por
  `account_move.invoice_user_id`. La atribución es del **documento completo**,
  según su vendedor de cabecera.
- Los ajustes manuales cuelgan de la línea plan-usuario.
- El pronóstico es por usuario.
- Las reglas de registro comparan contra `user.id`.

Es un módulo Enterprise (licencia OEEL-1): modificar su lógica interna se rompe en
cada actualización y compromete el soporte.

**El problema ya es visible en producción.** Hay un plan configurado, `Upsell`,
aprobado, mensual, 2026, con dos reglas al 5% sobre lo vendido en las categorías
*Upsell* y *Servicios Core*. Produce cifras (junio: $800.69) y **todas se las lleva
`admin`**, porque `sale_order.user_id` es admin en esos pedidos. Pedro Martínez,
que es quien vendió los 10 adicionales en campo y no tiene usuario, no aparece en
ninguna fila.

## Cómo funciona el nativo (lo que se copió)

Cuatro piezas:

- **Plan** — vigencia, periodicidad (mes/trimestre/año), estado aprobable
  (borrador → aprobado → terminado/cancelado). Solo los aprobados calculan.
- **Logros** — las reglas. Tipo (importe/cantidad, vendido/facturado) + filtro
  opcional por producto o categoría + tasa. Con `sale_commission_subscription` se
  suma el tipo MRR.
- **Periodos y metas** — los genera el plan según la periodicidad, cada uno con su
  meta y su **fecha de pago** (por la que se agrupa lo que sale en nómina).
- **Vendedores** — la lista de quién está en el plan, con vigencia individual.

Y dos modos de cálculo, que es lo que más se confunde:

- **Logros**: la tasa de cada regla *es* la comisión. 5% sobre vendido = 5% directo.
- **Metas**: lo logrado se compara con la meta y la comisión sale de una curva de
  tres puntos (0%, 50%, 100% → monto), interpolando en medio y **plano** por fuera.

Importes: el nativo siempre usa `price_subtotal` (**sin IVA**), y la venta cuenta
por `date_order` comparado en crudo contra el periodo (sin huso horario).

## Lo que este módulo hace distinto

Copiado tal cual: plan con vigencia y estado, periodos autogenerados, reglas con
filtro de producto/categoría y tasa, los dos modos de cálculo, ajustes manuales y
reporte por persona y periodo.

**Quitado a propósito** (el nativo lo necesita para millones de documentos; Visar
lleva ~213 pedidos confirmados en todo 2026): equipos de venta, maquinaria
multimoneda, pronóstico y vistas materializadas. El cálculo es Python sobre
`sale.order.line`: igual de rápido a este volumen, legible y comprobable.

**Agregado porque el nativo no lo hace:**

- **Base "cobrado"**, además de vendido y facturado. Importa por REQ-002: medido el
  17-sep-2026, **7 de 9 pedidos** con venta en campo estaban pagados en línea y
  **sin factura**. Con "facturado" esos vendedores no cobrarían comisión de una
  venta que sí entró. La base "cobrado" mira, en este orden: factura pagada o en
  proceso de pago → transacción de pago liquidada del pedido → sello de efectivo
  del técnico.
- **Atribución por LÍNEA.** El adicional que vende el técnico vive dentro del
  pedido del servicio (REQ-007) con su propio empleado en la línea. El nativo
  reparte el documento completo y no sabría separar al vendedor del técnico. Aquí
  la venta directa **excluye** las líneas de upsell a propósito: si el vendedor
  cerró la cotización y el técnico agregó un extra en la visita, el extra es del
  técnico. También se atribuyen los 10 adicionales históricos, que llevan al
  técnico en la **cabecera** del pedido aparte y no en la línea.
- **Cerrar un periodo.** El reporte nativo siempre recalcula: editar un pedido
  viejo cambia una comisión ya pagada. Aquí el periodo pasa a Cerrado → Pagado y
  las cifras se congelan; la guarda está en el plan **y** en el propio periodo,
  porque el botón de recalcular vive en el renglón del periodo.
- **Periodicidad quincenal**, que el nativo no tiene: del 1 al 15 y del 16 al fin
  de mes, como la nómina en México, no "cada 14 días".
- **Importe con o sin IVA, configurable.** Los precios de Visar están capturados
  IVA incluido: el subtotal de una venta de $350 es $301.72. Cuál de las dos es la
  base la decide el plan (`tax_base`), en vez de que lo decida el código.

## Modelos

- `visar.commission.plan` — el plan. `mode` (tasa|meta), `periodicity`
  (quincena|mes|trimestre|anio), `tax_base` (sin_iva|con_iva), `state`.
  `falta_configurar` enciende el aviso de la ficha y **bloquea la aprobación**.
- `visar.commission.plan.employee` — `hr.employee` con vigencia propia. Aquí está
  toda la diferencia con el nativo.
- `visar.commission.rule` — el logro: `base`, `origen` (venta_directa |
  upsell_campo | ambas), filtro producto/categoría, `rate`.
- `visar.commission.period` — periodo con meta, fecha de pago y estado
  (abierto|cerrado|pagado).
- `visar.commission.curve` — la tabla meta→comisión.
- `visar.commission.adjustment` — ajustes manuales; suman al **logro**, no a la
  comisión final, así que en el modo de metas también mueven el porcentaje.
- `visar.commission.line` + `visar.commission.line.rule` — el resultado, con el
  detalle por regla y las **líneas de venta exactas** que lo sostienen (sin eso
  nadie le cree al número).

Menú: *Ventas ▸ Comisiones (empleados)*. Se llama distinto al nativo
("Comisiones") a propósito: los dos siguen existiendo y calculan cosas distintas.

## Lo que falta definir con negocio (bloqueante para pagar, no para configurar)

Ninguna de estas respuestas necesita código: todas son configuración del plan.

1. **La regla**: ¿porcentaje fijo, distinto por categoría (Servicios Core,
   Servicios Especializados, Upsell, Pólizas y Suscripciones — las categorías ya
   existen con ese vocabulario), o escalonado por meta?
2. **La base**: ¿vendido, facturado o cobrado? Ver el dato de REQ-002 arriba.
3. **Venta directa vs upsell**: ¿misma regla o dos? El 10% al técnico que se
   menciona en el código del upsell está **sin confirmar**.
4. **Corte**: quincenal o mensual.
5. **Cierre**: ya está implementado; falta decidir quién tiene la autoridad de
   cerrar y pagar un periodo.
6. **Con o sin IVA** sobre qué se paga.

## Medición real (visar-test, copia de producción, 18-sep-2026)

Plan de prueba: Pedro Martínez, 10% de lo vendido en campo, 2026, mensual. Sobre
los 9 adicionales que tiene esa copia:

- **Vendido**: jul $1,293.09 + ago $2,112.06 = **$3,405.15** sin IVA → comisión
  **$340.52**. Cuadra exactamente con los $3,950 con IVA de esos 9 pedidos.
- **Cobrado**: **$2,801.71** → comisión **$280.18**. La diferencia son $603.45
  (los dos adicionales que nunca se cobraron: S00136 y S00150).

Los mismos adicionales, misma persona, **$60 de diferencia en la comisión** según
la base que elija el negocio. Es la pregunta 2, con número.

## Cómo probarlo

```bash
sudo -u odoo /opt/odoo/venv/bin/odoo -c /tmp/odoo-test.conf -d visar-test \
  -u visar_commission --test-enable --test-tags=/visar_commission \
  --stop-after-init --http-port=8199 --gevent-port=8299
```

31 pruebas. Dos hallaron errores reales durante el desarrollo: la composición del
dominio en notación polaca para el origen "las dos" (el OR se comía una hoja y la
regla medía cualquier cosa) y que la guarda del cierre solo estaba en el plan,
no en el periodo —que es donde está el botón—.
