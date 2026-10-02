# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CalendarBooking(models.Model):
    _inherit = 'calendar.booking'

    def _make_event_from_paid_booking(self):
        """Repone el enlace cita <-> ficha que el core daba, a la ficha BUENA.

        `visar_crm` suprime la oportunidad pobre que `appointment_crm` abria en
        el `create` del evento (ver `calendar_event.py`), y con ella se iba el
        `calendar.event.opportunity_id` — que si es util: es como se ve desde una
        cita a que negociacion pertenece.

        Se hace DESPUES del `super()` por necesidad, no por estilo:
        `calendar.booking.calendar_event_id` se escribe en la linea siguiente al
        `create` del evento (`appointment_account_payment/models/
        calendar_booking.py`), asi que durante el `create` el enlace inverso
        todavia no existe y desde el evento no hay forma de llegar a su orden.

        Best-effort: un fallo aqui no puede impedir que la cita exista.
        """
        result = super()._make_event_from_paid_booking()
        for booking in self.filtered(lambda b: b.calendar_event_id):
            oportunidad = booking.order_line_id.order_id.opportunity_id
            if not oportunidad or booking.calendar_event_id.opportunity_id:
                continue
            try:
                booking.calendar_event_id.sudo().write(
                    {'opportunity_id': oportunidad.id})
            except Exception:  # noqa: BLE001 - la cita manda, el enlace no
                _logger.exception(
                    "visar_crm: no se pudo enlazar la cita %s con la ficha %s",
                    booking.calendar_event_id.id, oportunidad.id)
        return result
