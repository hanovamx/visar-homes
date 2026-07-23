# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.13.0, siembra el catálogo de venta en campo (upsell):
enciende `visar_upsell_ok` en los productos de la categoría "Upsell".

`post_init_hook` solo corre en instalación limpia; esta migración cubre la BD donde
el módulo YA está instalado (QA y producción), que es justo donde vive la categoría
curada por negocio. Idempotente y solo enciende — ver `seed_upsell_catalog`."""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_upsell_catalog


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_upsell_catalog(env)
