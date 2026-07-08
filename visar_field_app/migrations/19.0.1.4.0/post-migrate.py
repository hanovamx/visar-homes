# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.4.0, siembra la tercera plantilla de hoja de trabajo:
"Visita de valoración técnica (App v2)". Idempotente (re-siembra las tres).

`post_init_hook` solo corre en instalación limpia; esta migración cubre el caso de
una BD donde el módulo YA está instalado (producción)."""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
