# -*- coding: utf-8 -*-
"""Pedidos históricos capturados a mano: la fecha de la orden es la real (27-oct-2026).

S00002 se capturó el 26-oct con su factura del 10-jun (dato histórico), y su
«Fecha de la orden» decía 26-oct: Odoo la pisa con «ahora» al confirmar. Para que
un pedido del pasado se pueda cargar tal cual:

  * al confirmar, una fecha de cotización puesta ANTES del día en que se capturó
    se respeta (es un pedido histórico, no uno de hoy);
  * al publicar una factura con fecha anterior a la de la orden, la orden se
    lleva a la fecha de la factura. Solo hacia atrás: una factura posterior (lo
    normal) no la toca.

La hora no dice nada en estos pedidos: el formulario enseña solo el día, y al
ajustarla se pone a mediodía en la zona de Visar, que cae el mismo día en UTC
(las comisiones comparan `date_order.date()` en UTC).
"""
from datetime import datetime, time

import pytz

from odoo import models

DEFAULT_TZ = 'America/Monterrey'
MEDIODIA = time(12, 0)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _visar_tz(self):
        return pytz.timezone(self.env.user.tz or DEFAULT_TZ)

    def _visar_dia_local(self, momento):
        return pytz.utc.localize(momento).astimezone(self._visar_tz()).date()

    def _visar_es_historico(self):
        """¿La fecha de la cotización es de un día anterior a su captura?"""
        self.ensure_one()
        return bool(self.date_order and self.create_date
                    and self._visar_dia_local(self.date_order)
                    < self._visar_dia_local(self.create_date))

    def _prepare_confirmation_values(self):
        valores = super()._prepare_confirmation_values()
        if self and all(order._visar_es_historico() for order in self):
            valores.pop('date_order', None)
        return valores

    def _visar_fecha_desde_factura(self, fecha_factura):
        """Lleva la orden al día de la factura si es anterior. Nunca hacia adelante."""
        tz = self._visar_tz()
        nueva = tz.localize(datetime.combine(fecha_factura, MEDIODIA)) \
            .astimezone(pytz.utc).replace(tzinfo=None)
        for order in self.filtered('date_order'):
            if fecha_factura < order._visar_dia_local(order.date_order):
                order.date_order = nueva


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        publicadas = super()._post(soft=soft)
        for move in publicadas.filtered(
                lambda m: m.move_type == 'out_invoice' and m.invoice_date):
            pedidos = move.invoice_line_ids.sale_line_ids.order_id
            pedidos.sudo()._visar_fecha_desde_factura(move.invoice_date)
        return publicadas
