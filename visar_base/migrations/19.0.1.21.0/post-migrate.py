# -*- coding: utf-8 -*-
"""Los add-ons antirroedores pasan a la plaga «Roedores»; fuera el producto disparador.

La casilla «Producto control de roedores» se retira: lo que hacía —ofrecer o
añadir un add-on solo cuando el cliente elige roedores— ahora se configura en la
columna «Plagas» de cada add-on. Para que nada cambie de comportamiento a peor,
las líneas que ya existen y son claramente de roedores se atan a esa plaga; sin
esto seguirían ofreciéndose con cualquier plaga hasta que alguien las editara.

Solo toca líneas SIN plagas: correr la migración dos veces, o después de que
Visar haya configurado la columna a mano, no pisa nada.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    roedores = env.ref('visar_base.plaga_roedores', raise_if_not_found=False)
    if roedores:
        lines = env['visar.product.optional.line'].search([
            ('plaga_ids', '=', False),
            ('optional_product_id.name', 'ilike', 'roedor'),
        ])
        lines.write({'plaga_ids': [(4, roedores.id)]})
        _logger.info(
            "visar_base: %s add-on(s) antirroedores atados a la plaga Roedores: %s",
            len(lines), lines.mapped(
                lambda l: '%s -> %s' % (l.product_tmpl_id.name, l.optional_product_id.name)))

    # El parámetro apuntaba al producto disparador; ya no lo lee nadie.
    env['ir.config_parameter'].search(
        [('key', '=', 'visar.roedores_product_tmpl_id')]).unlink()
