# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def _visar_set_measure_type(env):
    """Asigna measure_type a las dimensiones existentes según su código.

    - interior en el código          → 'interior'
    - exterior / corte / poda / pasto → 'exterior'
    - resto                          → se queda en 'direct' (default)

    Idempotente: solo escribe dimensiones que siguen en 'direct'.
    """
    Dimension = env['visar.service.dimension'].sudo()
    for dimension in Dimension.search([('measure_type', '=', 'direct')]):
        code = (dimension.code or '').lower()
        if 'interior' in code:
            dimension.measure_type = 'interior'
        elif any(token in code for token in ('exterior', 'corte', 'poda', 'pasto', 'jardin')):
            dimension.measure_type = 'exterior'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _visar_set_measure_type(env)
