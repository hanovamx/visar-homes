# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class VisarServiceTier(models.Model):
    """ Tramo de metros cuadrados de un servicio (producto) que mapea a una
    variante concreta del producto. Es la base del cálculo de variante/precio (D-04). """
    _name = 'visar.service.tier'
    _description = "Tramo de servicio Visar (m² → variante)"
    _order = 'product_tmpl_id, m2_min'

    product_tmpl_id = fields.Many2one(
        'product.template', string="Servicio (producto)",
        required=True, ondelete='cascade', index=True)
    name = fields.Char(
        "Etiqueta del rango",
        translate=True,
        help="Texto mostrado como opción en el wizard (p. ej. '1 – 250 m²').")
    m2_min = fields.Float("m² desde", required=True, default=0)
    m2_max = fields.Float(
        "m² hasta", required=True, default=0,
        help="Límite superior del tramo (inclusivo). Usa un valor alto (p. ej. 99999) "
             "para el último tramo / valoración técnica.")
    product_id = fields.Many2one(
        'product.product', string="Variante asignada",
        domain="[('product_tmpl_id', '=', product_tmpl_id)]",
        help="Variante del producto que se asignará a la cita cuando los m² caigan en este tramo.")
    is_valuation = fields.Boolean(
        "Visita de valoración",
        help="Marca este tramo como 'Visita de valoración técnica' (fuera de rango de servicio directo).")
    is_free = fields.Boolean(
        "Sin cargo",
        help="Si está marcado, el tramo aparece en el pedido con precio tachado al 100% "
             "(p. ej. fumigación exterior 0–50 m² incluida).")
    combo_discount_eligible = fields.Boolean(
        "Aplica descuento combo",
        help="Si una regla de combo aplica, esta línea puede recibir el descuento configurado.")
    sequence = fields.Integer("Secuencia", default=10)

    # Valida que m2_max no sea menor que m2_min en el tramo definido.
    @api.constrains('m2_min', 'm2_max')
    def _check_range(self):
        for tier in self:
            if tier.m2_max < tier.m2_min:
                raise ValidationError(
                    "En el tramo del servicio '%s', 'm² hasta' no puede ser menor que 'm² desde'."
                    % (tier.product_tmpl_id.display_name or ''))

    # Construye el nombre visible del tramo usando la etiqueta o el rango de m².
    @api.depends('product_tmpl_id', 'name', 'm2_min', 'm2_max')
    def _compute_display_name(self):
        for tier in self:
            if tier.name:
                tier.display_name = tier.name
            else:
                tier.display_name = "%s [%g - %g]" % (
                    tier.product_tmpl_id.display_name or '', tier.m2_min, tier.m2_max)
