# -*- coding: utf-8 -*-
from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _visar_apply_zone_pricelist(self, zone):
        """Asigna la pricelist de la zona al carrito/orden."""
        self.ensure_one()
        if zone and zone.pricelist_id:
            self.pricelist_id = zone.pricelist_id
