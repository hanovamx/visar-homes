# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_combo_corte_factor = fields.Float(
        string="Factor combo (legacy)",
        config_parameter='visar.combo_corte_factor',
        default=0.5,
        help="Respaldo si no hay reglas de combo activas. Precio = list_price × factor.")
    visar_valuation_product_tmpl_id = fields.Many2one(
        'product.template',
        string="Producto valoración técnica",
        domain="[('visar_is_valuation', '=', True)]",
        config_parameter='visar.valuation_product_tmpl_id',
        help="Producto usado en el flujo de valoración técnica.")
