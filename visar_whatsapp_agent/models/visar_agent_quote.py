# -*- coding: utf-8 -*-
"""El agente agenda y cobra una COTIZACIÓN ya hecha (22-sep-2026).

Paso 3 de los tratamientos que se cotizan a mano (termitas, chinches; ver
`visar_field_app/models/cotizacion_manual.py`). Oficina ya puso el precio y eligió
"Agendar después"; Visar le manda al cliente el aviso `quote_ready` con el monto y
un botón "Elegir fecha". Al tocarlo, el runtime recorre con estas herramientas el
mismo camino que una reserva: días → horarios → apartar → liga de pago.

Lo que cambia respecto de una reserva nueva, y por qué:

* **No se arma un pedido: ya existe.** La cotización ES el pedido. Se le cuelga la
  reserva (`calendar.booking.order_line_id` = la línea del tratamiento) y la liga
  cobra la cotización. Al pagarse, Odoo la confirma, `website_appointment_sale`
  convierte la reserva en cita y sale la confirmación de siempre.
* **Horarios de visita de valoración**: una hora, viaje incluido, con cualquier
  técnico elegible de la zona (Visar, 22-sep-2026: "cualquier técnico puede").
  Es el modo `valuation` del agendado, sin cuestionario.
* **El dueño se comprueba contra el cliente de la cotización**, y "no existe" y "es
  de otro" contestan lo mismo, como en la reagenda: si no, esto serviría para
  averiguar qué cotizaciones existen.

`visar_whatsapp_agent` no depende de `visar_field_app` (y al revés tampoco). Los
campos de la cotización se leen con guarda: sin la app de campo instalada, no hay
cotizaciones que agendar y todo contesta `not_found`.
"""
from odoo import api, fields, models

MOTIVO_NO_ENCONTRADA = 'not_found'


class VisarAgentTools(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    # ------------------------------------------------------------------
    # Resolver la cotización
    # ------------------------------------------------------------------
    @api.model
    def _agent_quote(self, payload):
        """(cotización, motivo). Resuelve la cotización y comprueba que es del
        teléfono y que está lista para agendarse.

        Sin `quote_id` se busca la ÚNICA cotización lista de ese cliente: la
        conversación caduca en 3 h y el aviso vale días, así que quien toca
        "Elegir fecha" mañana llega a una conversación que ya no sabe cuál era.
        """
        payload = payload or {}
        Order = self.env['sale.order'].sudo()
        vacio = Order.browse()
        if 'visar_quote_path' not in Order._fields:
            return vacio, MOTIVO_NO_ENCONTRADA
        partner = self._agent_find_partner(payload.get('phone'))
        if not partner:
            return vacio, MOTIVO_NO_ENCONTRADA
        comercial = partner.commercial_partner_id

        try:
            quote_id = int(payload.get('quote_id') or 0)
        except (TypeError, ValueError):
            quote_id = 0
        if quote_id:
            quote = Order.browse(quote_id).exists()
        else:
            quote = Order.search([
                ('visar_quote_path', '=', 'agendar'),
                ('state', 'in', ('draft', 'sent')),
                ('partner_id.commercial_partner_id', '=', comercial.id),
            ])
            if len(quote) != 1:
                return vacio, MOTIVO_NO_ENCONTRADA
        if not quote or quote.partner_id.commercial_partner_id != comercial:
            return vacio, MOTIVO_NO_ENCONTRADA
        if quote.state == 'sale':
            return quote, 'paid'
        if quote.state not in ('draft', 'sent') or quote.visar_quote_path != 'agendar':
            return quote, 'not_ready'
        return quote, None

    @api.model
    def _agent_quote_booking_payload(self, quote, phone):
        """Lo que el agendado necesita para buscar horarios: modo valoración, la zona
        y la dirección de servicio de la cotización (para el filtro de traslados)."""
        casa = quote.partner_shipping_id or quote.partner_id
        return {
            'mode': 'valuation',
            'phone': phone,
            'cp': casa.zip or quote.partner_id.zip or '',
            'delivery_address': {
                'street': casa.street or '',
                'neighborhood': casa.street2 or '',
                'zip': casa.zip or '',
                'city': casa.city or '',
            },
        }

    @api.model
    def _agent_quote_label(self, quote):
        lineas = quote.order_line.filtered(
            lambda l: not l.display_type and l.price_unit > 0)
        return ", ".join(lineas.product_id.mapped('name')) or "tu servicio"

    # ------------------------------------------------------------------
    # RPCs
    # ------------------------------------------------------------------
    @api.model
    def agent_quote_days(self, payload):
        """Días con hueco para agendar una cotización.

        `payload` = {"phone": "5218112345678", "quote_id": 445 | None}
        Devuelve {"days", "min_hours", "blocked", "quote_id", "service", "total",
        "currency", "message"}. `blocked` ∈ {None, 'not_found', 'not_ready', 'paid'}.
        """
        quote, motivo = self._agent_quote(payload)
        if motivo:
            return {'days': [], 'min_hours': 0, 'blocked': motivo, 'quote_id': None,
                    'message': "No encontré esa cotización a tu nombre."}
        result = self.agent_available_days(
            self._agent_quote_booking_payload(quote, payload.get('phone')))
        result.update({
            'blocked': None,
            'quote_id': quote.id,
            'service': self._agent_quote_label(quote),
            'total': quote.amount_total,
            'currency': quote.currency_id.name,
        })
        return result

    @api.model
    def agent_quote_slots(self, payload):
        """Horarios de un día. `payload` = {phone, quote_id, date}."""
        quote, motivo = self._agent_quote(payload)
        if motivo:
            return {'date': None, 'slots': [], 'blocked': motivo,
                    'message': "No encontré esa cotización a tu nombre."}
        consulta = dict(self._agent_quote_booking_payload(quote, payload.get('phone')),
                        date=payload.get('date'))
        result = self.agent_day_slots(consulta)
        result['blocked'] = None
        return result

    @api.model
    def agent_quote_prepare(self, payload):
        """Aparta el horario, le cuelga la reserva a la cotización y devuelve la liga.

        `payload` = {"phone", "quote_id", "slot": {"start", "stop"}} (UTC naive).
        Devuelve lo mismo que `agent_prepare_booking`: {"prepared", "reason",
        "message", "payment_url", "expire_at", "total", "currency", "booking_id",
        "order_id", "hold_id"}. Nunca lanza.

        Elegir OTRO horario después de recibir la liga es normal ("mejor el
        jueves"): la reserva anterior sin pagar se borra aquí mismo —con ella su
        apartado— y la cotización queda con una sola. La liga es la misma, porque
        cobra la cotización y no el horario.
        """
        payload = payload or {}
        quote, motivo = self._agent_quote(payload)
        if motivo:
            return self._agent_booking_fail(
                motivo, "No encontré esa cotización a tu nombre.")

        slot = payload.get('slot') or {}
        start = fields.Datetime.to_datetime(slot.get('start'))
        stop = fields.Datetime.to_datetime(slot.get('stop'))
        if not (start and stop) or stop <= start:
            return self._agent_booking_fail('slot_invalid', "El horario no es valido.")

        booking = self._agent_quote_booking_payload(quote, payload.get('phone'))
        zone = self._agent_booking_zone(booking)
        if not zone:
            return self._agent_booking_fail(
                'out_of_coverage', "Ese codigo postal esta fuera de cobertura.")
        AptType = self.env['appointment.type'].sudo()
        apt_type = AptType._visar_get_valuation_appointment_type()
        if not apt_type:
            return self._agent_booking_fail(
                'config_missing', "Falta configurar el tipo de cita.")

        self._agent_quote_release_bookings(quote)

        owner_key = self.env['res.partner'].sudo()._visar_phone_nat10_value(
            payload.get('phone'))
        apt_type = apt_type.with_context(visar_hold_owner=owner_key)
        items = AptType._visar_wizard_valuation_items()
        resources = self._agent_pick_resources(
            apt_type, zone, items, start, stop, 'valuation', 1)
        if not resources:
            return self._agent_booking_fail(
                'slot_taken', "Ese horario ya no esta disponible.")

        linea = quote.order_line.filtered(
            lambda l: not l.display_type and l.price_unit > 0)[:1]
        if not linea:
            return self._agent_booking_fail(
                'not_ready', "La cotización no tiene un servicio con precio.")

        hold = self.env['visar.slot.hold']._visar_hold(resources[0], start, stop, owner_key)
        wa_phone = payload.get('phone') or False
        if hold:
            hold.sudo().visar_wa_phone = wa_phone
        partner = quote.partner_id
        descripcion = "Cotización %s — %s" % (quote.name, self._agent_quote_label(quote))
        calendar_booking = self.env['calendar.booking']._visar_create_for_booking(
            apt_type, start, stop, descripcion, False, [], partner.name, partner,
            asked_capacity=1,
            booking_line_values=self._agent_booking_line_values(
                apt_type, resources, start, stop, 1))
        calendar_booking.sudo().write({
            'order_line_id': linea.id,
            'visar_wa_phone': wa_phone,
        })
        if hold:
            hold.sudo().calendar_booking_id = calendar_booking.id

        quote.sudo().require_payment = True
        link = self.env['payment.link.wizard'].sudo().create({
            'res_model': 'sale.order',
            'res_id': quote.id,
            'amount': quote.amount_total,
            'currency_id': quote.currency_id.id,
            'partner_id': quote.partner_id.id,
        })
        cuando = self._agent_window_label(start, stop)
        quote.message_post(body="El cliente eligió por WhatsApp: %s. Se le mandó la "
                                "liga de pago; el horario queda apartado mientras paga."
                           % cuando)
        return {
            'prepared': True,
            'reason': None,
            'message': None,
            'payment_url': link.link,
            'expire_at': hold.expire_at.isoformat() if hold else None,
            'total': quote.amount_total,
            'currency': quote.currency_id.name,
            'booking_id': calendar_booking.id,
            'order_id': quote.id,
            'hold_id': hold.id if hold else None,
            'when_label': cuando,
        }

    @api.model
    def agent_quote_release(self, payload):
        """El cliente se arrepintió después de recibir la liga: se suelta el horario.

        `payload` = {phone, quote_id}. Devuelve {"released": bool, "reason"}. La
        cotización NO se cancela —sigue vigente para cuando quiera agendar—, solo se
        borra la reserva sin pagar y con ella su apartado. Pagar la liga después
        confirmaría la cotización sin horario, y oficina la agenda a mano.
        """
        quote, motivo = self._agent_quote(payload)
        if motivo:
            return {'released': False, 'reason': motivo}
        soltadas = self._agent_quote_release_bookings(quote)
        return {'released': bool(soltadas), 'reason': None}

    @api.model
    def _agent_quote_release_bookings(self, quote):
        """Borra las reservas sin pagar de la cotización (arrastra sus apartados)."""
        reservas = quote.order_line.calendar_booking_ids.filtered(
            lambda b: not b.calendar_event_id)
        cuantas = len(reservas)
        if reservas:
            reservas.sudo().unlink()
        return cuantas
