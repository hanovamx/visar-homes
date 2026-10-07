# -*- coding: utf-8 -*-
from odoo.http import request

from odoo.addons.website_sale.controllers.main import WebsiteSale


class VisarWebsiteSale(WebsiteSale):
    """El paso de dirección de "Finalizar compra" para una cita de Visar.

    Lo que ese paso pide es la dirección de FACTURACIÓN: la de servicio ya se
    capturó en el wizard y está fija en el pedido. El formulario nativo no lo
    dice ("Editar dirección") y llegaba en blanco, así que el cliente creía que
    le pedían otra vez dónde es el servicio (pedido de Visar, 7-oct-2026).

    Aquí: se le enseña UNA vez, ya lleno con la dirección de servicio, y
    rotulado. Si es la misma, sigue; si factura a otra, la cambia ahí.
    """

    def _check_addresses(self, order_sudo):
        redirection = super()._check_addresses(order_sudo)
        if redirection or not order_sudo.visar_billing_assumed:
            return redirection
        # Se apaga AL mandarlo al formulario, no al enviarlo: es "una vez", y si
        # el cliente se sale y vuelve no se le repite. Desde el pago la puede
        # seguir editando.
        order_sudo.sudo().visar_billing_assumed = False
        factura = order_sudo.partner_invoice_id
        if not factura._can_be_edited_by_current_customer(order_sudo=order_sudo):
            return redirection
        return request.redirect(
            f'/shop/address?partner_id={factura.id}&address_type=billing')

    def _prepare_address_form_values(self, *args, order_sudo=False, **kwargs):
        values = super()._prepare_address_form_values(
            *args, order_sudo=order_sudo, **kwargs)
        servicio = order_sudo and order_sudo.visar_service_partner_id
        if servicio and values.get('address_type') == 'billing':
            values['visar_service_address'] = ', '.join(filter(None, [
                servicio.street, servicio.street2, servicio.city, servicio.zip]))
        return values
