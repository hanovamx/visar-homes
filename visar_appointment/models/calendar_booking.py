# -*- coding: utf-8 -*-
"""Reserva pendiente de pago: creacion compartida y liberacion del apartado."""
from odoo import Command, api, models


class CalendarBooking(models.Model):
    _inherit = 'calendar.booking'

    @api.model
    def _visar_create_for_booking(self, appointment_type, date_start, date_end,
                                  description, allday, answer_input_values, name,
                                  customer, appointment_invite=None, guests=None,
                                  staff_user=None, asked_capacity=1,
                                  booking_line_values=None):
        """Crea la reserva pendiente de pago con todos sus campos.

        Estaba en el controlador del wizard; se bajo aqui porque el agente de
        WhatsApp crea exactamente la misma reserva sin peticion HTTP.
        `appointment_invite` es opcional: por WhatsApp no hay invitacion de por
        medio.
        """
        return self.sudo().create([{
            'allday': bool(allday),
            'appointment_answer_input_ids': [
                Command.create(vals) for vals in (answer_input_values or [])
            ],
            'appointment_invite_id': appointment_invite.id if appointment_invite else False,
            'appointment_type_id': appointment_type.id,
            'asked_capacity': asked_capacity,
            'booking_line_ids': [
                Command.create(vals) for vals in (booking_line_values or [])
            ],
            'description': description,
            'guest_ids': [Command.link(pid) for pid in guests.ids] if guests else [],
            'name': name,
            'partner_id': customer.id,
            'product_id': appointment_type.product_id.id,
            'staff_user_id': staff_user.id if staff_user else False,
            'start': date_start,
            'stop': date_end,
        }])

    def _make_event_from_paid_booking(self):
        """Al confirmarse la reserva, su apartado ya no hace falta.

        Se libera DESPUES del super: si el nativo no pudo crear la cita (su
        `_filter_unavailable_bookings` descarta las reservas que perdieron el
        horario), el apartado debe seguir en pie. Solo se sueltan las reservas
        que efectivamente quedaron con cita.
        """
        result = super()._make_event_from_paid_booking()
        confirmed = self.filtered(lambda booking: booking.calendar_event_id)
        if confirmed:
            self.env['visar.slot.hold'].sudo().search([
                ('calendar_booking_id', 'in', confirmed.ids),
            ]).unlink()
        return result
