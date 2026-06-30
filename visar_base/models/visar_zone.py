# -*- coding: utf-8 -*-
from odoo import fields, models


class VisarZone(models.Model):
    _name = 'visar.zone'
    _description = "Zona geográfica Visar"
    _order = 'sequence, name'

    name = fields.Char("Zona", required=True, translate=True)
    code = fields.Char("Código", help="Identificador corto, p. ej. A, B, C.")
    sequence = fields.Integer("Secuencia", default=10)
    active = fields.Boolean("Activo", default=True)
    pricelist_id = fields.Many2one(
        'product.pricelist', string="Lista de precios",
        help="Lista de precios aplicada a reservas en esta zona (A +15%%, B base, C −10%%).")

    _sql_constraints = [
        ('code_uniq', 'unique(code)', "El código de zona debe ser único."),
    ]
