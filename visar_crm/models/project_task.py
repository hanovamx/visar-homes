# -*- coding: utf-8 -*-
from odoo import models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def write(self, vals):
        res = super().write(vals)
        # Cerrado (won): la tarea FSM se cerro en campo. La senal es state=='1_done'
        # (lo escribe el cierre del tecnico en visar_field_app), NO la etapa: un
        # movimiento de etapa en backend no fija state, y '1_canceled' ("cliente no
        # llego") NO cuenta. Idempotente: si el lead ya esta en Cerrado, el search
        # lo excluye, asi que reabrir y re-cerrar no lo re-procesa.
        if vals.get('state') == '1_done':
            Lead = self.env['crm.lead']
            for task in self.filtered(lambda t: t.state == '1_done'):
                order = task.visar_sale_order_id  # de visar_fsm
                if order:
                    Lead._visar_crm_win_order_leads(order)
        return res
