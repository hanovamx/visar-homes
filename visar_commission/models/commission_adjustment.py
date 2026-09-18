# -*- coding: utf-8 -*-
from odoo import fields, models


class VisarCommissionAdjustment(models.Model):
    _name = 'visar.commission.adjustment'
    _description = "Ajuste manual de comisión"
    _order = 'date desc, id desc'

    # Equivalente de `sale.commission.achievement` del nativo: lo que no sale de
    # una venta (un bono, un descuento acordado, una corrección del mes pasado).
    # Suma al LOGRO, no a la comisión final: en un plan por metas un ajuste
    # también debe mover el porcentaje alcanzado, como en el nativo.
    plan_employee_id = fields.Many2one(
        'visar.commission.plan.employee', string="Empleado del plan",
        required=True, index=True, ondelete='cascade')
    plan_id = fields.Many2one(
        related='plan_employee_id.plan_id', store=True, index=True, readonly=True)
    employee_id = fields.Many2one(
        related='plan_employee_id.employee_id', store=True, readonly=True)
    currency_id = fields.Many2one(related='plan_id.currency_id', readonly=True)
    date = fields.Date("Fecha", required=True, default=fields.Date.context_today,
                       help="Determina en qué periodo cae el ajuste.")
    amount = fields.Monetary(
        "Importe", currency_field='currency_id', required=True,
        help="Positivo suma, negativo descuenta.")
    note = fields.Char("Motivo", required=True,
                       help="Por qué se ajustó. Se ve en el detalle del periodo.")

    def _compute_display_name(self):
        for ajuste in self:
            ajuste.display_name = "%s: %s" % (
                ajuste.employee_id.name or '', ajuste.note or '')
