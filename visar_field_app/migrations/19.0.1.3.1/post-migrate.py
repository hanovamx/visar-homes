# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.3.1, re-siembra las plantillas de hoja de trabajo para
aplicar el reetiquetado de los campos-foto a plural ("Fotos ...", ahora galerías
multi-foto). Idempotente.

`post_init_hook` solo corre en instalación limpia; esta migración cubre el caso de
una BD donde el módulo YA está instalado (producción)."""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
