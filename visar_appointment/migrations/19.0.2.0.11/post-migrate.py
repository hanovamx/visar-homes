# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def _visar_fix_is_free_tiers(env):
    """is_free solo aplica a fumigación exterior 0–50 m² (incluida)."""
    Dimension = env['visar.service.dimension'].sudo()
    Tier = env['visar.service.tier'].sudo()

    exterior = Dimension.search([('code', '=', 'fumigacion_exterior')], limit=1)
    poda = Dimension.search([('code', '=', 'corte_poda')], limit=1)

    if poda and poda.product_tmpl_id:
        Tier.search([
            ('product_tmpl_id', '=', poda.product_tmpl_id.id),
            ('is_free', '=', True),
        ]).write({'is_free': False})

    if exterior and exterior.product_tmpl_id:
        Tier.search([
            ('product_tmpl_id', '=', exterior.product_tmpl_id.id),
            ('m2_min', '=', 0),
            ('m2_max', '<=', 50),
        ]).write({'is_free': True})


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _visar_fix_is_free_tiers(env)
