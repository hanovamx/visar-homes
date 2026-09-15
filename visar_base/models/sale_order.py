# -*- coding: utf-8 -*-
from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # --- Lista de precios obligatoria para confirmar (REQ-004) ---
    # Visar cobra por zona (A/B/C). Odoo llenaba la lista de toda cotización con
    # la primera lista activa ("VISAR Zona A", sequence 11) cuando el cliente no
    # tenía una propia, y así se podía confirmar con el precio de otra zona. Ese
    # default ya no existe fuera del sitio web (`visar_appointment/models/
    # res_partner.py`); esto impide confirmar sin elegirla.

    def _visar_requiere_lista_de_precios(self):
        """¿Esta orden necesita lista de precios para confirmarse?

        No cuando la confirma un PAGO en línea: ahí el dinero ya entró, y un error
        a media transacción deja al cliente pagado y sin pedido confirmado (el
        problema de REQ-002). Ver `payment.transaction`. Los módulos que tienen
        sus propias excepciones (el upsell de campo) extienden este método.
        """
        self.ensure_one()
        return not self.env.context.get('visar_confirmacion_por_pago')

    def _confirmation_error_message(self):
        error = super()._confirmation_error_message()
        if error or self.pricelist_id or not self._visar_requiere_lista_de_precios():
            return error
        return _("Selecciona la lista de precios de la zona del cliente antes de "
                 "confirmar la cotización.")

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
