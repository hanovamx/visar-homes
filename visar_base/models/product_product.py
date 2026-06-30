# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    visar_zone_id = fields.Many2one(
        'visar.zone', string="Zona Visar",
        help="Zona a la que pertenece esta variante. "
             "Se usa para resolver automáticamente la variante correcta en el tabulador según la zona del cliente.")
