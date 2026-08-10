# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.16.0, re-siembra las plantillas para aplicar la
reestructuración de "Fumigación interior o exterior (App v2)":

- **Áreas obligatorias** (Cocina / Baño / Área de basura): campo `x_fija` en el
  modelo de línea + `x_cliente_no_permitio` (dispensa de obligatoriedad).
- **Taxonomía de plagas de 2 niveles**: el m2m principal pasa a ser la CATEGORÍA
  (Rastreros / Voladores / Roedores / Otras plagas / Otra plaga no en las
  opciones) y cada categoría gana su lista de especies en un m2m companion.

⚠️ El catálogo de categorías se siembra con `prune=True`: las etiquetas viejas de
nivel 1 (Termitas / Polilla / Chinches / Otros) **se borran** — ahora son especies
bajo "Otras plagas". Borrarlas solo quita las filas de relación de los m2m que ya
las tenían capturadas; no borra líneas ni hojas. Es lo que se quiere aquí (los
datos actuales son de prueba), pero NO es reversible: si en esa BD hubiera hojas
reales, hay que mapear los valores antes de correr esta migración.

`post_init_hook` solo corre en instalación limpia; esta migración cubre la BD donde
el módulo YA está instalado (QA y producción). Idempotente (re-siembra las tres)."""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
