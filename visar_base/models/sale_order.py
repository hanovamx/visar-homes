# -*- coding: utf-8 -*-
from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _visar_apply_mandatory_addons(self, addon_map):
        """Agrega o suma líneas de add-ons obligatorios (SO manual / backend)."""
        self.ensure_one()
        if not addon_map:
            return

        SaleOrderLine = self.env['sale.order.line'].with_context(
            visar_skip_mandatory_addons=True,
        )
        for product_id, qty in addon_map.items():
            if qty <= 0:
                continue
            existing = self.order_line.filtered(
                lambda line: line.product_id.id == product_id and not line.display_type
            )
            if existing:
                existing[0].write({'product_uom_qty': existing[0].product_uom_qty + qty})
            else:
                SaleOrderLine.create({
                    'order_id': self.id,
                    'product_id': product_id,
                    'product_uom_qty': qty,
                })
