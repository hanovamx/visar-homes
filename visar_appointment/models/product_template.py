# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    visar_appointment_type_id = fields.Many2one(
        'appointment.type', string="Tipo de cita web",
        help="Tipo de cita que el cliente elige en la web y que corresponde a este producto/servicio.")

    @api.model
    def _visar_valuation_price(self, zone=None):
        """Precio de valoración para mostrar en web (respeta pricelist de zona)."""
        tmpl = self._visar_get_valuation_template()
        if not tmpl:
            return False
        variant = tmpl.product_variant_id
        pricelist = zone.pricelist_id if zone else self.env['product.pricelist']
        if not pricelist:
            website = self.env['website'].get_current_website(fallback=False)
            if website:
                pricelist = website._get_and_cache_current_pricelist()
        if pricelist:
            return pricelist._get_product_price(variant, 1.0)
        return variant.lst_price
