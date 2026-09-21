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

    def _visar_quote_item(self, m2):
        """(item, error): la pieza de cotización de esta dimensión para `m2` metros.

        `item` es el dict que consume el motor de precios del agendado
        (`appointment.type._visar_build_sale_lines`). Vive aquí, y no en cada canal,
        porque lo necesitan dos que no se conocen entre sí —el agente de WhatsApp y la
        venta en campo del técnico— y el tramo equivocado cobra de menos en silencio
        (ver el guardia de `_visar_combined_variant_for_tiers`). Una sola traducción
        de m² a tramo es la garantía de que la misma casa cuesta lo mismo por
        cualquier canal.

        `error` ∈ {'sin_m2', 'sin_producto', 'sin_tramo'}; cada canal lo convierte
        en el mensaje que le toque (al modelo, al técnico).
        """
        self.ensure_one()
        if not m2 or m2 <= 0:
            return None, 'sin_m2'
        template = self.env['product.template']._visar_get_service_template_for_dimension(self)
        if not template:
            return None, 'sin_producto'
        tier = template._visar_tier_for_dimension_m2(self, m2)
        if not tier:
            return None, 'sin_tramo'
        return {
            'dimension_id': self.id,
            'tier_id': tier.id,
            'tier_name': tier.name or '',
            'variant_id': None,   # lo resuelve por zona el motor de precios
            'product_tmpl_id': template.id,
            'is_valuation': tier.is_valuation,
            'is_free': tier.is_free,
        }, None
