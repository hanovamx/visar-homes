# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


_LEGACY_GROUP = {
    'fumigacion': ('Fumigación', 10),
    'corte': ('Corte y poda', 20),
}

_LEGACY_DIMENSION = {
    ('fumigacion', 'interior'): ('Interior', 10),
    ('fumigacion', 'exterior'): ('Exterior', 20),
    ('corte', 'poda'): ('Poda / jardín', 10),
}


def _visar_migrate_legacy_catalog(env):
    """Crea grupos/dimensiones desde campos legacy del producto si aún no existen."""
    Group = env['visar.service.group'].sudo()
    Dimension = env['visar.service.dimension'].sudo()
    Product = env['product.template'].sudo()
    Tier = env['visar.service.tier'].sudo()
    ComboRule = env['visar.combo.rule'].sudo()

    if not Group.search_count([]):
        group_by_code = {}
        for code, (name, seq) in _LEGACY_GROUP.items():
            group_by_code[code] = Group.create({
                'name': name,
                'code': code,
                'sequence': seq,
                'show_in_wizard': True,
            })

        for (g_code, d_code), (d_name, d_seq) in _LEGACY_DIMENSION.items():
            group = group_by_code.get(g_code)
            if not group:
                continue
            Dimension.create({
                'group_id': group.id,
                'name': d_name,
                'code': '%s_%s' % (g_code, d_code),
                'sequence': d_seq,
            })

    dimension_by_legacy = {}
    for dimension in Dimension.search([]):
        parts = (dimension.code or '').split('_', 1)
        if len(parts) == 2:
            dimension_by_legacy[(parts[0], parts[1])] = dimension

    for product in Product.search([('visar_is_service', '=', True)]):
        legacy_key = (product.visar_service_group or '', product.visar_dimension_kind or '')
        dimension = dimension_by_legacy.get(legacy_key)
        if dimension and not dimension.product_tmpl_id:
            dimension.product_tmpl_id = product.id
        if dimension and not product.visar_dimension_id:
            product.visar_dimension_id = dimension.id

    for product in Product.search([('visar_is_valuation', '=', False)]):
        if 'valoración' in (product.name or '').lower() or 'valoracion' in (product.name or '').lower():
            product.visar_is_valuation = True

    exterior = dimension_by_legacy.get(('fumigacion', 'exterior'))
    if exterior and exterior.product_tmpl_id:
        Tier.search([
            ('product_tmpl_id', '=', exterior.product_tmpl_id.id),
            ('m2_min', '=', 0),
            ('m2_max', '<=', 50),
        ]).write({'is_free': True})

    if not ComboRule.search_count([]):
        interior = dimension_by_legacy.get(('fumigacion', 'interior'))
        exterior = dimension_by_legacy.get(('fumigacion', 'exterior'))
        poda = dimension_by_legacy.get(('corte', 'poda'))
        if interior and exterior and poda:
            factor = float(env['ir.config_parameter'].sudo().get_param(
                'visar.combo_corte_factor', '0.5'))
            ComboRule.create({
                'name': 'Combo fumigación + corte',
                'discount_factor': factor,
                'required_dimension_ids': [(6, 0, [interior.id, exterior.id, poda.id])],
                'discount_dimension_ids': [(6, 0, [poda.id])],
            })
            if poda.product_tmpl_id:
                Tier.search([
                    ('product_tmpl_id', '=', poda.product_tmpl_id.id),
                ]).write({'combo_discount_eligible': True})


def migrate(cr, version):
    # Ejecuta la migración del catálogo legacy creando grupos, dimensiones y reglas de combo.
    env = api.Environment(cr, SUPERUSER_ID, {})
    _visar_migrate_legacy_catalog(env)
