# -*- coding: utf-8 -*-
from odoo import _, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _check_amount_and_confirm_order(self):
        """Un pago en línea confirma la orden aunque no tenga lista de precios.

        La regla de "lista obligatoria para confirmar" (`sale.order.
        _confirmation_error_message`) es para quien confirma a mano. Aquí el
        cliente ya pagó: el nativo llama a `action_confirm` sin atrapar errores, y
        un UserError dejaría el cobro hecho y el pedido sin confirmar. En la
        práctica no pasa —el sitio web y el agente asignan la lista de la zona—,
        pero si pasa se confirma y se deja una nota para que oficina lo revise.
        """
        sin_lista = self.sale_order_ids.filtered(
            lambda o: o.state in ('draft', 'sent') and not o.pricelist_id)
        confirmadas = super(
            PaymentTransaction, self.with_context(visar_confirmacion_por_pago=True)
        )._check_amount_and_confirm_order()
        for order in confirmadas.filtered(lambda o: o in sin_lista):
            order.message_post(body=_(
                "Se confirmó por un pago en línea sin lista de precios. Revisa que "
                "el precio cobrado corresponda a la zona del cliente."))
        return confirmadas
