# -*- coding: utf-8 -*-
from odoo import fields, models


class AppointmentResource(models.Model):
    """ Un recurso = un técnico. Se extiende con la zona(s) que cubre y los
    servicios (tipos de cita) que ejecuta, para filtrar disponibilidad por
    servicio + zona (D-03). """
    _inherit = 'appointment.resource'

    visar_zone_ids = fields.Many2many(
        'visar.zone', 'visar_resource_zone_rel', 'resource_id', 'zone_id',
        string="Zonas que cubre")
    visar_service_ids = fields.Many2many(
        'appointment.type', 'visar_resource_service_rel', 'resource_id', 'appointment_type_id',
        string="Servicios que ejecuta")
