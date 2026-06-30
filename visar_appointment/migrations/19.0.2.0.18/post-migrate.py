# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Quita preguntas redundantes (Zona/m²) del formulario nativo.
    # Busca tanto por external ID como por nombre para cubrir duplicados históricos.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['appointment.type']._visar_unlink_questions_from_entry_types()
