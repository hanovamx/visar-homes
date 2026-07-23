# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # --- Pedido de adicionales vendidos en campo ---
    # Vive SEPARADO del pedido/póliza que originó el servicio (ver la nota larga en
    # `project.task._visar_upsell_order`). Estos campos son la trazabilidad: de qué
    # servicio salió y qué técnico lo vendió (comisiones).
    visar_upsell_task_id = fields.Many2one(
        'project.task', string="Servicio de origen (upsell)", readonly=True,
        copy=False, index='btree_not_null',
        help="Servicio en el que el técnico vendió estos productos adicionales.")
    visar_upsell_employee_id = fields.Many2one(
        'hr.employee', string="Vendido por (técnico)", readonly=True, copy=False,
        help="Técnico que levantó la venta desde la app de campo. "
             "Base para comisiones de upsell.")
    visar_upsell_cash_at = fields.Datetime(
        string="Cobrado en efectivo", readonly=True, copy=False,
        help="Momento en que el técnico declaró haber recibido el pago en sitio. "
             "No sustituye la conciliación contable: administración registra el "
             "pago contra la factura.")
    visar_upsell_cash_by_id = fields.Many2one(
        'hr.employee', string="Efectivo recibido por", readonly=True, copy=False)

    def _visar_upsell_is_paid(self):
        """¿El cobro de este pedido de adicionales ya está resuelto?

        Dos caminos, porque en campo pasan los dos:
          * en línea — hay una transacción de pago liquidada/autorizada;
          * en efectivo — el técnico lo declaró (sello propio).

        NO se mira `payment_state` de la factura para el caso de efectivo: en
        Odoo 19 un pago registrado a mano queda `in_process` sin asiento hasta que
        se liquida, así que la factura seguiría en 'not_paid' durante horas.
        """
        self.ensure_one()
        if self.visar_upsell_cash_at:
            return True
        done = self.transaction_ids.filtered(
            lambda t: t.state in ('done', 'authorized'))
        if done:
            return True
        return any(
            move.payment_state in ('paid', 'in_payment')
            for move in self.invoice_ids
            if move.move_type == 'out_invoice' and move.state == 'posted'
        )
