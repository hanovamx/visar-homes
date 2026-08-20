# -*- coding: utf-8 -*-
"""La reserva recuerda de qué WhatsApp vino, y avisa cuando el pago entra.

Dos cosas que faltaban al cerrar el agendado por el chat:

**1. El `wa_id` exacto.** El apartado guarda `owner_key`, el número nacional de
10 dígitos, que es lo que hace que "el mismo cliente" signifique lo mismo en todo
Odoo. Pero para el runtime la conversación se identifica por el `wa_id` completo
(`5218112345678`), y de 10 dígitos no se puede reconstruir: el prefijo de país y
el `1` de móvil se perdieron a propósito. Sin guardarlo, Odoo sabe a quién
avisar y no sabe **por dónde**.

**2. Nadie confirmaba el pago.** El cliente pagaba y el chat se quedaba mudo. La
cita se creaba, la tarea se generaba, y el único que no se enteraba era él —
justo después de haber pagado, que es cuando más falta hace decir algo.
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class CalendarBooking(models.Model):
    _inherit = 'calendar.booking'

    visar_wa_phone = fields.Char(
        string="WhatsApp de origen", index=True, copy=False,
        help="El wa_id EXACTO desde el que se reservó por WhatsApp. Vacío = la "
             "reserva vino del wizard web. Es la clave con la que el runtime "
             "encuentra la conversación; el nacional de 10 dígitos no sirve.")

    def _make_event_from_paid_booking(self):
        """Al confirmarse la reserva, se le dice al cliente por donde vino.

        Se avisa DESPUES del super y solo de las que de verdad quedaron con cita:
        el nativo descarta las reservas que perdieron el horario, y confirmarle
        una cita que no existe es peor que no decir nada.
        """
        result = super()._make_event_from_paid_booking()
        confirmadas = self.filtered(
            lambda b: b.calendar_event_id and b.visar_wa_phone)
        for booking in confirmadas:
            booking._visar_wa_notify_confirmed()
        return result

    def _visar_wa_notify_confirmed(self):
        """Encola la confirmación. **Nunca lanza**: esto corre dentro del cobro.

        Un fallo mandando un WhatsApp no puede tumbar la transacción que acaba de
        crear la cita — el cliente se quedaría pagado y sin cita por un aviso.
        """
        self.ensure_one()
        try:
            Tools = self.env['visar.agent.tools'].sudo()
            cuando = Tools._agent_window_label(self.start, self.stop)
            nombre = (self.partner_id.name or '').split(' ')[0]
            saludo = ("¡Listo, %s!" % nombre) if nombre else "¡Listo!"
            # La política de cancelación va EN la confirmación, no en un aviso
            # aparte: es el único momento en que el cliente la lee, y es justo
            # cuando acaba de pagar. Enterarse el día que quiere cancelar es
            # enterarse tarde.
            texto = (
                "%s Tu cita quedó confirmada. ✅\n\n"
                "*%s*\n\n"
                "Te avisamos cuando el técnico vaya en camino. "
                "Si necesitas algo, escríbeme por aquí.\n\n"
                "_Las citas pagadas no son cancelables ni reembolsables. Si "
                "necesitas cambiar tu cita, puedes reprogramarla sin costo con "
                "al menos 24 horas de anticipación._" % (saludo, cuando)
            )
            self.env['visar.wa.booking.message'].sudo()._visar_wa_enqueue(
                'booking_confirmed', self.visar_wa_phone, texto,
                params=[nombre or '', cuando],
                values={'calendar_booking_id': self.id,
                        'partner_id': self.partner_id.id})
        except Exception:  # noqa: BLE001 - un aviso no tumba un cobro
            _logger.exception(
                "No se pudo encolar la confirmacion de la reserva %s", self.id)
