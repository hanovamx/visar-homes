from odoo import models


class VisarZone(models.Model):
    _inherit = 'visar.zone'

    def _visar_poliza_pricelist(self, plan=None):
        """Lista de precios del carrito para esta zona, con o sin póliza.

        Sin plan (o sin lista (zona × plan) configurada) devuelve la lista de la zona,
        de modo que el flujo de compra única sigue cotizando exactamente igual.
        """
        self.ensure_one()
        if not plan:
            return self.pricelist_id
        pricelist = self.env['product.pricelist'].sudo().search([
            ('visar_zone_id', '=', self.id),
            ('visar_plan_id', '=', plan.id),
        ], limit=1)
        return pricelist or self.pricelist_id
