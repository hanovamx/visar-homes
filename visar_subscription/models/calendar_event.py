# -*- coding: utf-8 -*-
from odoo import models


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    def _visar_appointment_partners(self):
        """Suma el dueño de la PÓLIZA a los dueños de la cita.

        La cita de una visita pre-agendada no la originó ninguna línea de orden
        —la creó el cron de pre-agenda—, así que la búsqueda de `visar_fsm`
        devuelve vacío para ella. Sin esto, el cliente al que acabamos de invitar
        a mover su visita recibiría «no encontré esa cita a tu nombre».

        La extensión vive aquí y no en `visar_fsm` porque el campo que lo sabe
        (`visar_subscription_order_id`) es de este módulo, y `visar_fsm` es su
        dependencia: no puede mirar hacia abajo.
        """
        duenos = super()._visar_appointment_partners()
        duenos |= self.sudo().visar_visit_task_ids.mapped(
            'visar_subscription_order_id.partner_id')
        return duenos
