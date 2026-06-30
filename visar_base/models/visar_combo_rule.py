# -*- coding: utf-8 -*-
from odoo import fields, models


class VisarComboRule(models.Model):
    _name = 'visar.combo.rule'
    _description = "Regla de combo / descuento multi-servicio Visar"
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    discount_factor = fields.Float(
        "Factor de precio (0–1)",
        default=0.5,
        help="Precio final = list_price × factor. Ej. 0.5 = 50% de descuento.")
    required_dimension_ids = fields.Many2many(
        'visar.service.dimension', 'visar_combo_required_rel',
        'rule_id', 'dimension_id',
        string="Dimensiones requeridas",
        help="Todas deben estar seleccionadas para aplicar la regla.")
    discount_dimension_ids = fields.Many2many(
        'visar.service.dimension', 'visar_combo_discount_rel',
        'rule_id', 'dimension_id',
        string="Dimensiones con descuento",
        help="Líneas de venta de estas dimensiones reciben el descuento del combo.")

    def _visar_applies_to_items(self, dimension_ids):
        """True si las dimensiones seleccionadas cumplen la regla."""
        self.ensure_one()
        required = set(self.required_dimension_ids.ids)
        return required and required.issubset(set(dimension_ids))

    def _visar_discount_percent(self):
        self.ensure_one()
        factor = max(min(self.discount_factor, 1.0), 0.0)
        return (1.0 - factor) * 100.0
