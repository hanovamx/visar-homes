# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.40.0: hojas de trabajo de los tratamientos que se cotizan
a mano (termitas, chinches), sus proyectos FSM y el enganche de los productos.

`post_init_hook` solo corre en instalación limpia; esta migración cubre producción,
donde el módulo ya está instalado. `seed_worksheet_templates` es idempotente.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
