# -*- coding: utf-8 -*-
"""Avisos por WhatsApp del agendado: pago confirmado y apartado vencido.

El cliente reservaba por el chat, recibía la liga de pago… y ahí se acababa la
conversación. Pagaba y **nadie le confirmaba nada**; o dejaba pasar los diez
minutos y su apartado moría **en silencio**, con una liga que él seguía creyendo
buena. El agendado terminaba justo donde el cliente más necesita que le hablen.

El transporte es el de siempre (`visar.wa.outbox.mixin`, en `visar_base`): el
mismo que ya usaban los avisos de la app de campo. Aquí solo está lo propio de
una reserva: de qué cuelga el aviso y cuáles son.

**Por qué cuelga de `calendar.booking` y no del `sale.order`.** La reserva es lo
que existe en los dos finales: cuando el pago entra se convierte en cita, y
cuando el apartado vence sigue ahí para explicar qué pasó. El pedido puede no
haberse creado todavía.

**El teléfono es el `wa_id` EXACTO**, no el nacional de 10 dígitos. Es la clave
con la que el runtime encuentra la conversación, y la clave del apartado
(`owner_key`) no sirve: `5218112345678` y `8112345678` son la misma persona para
el dedupe de Odoo y dos conversaciones distintas para el runtime.
"""
from odoo import api, fields, models

# Catálogo CERRADO, como el de la app de campo. El runtime mapea cada clave a su
# plantilla aprobada; Odoo no puede pedir "manda esta plantilla".
TEMPLATE_KEYS = [
    ('booking_confirmed', "Cita confirmada (pago recibido)"),
    ('hold_expired', "Se venció el apartado (sin liga de pago)"),
    ('hold_expired_link', "Se venció el apartado (con liga enviada)"),
]

# Vida útil, en minutos. Sale de para qué sirve el mensaje:
#   * la confirmación de una cita vale mientras la cita exista — un día de sobra;
#   * el aviso de apartado vencido es urgente y perecedero: llegar media hora
#     tarde a decir "se te acabó el tiempo" no ayuda, molesta.
TTL_MINUTES = {
    'booking_confirmed': 24 * 60,
    'hold_expired': 30,
    'hold_expired_link': 30,
}
DEFAULT_TTL_MINUTES = 60


class VisarWaBookingMessage(models.Model):
    _name = 'visar.wa.booking.message'
    _inherit = ['visar.wa.outbox.mixin']
    _description = "Aviso por WhatsApp del agendado (buzón de salida)"
    _order = 'create_date desc, id desc'
    _rec_name = 'template_key'

    calendar_booking_id = fields.Many2one(
        'calendar.booking', string="Reserva", ondelete='set null', index=True,
        help="La reserva que originó el aviso. Puede quedar vacía: al vencer un "
             "apartado sin liga de pago todavía no hay reserva que apuntar.")
    partner_id = fields.Many2one(
        'res.partner', string="Cliente", index=True,
        help="Solo para poder buscar por cliente; el envío usa `phone`.")

    # ------------------------------------------------------------------
    # Lo que el mixin pide
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_keys(self):
        return TEMPLATE_KEYS

    @api.model
    def _visar_wa_ttl_minutes(self, template_key):
        return TTL_MINUTES.get(template_key, DEFAULT_TTL_MINUTES)

    @api.model
    def _visar_wa_cron_xmlid(self):
        return 'visar_whatsapp_agent.visar_wa_booking_outbox_cron'

    @api.model
    def _visar_wa_endpoint(self):
        # No es solo un texto: el runtime tiene que dejar la conversación lista
        # para lo que sigue (volver a elegir horario, o cerrar la reserva).
        return '/internal/booking-event'

    def _visar_wa_chatter(self):
        """`calendar.booking` no tiene chatter; la nota va al cliente.

        Es donde alguien la va a ver: la ficha del cliente es lo que abre el
        asesor cuando le dicen "este señor pagó y no le llegó nada".
        """
        self.ensure_one()
        return self.partner_id
