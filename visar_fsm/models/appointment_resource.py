# -*- coding: utf-8 -*-
from odoo import fields, models


class AppointmentResource(models.Model):
    _inherit = 'appointment.resource'

    visar_employee_id = fields.Many2one(
        'hr.employee', string="Empleado",
        help="Empleado detrás de este recurso. Conviene que el recurso comparta su "
             "calendario laboral para que la disponibilidad respete su horario y ausencias.")
