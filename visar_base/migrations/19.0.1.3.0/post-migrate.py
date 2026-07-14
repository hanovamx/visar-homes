# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID

# Límites de rango del tabulador oficial (guión). Se usan para clasificar los
# tramos de un producto que sirve a la vez a interior y exterior (conflacionado),
# de modo que la resolución m² → tramo no mezcle ambos tabuladores.
_EXTERIOR = {(0, 50), (51, 100), (101, 500), (501, 99999)}
_INTERIOR = {(1, 250), (251, 500), (501, 1000), (1001, 99999)}


def _scope_for_tier(tier):
    key = (round(tier.m2_min), round(tier.m2_max))
    if key in _EXTERIOR:
        return 'exterior'
    if key in _INTERIOR:
        return 'interior'
    return False


def _visar_scope_conflated_tiers(env):
    """Asigna measure_scope a los tramos de productos que sirven a interior Y exterior.

    Solo toca productos realmente conflacionados (respaldan una dimensión con
    measure_type='interior' y otra con 'exterior') y tramos aún en 'all'.
    """
    Dimension = env['visar.service.dimension'].sudo()
    interior_tmpls = Dimension.search([
        ('measure_type', '=', 'interior'), ('product_tmpl_id', '!=', False),
    ]).product_tmpl_id
    exterior_tmpls = Dimension.search([
        ('measure_type', '=', 'exterior'), ('product_tmpl_id', '!=', False),
    ]).product_tmpl_id
    conflated = interior_tmpls & exterior_tmpls
    for tmpl in conflated:
        for tier in tmpl.visar_tier_ids.filtered(lambda t: t.measure_scope == 'all'):
            scope = _scope_for_tier(tier)
            if scope:
                tier.measure_scope = scope


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _visar_scope_conflated_tiers(env)
