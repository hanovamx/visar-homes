# -*- coding: utf-8 -*-
from odoo import fields, models


class VisarCommissionCurve(models.Model):
    _name = 'visar.commission.curve'
    _description = "Escalón de la tabla meta → comisión"
    _order = 'target_rate, id'

    plan_id = fields.Many2one(
        'visar.commission.plan', required=True, index=True, ondelete='cascade')
    currency_id = fields.Many2one(related='plan_id.currency_id', readonly=True)
    # Mismo modelo que el nativo: la tabla son puntos (% de la meta → dinero) y
    # entre dos puntos se interpola en línea recta. Con eso se expresan tanto un
    # pago plano como un acelerador ("del 100% en adelante, el doble").
    target_rate = fields.Float(
        "% de la meta", required=True, default=0.0,
        help="1.0 = el empleado llegó justo a su meta. 1.2 = la superó en 20%.")
    amount = fields.Monetary(
        "Comisión", currency_field='currency_id', required=True, default=0.0,
        help="Lo que se paga al alcanzar ese porcentaje de la meta.")
