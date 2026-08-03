# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Dirección de servicio capturada en el wizard/valoración. Si está definida,
    # el checkout de eCommerce no puede sustituirla por la dirección del usuario
    # logueado (causa del bug "Santos Cantú" vs dirección del flujo).
    visar_service_partner_id = fields.Many2one(
        'res.partner',
        string='Dirección de servicio Visar',
        index=True,
        copy=False,
        help='Contacto de entrega fijado por el flujo Visar (wizard/valoración).',
    )

    def _visar_apply_zone_pricelist(self, zone, plan=None):
        """Asigna al carrito/orden la lista de precios de la zona.

        Con `plan` usa la lista (zona × plan) de la póliza, que deriva sus precios de
        la lista de la zona: el servicio recurrente lleva el descuento del plan y todo
        lo demás (add-ons, extras, roedores) cotiza idéntico a una compra única.
        """
        self.ensure_one()
        if not zone:
            return
        pricelist = zone._visar_poliza_pricelist(plan)
        if pricelist:
            self.pricelist_id = pricelist

    def _visar_set_service_shipping(self, partner):
        """Fija la dirección de servicio Visar y la usa como partner_shipping_id."""
        self.ensure_one()
        if not partner:
            return
        self.with_context(visar_allow_shipping_change=True).write({
            'visar_service_partner_id': partner.id,
            'partner_shipping_id': partner.id,
        })

    def _update_address(self, partner_id, fnames=None):
        """Evita que el checkout reemplace la dirección de servicio Visar."""
        if fnames and self.visar_service_partner_id:
            fnames = [f for f in fnames if f != 'partner_shipping_id']
            if not fnames:
                return
        return super()._update_address(partner_id, fnames)

    def write(self, vals):
        if (
            'partner_shipping_id' in vals
            and not self.env.context.get('visar_allow_shipping_change')
        ):
            locked = self.filtered('visar_service_partner_id')
            unlocked = self - locked
            res = True
            if unlocked:
                res = super(SaleOrder, unlocked).write(vals)
            for order in locked:
                order_vals = dict(vals)
                order_vals['partner_shipping_id'] = order.visar_service_partner_id.id
                super(SaleOrder, order).write(order_vals)
            return res
        return super().write(vals)
