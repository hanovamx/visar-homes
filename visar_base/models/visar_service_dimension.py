# -*- coding: utf-8 -*-
from odoo import fields, models


class VisarServiceDimension(models.Model):
    _name = 'visar.service.dimension'
    _description = "Dimensión / sub-servicio Visar"
    _order = 'group_id, sequence, name'

    group_id = fields.Many2one(
        'visar.service.group', string="Grupo", required=True, ondelete='cascade', index=True)
    name = fields.Char("Nombre", required=True, translate=True)
    code = fields.Char("Código", required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    wizard_label = fields.Char(
        "Etiqueta en wizard",
        translate=True,
        help="Texto mostrado en sub-pasos y en dimensiones; por defecto el nombre.")
    product_tmpl_id = fields.Many2one(
        'product.template', string="Producto servicio",
        domain="[('visar_is_service', '=', True)]",
        help="Producto cuyo tabulador (tramos m²) se muestra en el wizard.")

    _sql_constraints = [
        ('code_uniq', 'unique(code)', "El código de dimensión debe ser único."),
    ]

    # Devuelve la etiqueta personalizada de la dimensión o su nombre si no hay etiqueta configurada.
    def _visar_wizard_label(self):
        self.ensure_one()
        return self.wizard_label or self.name

    def _visar_tier_field_name(self):
        """Nombre del campo POST para el tramo elegido."""
        self.ensure_one()
        return 'tier_%s' % self.id
