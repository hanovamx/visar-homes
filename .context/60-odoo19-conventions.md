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

## Parámetros del flujo de citas
- `filter_resource_ids` como **JSON url-encoded**: `quote_plus(json.dumps(ids))`.
- No reimplementar cálculo de slots; post-filtrar sobre el core cuando haga falta (Visar multi-técnico).

## Validación antes de dar por terminado
```bash
python -m py_compile <archivos.py>
python3 -c "import xml.dom.minidom; xml.dom.minidom.parse('<archivo.xml>')"
```
Validación real de vistas:

```bash
PYTHONPATH=/Users/luisgarza27/Documents/HANOVA/odoo_19_visar:/Users/luisgarza27/Documents/HANOVA/VISAR/repo \
  .venv/bin/python setup/odoo -c odoo.visar.conf \
  -u visar_base,visar_fsm,visar_appointment --stop-after-init
```

Reiniciar servidor tras `-u`.

## Código fuente Odoo
`/Users/luisgarza27/Documents/HANOVA/odoo_19_visar/odoo/addons/`

Referencia útil para citas web: `website_sale/models/website.py` (`_get_and_cache_current_pricelist`).
