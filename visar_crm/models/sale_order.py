# -*- coding: utf-8 -*-
from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def write(self, vals):
        res = super().write(vals)
        # Servicio programado: la orden se confirmo/pago (state 'sale'). En el flujo
        # web la confirmacion nativa por transaccion de pago es lo que lleva la
        # orden a 'sale' (no hay confirmacion custom), asi que 'sale' es la senal de
        # "pagado y real". El grupo se deriva de las lineas de servicio (que si
        # llevan producto->dimension->grupo), no del evento -> sin carrera de
        # timing con la creacion del calendar.event. Ver 32-...-implementation.md.
        if vals.get('state') == 'sale':
            Lead = self.env['crm.lead']
            for order in self.filtered(lambda o: o.state == 'sale'):
                if Lead._visar_order_service_groups(order):
                    Lead._visar_crm_advance_order_leads(
                        order, 'visar_crm.crm_stage_wa_programado')
        return res
