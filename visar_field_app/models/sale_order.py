# -*- coding: utf-8 -*-
from odoo import api, fields, models


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
    # Segundo salto de la trazabilidad: el pedido/póliza que originó el SERVICIO.
    # Se congela al crear el pedido de adicionales en vez de leerlo al vuelo por
    # `visar_upsell_task_id.sale_order_id` para que sea indexable y agrupable (el
    # nativo de la tarea es un `compute` sobre `sale_line_id`), y para que el
    # vínculo sobreviva si mañana la tarea se re-apunta a otra línea.
    visar_upsell_source_order_id = fields.Many2one(
        'sale.order', string="Pedido que originó el servicio", readonly=True,
        copy=False, index='btree_not_null',
        help="Pedido de venta (o póliza) del que salió el servicio en el que se "
             "vendieron estos adicionales. El upsell NO se factura aquí: es solo "
             "el hilo para llegar de la venta original a lo vendido en sitio.")
    # Inverso: desde el pedido ORIGINAL, todo lo que sus visitas generaron en campo.
    visar_upsell_order_ids = fields.One2many(
        'sale.order', 'visar_upsell_source_order_id',
        string="Adicionales vendidos en sitio", readonly=True)
    visar_upsell_order_count = fields.Integer(
        compute='_compute_visar_upsell_order_count',
        string="Pedidos de adicionales", export_string_translation=False)
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

    @api.depends('visar_upsell_order_ids')
    def _compute_visar_upsell_order_count(self):
        counts = dict(self.env['sale.order']._read_group(
            [('visar_upsell_source_order_id', 'in', self.ids)],
            ['visar_upsell_source_order_id'], ['__count'],
        ))
        for order in self:
            order.visar_upsell_order_count = counts.get(order, 0)

    def action_visar_view_upsell_orders(self):
        """Botón inteligente: los pedidos de adicionales nacidos de ESTE pedido.

        Con uno solo abre la ficha directo (el caso normal: una cita, un upsell);
        con varios abre la lista, porque una póliza acumula uno por visita.
        """
        self.ensure_one()
        orders = self.visar_upsell_order_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': "Adicionales vendidos en sitio",
            'res_model': 'sale.order',
            'context': {'create': False},
        }
        if len(orders) == 1:
            action.update(view_mode='form', res_id=orders.id)
        else:
            action.update(
                view_mode='list,form',
                domain=[('visar_upsell_source_order_id', '=', self.id)])
        return action

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
