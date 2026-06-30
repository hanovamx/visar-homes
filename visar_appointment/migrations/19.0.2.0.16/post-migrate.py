# -*- coding: utf-8 -*-
"""
Migración 19.0.2.0.16: Variantes por zona
==========================================

Enlaza cada variante de producto con su zona (campo visar_zone_id en product.product).
El tabulador conserva su product_id (variante de Zona B como referencia); el código
usa ese product_id para encontrar la variante análoga en otras zonas por intersección
de atributos de variante.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Mapeo: variante → zona
    VARIANT_ZONE_MAP = {
        # Fumigación (producto 30)
        488: 1, 489: 1, 490: 1, 492: 1, 496: 1,   # Zona A
        504: 2, 505: 2, 506: 2, 508: 2, 512: 2,   # Zona B
        520: 3, 521: 3, 522: 3, 524: 3, 528: 3,   # Zona C
        # Mantenimiento (producto 31)
        539: 1, 540: 1, 541: 1, 542: 1,            # Zona A
        544: 2, 545: 2, 546: 2, 547: 2,            # Zona B
        549: 3, 550: 3, 551: 3, 552: 3,            # Zona C
    }

    Product = env['product.product'].sudo()
    for variant_id, zone_id in VARIANT_ZONE_MAP.items():
        variant = Product.browse(variant_id).exists()
        if variant:
            variant.write({'visar_zone_id': zone_id})
