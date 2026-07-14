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
    measure_type = fields.Selection(
        selection=[
            ('direct', 'Rango directo'),
            ('interior', 'Estimación interior (proxy)'),
            ('exterior', 'Banda unificada de exterior'),
        ],
        string="Tipo de medición",
        default='direct',
        required=True,
        help="Cómo pregunta el wizard el tamaño para esta dimensión:\n"
             "- Rango directo: el cliente elige un tramo del tabulador (legacy).\n"
             "- Estimación interior: sabe m² o los estima por recámaras/baños/niveles/cochera.\n"
             "- Banda unificada de exterior: una sola medición de jardín compartida por "
             "fumigación exterior y corte de pasto.")

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
