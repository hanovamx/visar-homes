# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    visar_fsm_task_ids = fields.Many2many(
        'project.task',
        string="Servicios externos (Visar)",
        compute='_compute_visar_fsm_task_ids',
        help="Tareas FSM generadas para esta cita (via la orden de venta).")

    @api.depends('sale_order_line_ids.task_id')
    def _compute_visar_fsm_task_ids(self):
        for event in self:
            event.visar_fsm_task_ids = event.sale_order_line_ids.mapped('task_id').filtered(
                lambda t: t.project_id.is_fsm
            )
