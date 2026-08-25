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

    def _visar_missing_dimensions(self, dimension_ids):
        """Lo que le falta a la canasta para que esta regla aplique.

        Recordset vacio = la regla ya aplica, o no exige nada.

        Devuelve las dimensiones que faltan y no un booleano a proposito: la
        pregunta util delante de un cliente no es "aplica?" sino "que tiene que
        anadir para que aplique?". Un combo que solo se puede comprobar DESPUES
        de armar la canasta no vende nada; el cuestionario web llega a el porque
        su estructura lo empuja (el paso de cobertura ofrece "ambos"), pero el
        chat arma la canasta con lo que el cliente dijo y necesita que alguien le
        diga que falta. Ese alguien es esta regla, no el prompt: aqui es donde el
        consultor la edita.
        """
        self.ensure_one()
        required = self.required_dimension_ids
        if not required:
            return self.env['visar.service.dimension'].browse()
        elegidas = set(dimension_ids or [])
        return required.filtered(lambda dim: dim.id not in elegidas)

    def _visar_discount_percent(self):
        self.ensure_one()
        factor = max(min(self.discount_factor, 1.0), 0.0)
        return (1.0 - factor) * 100.0
