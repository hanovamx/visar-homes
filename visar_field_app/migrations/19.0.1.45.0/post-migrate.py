# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.45.0: que un servicio especializado nuevo se pueda
configurar sin ayuda.

Dos huecos que Visar encontró al intentar dar de alta un tratamiento antialacranes
el 25-sep-2026:

- el campo "Se cotiza cuando la hoja marca" caía como última fila suelta de la
  pestaña Ventas, debajo de los bloques de comercio electrónico (lo mueve la vista,
  no esta migración);
- el catálogo de "Servicios identificados" no tenía menú ni acción, así que agregar
  una opción exigía entrar a una hoja de trabajo y crearla desde el desplegable.

`seed_worksheet_templates` es idempotente y ahora crea ese menú.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
