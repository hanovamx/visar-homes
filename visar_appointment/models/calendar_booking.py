# -*- coding: utf-8 -*-
"""Reserva pendiente de pago: creacion compartida y liberacion del apartado."""
from odoo import Command, api, fields, models


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

    def _visar_ensure_hold_for_payment(self):
        """¿Se puede cobrar esta reserva? Y si sí, se vuelve a apartar el horario.

        Se llama justo antes de crear la transacción (ver
        `payment_transaction.py`). Tres casos:

          * **apartado vivo** → nada que hacer, adelante;
          * **apartado vencido pero el horario libre** → se vuelve a apartar y
            adelante. El cliente pagó tarde y su lugar seguía ahí: cobrarle y
            darle la cita es exactamente lo correcto;
          * **horario ocupado por otro** → False, y el cobro se rechaza antes de
            que haya dinero de por medio.

        Volver a apartar no es un detalle: sin apartado,
        `_filter_unavailable_bookings` no tiene qué ignorar y el nativo puede
        descartar la reserva **después** de cobrar.

        Devuelve True para todo lo que no sea una reserva con apartado: el wizard
        web no crea apartados y no tiene por qué pasar por esta regla.
        """
        self.ensure_one()
        Hold = self.env['visar.slot.hold'].sudo()
        holds = Hold.search([('calendar_booking_id', '=', self.id)])
        if not holds:
            return True
        vivo = holds.filtered(
            lambda h: h.is_frozen or h.expire_at > fields.Datetime.now())
        if vivo:
            return True

        # Venció. ¿Sigue libre? El apartado propio ya no cuenta (venció), así que
        # lo que quede ocupado lo ocupa otro.
        hold = holds[0]
        resource = hold.appointment_resource_id
        apt_type = self.appointment_type_id
        if not apt_type or resource not in apt_type.resource_ids:
            return True  # configuración rara: no se bloquea un cobro por dudar
        remaining = apt_type._get_resources_remaining_capacity(
            resource, hold.start, hold.stop, with_linked_resources=False)
        if remaining.get('total_remaining_capacity', 0) < (hold.capacity or 1):
            return False

        holds.write({
            'expire_at': fields.Datetime.add(
                fields.Datetime.now(), minutes=Hold._visar_hold_minutes()),
        })
        return True

    def _filter_unavailable_bookings(self):
        """Una reserva NO compite contra su propio apartado.

        Sin esto el apartado se muerde la cola y provoca exactamente el desastre
        que fue escrito para evitar. El nativo consulta la capacidad **sin**
        contexto, asi que el override de `_get_resources_remaining_capacity` le
        restaba el apartado del propio cliente, declaraba el horario sin cupo y
        descartaba la reserva; el cobro ya habia entrado. Resultado medido en el
        servidor: `order.state = sale`, `tx = done`, `calendar_event_id = None`,
        y el apartado sin liberar. **Todas** las reservas por WhatsApp acababan
        cobradas y sin cita.

        El wizard web nunca lo pisaba porque el web no crea apartados.
        """
        holds = self.env['visar.slot.hold'].sudo().search([
            ('calendar_booking_id', 'in', self.ids),
        ])
        records = self.with_context(
            visar_ignore_hold_ids=holds.ids) if holds else self
        return super(CalendarBooking, records)._filter_unavailable_bookings()

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
