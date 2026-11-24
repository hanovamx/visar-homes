# -*- coding: utf-8 -*-
"""Rutas del código de descuento en la liga de pago (REQ-7600).

La lógica vive en `models/sale_order_cupon.py`; aquí solo se valida el acceso
(el mismo token de la liga) y se regresa al portal con el aviso.
"""
from werkzeug.urls import url_encode

from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.sale.controllers.portal import CustomerPortal

AVISO_SESION = 'visar_cupon_aviso'


class VisarCuponPortal(CustomerPortal):

    @http.route()
    def portal_order_page(self, order_id, report_type=None, access_token=None,
                          message=False, download=False, payment_amount=None,
                          amount_selection=None, **kw):
        if not report_type:
            order_sudo = self._visar_cupon_orden(order_id, access_token)
            if order_sudo:
                con_descuento = order_sudo._visar_cupon_refrescar()
                payment_amount = order_sudo._visar_cupon_tope_liga(
                    payment_amount, con_descuento)
        response = super().portal_order_page(
            order_id, report_type=report_type, access_token=access_token,
            message=message, download=download, payment_amount=payment_amount,
            amount_selection=amount_selection, **kw)
        if AVISO_SESION in request.session:
            aviso = request.session.pop(AVISO_SESION)
            if getattr(response, 'qcontext', None) is not None:
                response.qcontext['visar_cupon_aviso'] = aviso
        return response

    @http.route('/my/orders/<int:order_id>/visar/cupon', type='http',
                auth='public', methods=['POST'], website=True, sitemap=False)
    def visar_cupon_aplicar(self, order_id, access_token=None, code=None,
                            monto_liga=None, **kw):
        order_sudo = self._visar_cupon_orden(order_id, access_token)
        if not order_sudo:
            return request.redirect('/my')
        total_antes = order_sudo.amount_total
        ok, mensaje = order_sudo._visar_cupon_aplicar(code)
        return self._visar_cupon_volver(
            order_sudo, ok, mensaje, monto_liga, total_antes)

    @http.route('/my/orders/<int:order_id>/visar/cupon/quitar', type='http',
                auth='public', methods=['POST'], website=True, sitemap=False)
    def visar_cupon_quitar(self, order_id, access_token=None, line_id=None,
                           monto_liga=None, **kw):
        order_sudo = self._visar_cupon_orden(order_id, access_token)
        if not order_sudo:
            return request.redirect('/my')
        try:
            line_id = int(line_id)
        except (TypeError, ValueError):
            line_id = 0
        total_antes = order_sudo.amount_total
        ok, mensaje = order_sudo._visar_cupon_quitar(line_id)
        return self._visar_cupon_volver(
            order_sudo, ok, mensaje, monto_liga, total_antes)

    @http.route('/my/orders/<int:order_id>/visar/confirmar', type='http',
                auth='public', methods=['POST'], website=True, sitemap=False)
    def visar_cupon_confirmar(self, order_id, access_token=None, **kw):
        order_sudo = self._visar_cupon_orden(order_id, access_token)
        if not order_sudo:
            return request.redirect('/my')
        ok, mensaje = order_sudo._visar_cupon_confirmar_sin_pago()
        return self._visar_cupon_volver(order_sudo, ok, mensaje, None, None)

    # ------------------------------------------------------------------
    def _visar_cupon_orden(self, order_id, access_token):
        try:
            return self._document_check_access(
                'sale.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return None

    def _visar_cupon_volver(self, order_sudo, ok, mensaje, monto_liga, total_antes):
        """De regreso al portal con el aviso y, si venía de la liga, su monto.

        La liga del agente lleva `payment_amount`: con él el portal cobra sin
        pedir firma y abre solo el modal de pago. Se conserva solo si el cliente
        venía de ahí —para no saltarle la firma a una cotización que la pide—,
        recalculado (`_visar_cupon_monto_liga`): total nuevo si era el total,
        el mismo anticipo si era anticipo.
        """
        request.session[AVISO_SESION] = ['ok' if ok else 'error', mensaje]
        query = ''
        if monto_liga and total_antes is not None:
            monto = order_sudo._visar_cupon_monto_liga(monto_liga, total_antes)
            if monto:
                query = '&' + url_encode({'payment_amount': monto})
        return request.redirect(order_sudo.get_portal_url(query_string=query))
