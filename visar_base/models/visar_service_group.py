# -*- coding: utf-8 -*-
from odoo import fields, models


class VisarServiceGroup(models.Model):
    _name = 'visar.service.group'
    _description = "Grupo de servicio Visar (wizard)"
    _order = 'sequence, name'

    name = fields.Char("Nombre", required=True, translate=True)
    code = fields.Char("Código", required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    show_in_wizard = fields.Boolean(
        "Mostrar en wizard web",
        default=True,
        help="Si está activo, aparece como opción en el paso 1 del wizard.")
    wizard_label = fields.Char(
        "Etiqueta en wizard",
        translate=True,
        help="Texto del checkbox; por defecto se usa el nombre del grupo.")
    wizard_help = fields.Char("Ayuda", translate=True)
    dimension_ids = fields.One2many(
        'visar.service.dimension', 'group_id', string="Dimensiones / sub-servicios")

    _sql_constraints = [
        ('code_uniq', 'unique(code)', "El código de grupo debe ser único."),
    ]

    # Devuelve la etiqueta personalizada del grupo o su nombre si no hay etiqueta configurada.
    def _visar_wizard_label(self):
        self.ensure_one()
        return self.wizard_label or self.name
