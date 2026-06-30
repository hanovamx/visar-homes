# -*- coding: utf-8 -*-
from odoo import api, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.onchange('product_id')
    def _onchange_product_id_visar_mandatory_addons(self):
        if not self.product_id or self.display_type:
            return
        addon_map = self.product_id.product_tmpl_id._visar_get_mandatory_addon_map(zone=None)
        if not addon_map or not self.order_id:
            return
        for product_id, qty in addon_map.items():
            existing = self.order_id.order_line.filtered(
                lambda line: line.product_id.id == product_id
                and not line.display_type
                and line != self
            )
            if existing:
                existing[0].product_uom_qty = existing[0].product_uom_qty + qty
            else:
                self.order_id.order_line = [(0, 0, {
                    'product_id': product_id,
                    'product_uom_qty': qty,
                })]

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if self.env.context.get('visar_skip_mandatory_addons'):
            return lines

        for order in lines.order_id:
            new_lines = lines.filtered(lambda line: line.order_id == order)
            addon_needed = {}
            for line in new_lines:
                if not line.product_id or line.display_type or line.calendar_booking_ids:
                    continue
                for product_id, qty in line.product_id.product_tmpl_id._visar_get_mandatory_addon_map(
                    zone=None
                ).items():
                    addon_needed[product_id] = addon_needed.get(product_id, 0) + qty

            if not addon_needed:
                continue

            for line in new_lines:
                product_id = line.product_id.id
                if product_id in addon_needed:
                    addon_needed[product_id] -= line.product_uom_qty
                    if addon_needed[product_id] <= 0:
                        del addon_needed[product_id]

            order._visar_apply_mandatory_addons(addon_needed)

        return lines

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('visar_skip_mandatory_addons') or 'product_id' not in vals:
            return res
        for line in self:
            if line.display_type or not line.product_id or line.calendar_booking_ids:
                continue
            addon_map = line.product_id.product_tmpl_id._visar_get_mandatory_addon_map(zone=None)
            if addon_map:
                line.order_id._visar_apply_mandatory_addons(addon_map)
        return res
