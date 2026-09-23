# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.42.0: inventario por ruta.

Resiembra las hojas para que el plaguicida deje de ser una lista fija de principios
activos y pase a ser un producto (`x_plaguicida_id`), y reescribe el arch de la
tarjeta de área tratada.

`post_init_hook` solo corre en instalación limpia; esta migración cubre producción.
`seed_worksheet_templates` es idempotente: los campos que ya existen no se tocan
(`_ensure_field`) y las respuestas históricas de `x_plaguicida_nombre` se conservan.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
