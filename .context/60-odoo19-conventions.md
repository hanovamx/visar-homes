# Convenciones de Odoo 19 a respetar

Cosas que cambiaron respecto a versiones anteriores y errores fáciles de cometer.
**Incluye gotchas descubiertos en E2E web Visar (jun-2026).**

## Vistas
- **`<list>` en lugar de `<tree>`.** Odoo 19 usa `<list>` y `view_mode="list,form"`.
- Atributos de visibilidad: `invisible="..."`, `required="..."`, `readonly="..."` (no `attrs`).
- En listas embebidas usar `parent.<campo>` en dominios.

## Modelos
- **`name_get` está deprecado.** Usar `_compute_display_name`.
- Importar excepciones desde `odoo.exceptions`.

## Controladores web
- Heredar controlador del core e invocar `super()`.
- Rutas: `@http.route(..., type='http', auth='public', website=True, methods=['POST'])`.
- CSRF en formularios POST: `request.csrf_token()`.
- Estado multi-paso: `request.session[...]`.

### POST con campos repetidos (checkboxes, multi-select)

En Odoo 19 el parámetro `**post` / `**kwargs` del controlador es un **`dict` plano**, no Werkzeug `MultiDict`.

```python
# ❌ AttributeError: 'dict' object has no attribute 'getlist'
group_ids = post.getlist('group_ids')

# ✅ Correcto
group_ids = request.httprequest.form.getlist('group_ids')
```

En Visar: helper `_visar_form_id_list(field)` en `controllers/appointment.py`.

## Website / ecommerce (Odoo 19)

### Pricelist del sitio

```python
# ❌ AttributeError: 'website' object has no attribute 'pricelist_id'
pricelist = website.pricelist_id

# ✅ Correcto (website_sale)
pricelist = website._get_and_cache_current_pricelist()
```

El modelo `website` expone `pricelist_ids` (conjunto), no un único `pricelist_id`.

## QWeb frontend
- Envolver en `<t t-call="website.layout">` + `<div id="wrap">`.
- **`getattr`, `hasattr` y builtins arbitrarios NO están disponibles** en expresiones `t-value` / `t-if`.
  QWeb compila nombres como `values['nombre']` → `KeyError` si no existen en contexto.
- Pasar datos desde Python al template; no leer atributos dinámicos de `request` en QWeb.

```xml
<!-- ❌ Falla si visar_quote es False y request.visar_quote no existe -->
<t t-set="quote" t-value="visar_quote or request.visar_quote"/>

<!-- ❌ KeyError: 'getattr' -->
<t t-set="quote" t-value="getattr(request, 'visar_quote', False)"/>

<!-- ✅ Pasar visar_quote siempre desde el controlador -->
<t t-set="quote" t-value="visar_quote or False"/>
```

Para rutas que no pasan contexto (p. ej. `appointment_type_id_form`), inyectar valores en
`request.render()` desde Python en lugar de hacks en `request`.

## Suscripciones / pólizas (`sale_subscription`)

Gotchas caros descubiertos al implementar el cobro adelantado (ago-2026). Ver
[`35-polizas.md`](./35-polizas.md).

### El sitio web cobra `amount_total`, punto

`website_sale/controllers/payment.py` rechaza cualquier importe distinto de
`order.amount_total` (*"The cart has been updated. Please refresh the page."*).
Cualquier regla de negocio que cobre más de lo que suman las líneas **tiene que ser
una línea**: no existe forma de cobrar de más "por detrás" en el checkout web.

### `pricelist.item_ids` NO trae las reglas con plan

`sale_subscription` filtra `item_ids` a las reglas **sin** plan. Una lista que solo
tenga reglas de plan aparece con `item_ids` **vacío**.

```python
# ❌ Falso negativo en listas de suscripción
if pricelist.item_ids and all(i.plan_id for i in pricelist.item_ids): ...

# ✅ Consultar el modelo directamente
self.env['product.pricelist.item'].search([('plan_id', '!=', False), ...])
```

### `_recompute_prices()` también pone `discount` a 0

No solo recalcula `price_unit` desde la lista: **resetea el descuento**. Y se dispara
solo desde `res.partner.write()` cuando cambian `country_id` / `vat` / `zip` y se
mueve la posición fiscal — es decir, **al escribir la dirección en el checkout**.

Cualquier línea con precio o descuento puesto a mano se pierde ahí. Para protegerla,
filtrarla en `_get_update_prices_lines()`. (El descuento de combo de las líneas de
servicio sigue expuesto a esto — ver `90-improvements-later.md`.)

### `last_invoice_date` es calculado y NO almacenado

Se deriva de `next_invoice_date` — que la propia facturación mueve. Usarlo para
decidir "¿es la primera factura?" es una trampa: cualquier cosa que empuje esa fecha
vuelve a encender el flag. Produjo dobles facturas en producción (S00087/S00088).
**No construir idempotencia sobre campos calculados no almacenados.**

### `_get_max_invoiced_date()` ignora los apuntes sin fecha diferida

Es lo que `sale_subscription` usa para avanzar `next_invoice_date` tras postear. Si
una línea debe extender el periodo cubierto, hay que darle
`deferred_start_date` / `deferred_end_date` en `_prepare_invoice_line`; sin fechas,
el apunte simplemente no cuenta.

> ⚠️ Si ya se hace eso, **no** override-ear además `_update_next_invoice_date`:
> se adelantaría el doble.

### `_cart_add` convierte el carrito en suscripción sin avisar

`website_sale_subscription._cart_add` entra en la rama de suscripción siempre que
`product.recurring_invoice and not kwargs.get('allow_one_time_sale')`, y ahí puede
fijar `order.plan_id`. Un producto que se vende en ambos modos
(`recurring_invoice` + `allow_one_time_sale`) es un campo minado:

- pasar **`allow_one_time_sale=True`** explícito en el flujo de compra única;
- pasar **`plan_id=<id>`** en el flujo de póliza;
- y limpiar `order.plan_id` al reconstruir el carrito: `_verify_cart_after_update`
  solo lo limpia cuando la actualización deja **cero** líneas recurrentes, así que
  un plan viejo se queda pegado y la orden se confirma como suscripción.

### Un pago completo puede clasificarse como parcial

`_get_partial_payment_subscription_transaction()` compara el importe autorizado
contra `_next_billing_details()`, que se arma desde `_get_invoiceable_lines()`. Si
algo que sí se cobra queda fuera de esas líneas, el pago se marca **parcial**: no se
crea factura, no corre `_invoice_paid_hook`, no hay visitas y el dinero queda sin
aplicar — **sin ningún error visible**. Vale la pena un test que fije
`_next_billing_details()['tax_totals']['total_amount_currency'] == amount_total`.

## Parámetros del flujo de citas
- `filter_resource_ids` como **JSON url-encoded**: `quote_plus(json.dumps(ids))`.
- No reimplementar cálculo de slots; post-filtrar sobre el core cuando haga falta (Visar multi-técnico).

## Versión del manifest: NO subirla salvo que sea necesario

**Preferencia del cliente (jul-2026):** **no** bumpear `version` en `__manifest__.py` por
costumbre. Subirla solo cuando el cambio **exige `-u`** para aplicarse; si basta con
**reiniciar**, se deja la versión como está.

| Tipo de cambio | ¿Aplica cómo? | ¿Bumpear versión? |
|---|---|---|
| Campos nuevos / modificados, XML (vistas, datos, reportes, `security`), assets nuevos | **`-u <módulo>`** | **Sí** |
| Solo Python (métodos de modelo, controladores, rutas `@http.route` nuevas, hooks) | **Reiniciar** el servidor | **No** |

Razón del split: los `.xml`/campos solo se re-leen en `-u`; el código Python se recarga
al arrancar el proceso. Una ruta de controlador nueva es Python → **reinicio basta**.

> ⚠️ El bump de versión es también lo que **dispara las migraciones** (`migrations/<ver>/`).
> Subir la versión sin querer puede correr un post-migrate antes de tiempo.

## Validación antes de dar por terminado
```bash
python -m py_compile <archivos.py>
python3 -c "import xml.dom.minidom; xml.dom.minidom.parse('<archivo.xml>')"
```

### En el servidor de producción

`/opt/custom` **es** el addons path del Odoo que corre (`/etc/odoo/odoo.conf`,
BD `visar-db`). Editar ahí es editar producción: el proceso vivo sigue con el Python
que cargó en memoria, pero **cualquier reinicio levanta lo que haya en disco**.

Nunca correr tests contra `visar-db`. Copia de trabajo:

```bash
sudo -u odoo createdb visar-test
sudo -u odoo bash -c "pg_dump visar-db | psql -q -d visar-test"   # ~175 MB, ~1 min

# odoo.conf apunta a visar-db; se sobreescribe con -d y puertos libres
sudo -u odoo /opt/odoo/venv/bin/odoo -c /tmp/odoo-test.conf -d visar-test \
  -u visar_subscription --test-enable --test-tags=/visar_subscription \
  --stop-after-init --http-port=8199 --gevent-port=8299 --log-level=test
```

`--log-level=test` es lo que hace visible la línea `N failed, N error(s) of N tests`;
con el nivel por defecto los tests pasan en silencio y **la ausencia de errores no
prueba que corrieran**.

Antes de un `-u` sobre producción: `pg_dump -Fc visar-db -f <backup>.dump`.

### Odoo shell para verificar datos

```bash
sudo -u odoo /opt/odoo/venv/bin/odoo shell -c /etc/odoo/odoo.conf -d visar-db \
  --no-http --log-level=error --http-port=8199 --gevent-port=8299 < script.py
```

## Código fuente Odoo

- **Servidor:** `/opt/odoo/odoo/addons/`
- Local (Mac de desarrollo): `/Users/luisgarza27/Documents/HANOVA/odoo_19_visar/odoo/addons/`

Referencia útil para citas web: `website_sale/models/website.py` (`_get_and_cache_current_pricelist`).
