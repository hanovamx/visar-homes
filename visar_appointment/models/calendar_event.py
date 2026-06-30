# -*- coding: utf-8 -*-
from odoo import fields, models


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    visar_zone_id = fields.Many2one(
        'visar.zone', string="Zona (Visar)",
        help="Zona geográfica respondida por el cliente al agendar.")
    visar_booking_items = fields.Json(
        string="Servicios reservados (Visar)",
        help="Snapshot de los servicios/variantes elegidos en el wizard multi-servicio (D-05).")
