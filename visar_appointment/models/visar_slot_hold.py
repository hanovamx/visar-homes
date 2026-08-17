# -*- coding: utf-8 -*-
"""Apartado temporal de un horario (butaca de cine).

Existe porque una reserva pendiente de pago **no consume capacidad** en Odoo:
`appointment.type._get_resources_remaining_capacity` solo cuenta
`appointment.booking.line`, que cuelgan de citas ya CONFIRMADAS. Las
`calendar.booking.line` de una reserva sin pagar son invisibles. Resultado: dos
clientes pueden llegar al pago del mismo horario, y al segundo
`_make_event_from_paid_booking` le descarta la reserva en silencio — paga y se
queda sin cita.

En el web ese hueco es estrecho (un checkout seguido). Por WhatsApp se ensancha:
entre "te mando la liga" y "el pago entra" pasan minutos. De ahí este modelo.

**Se aplica a los dos canales.** El apartado se descuenta en
`appointment.type._get_resources_remaining_capacity`, que es el único punto por
el que pasan TODOS los caminos: la generación de slots del calendario, la
validación final al enviar el formulario, y las lecturas del agente. Filtrar solo
en `_visar_filter_slots_multi_service` no habría bastado — la rama de valoración
no pasa por ahí (`_visar_wizard_active()` solo es cierto con `mode == 'wizard'`).

Dos reglas que no son evidentes:

* **El dueño del apartado no se bloquea a sí mismo.** Si el cliente que apartó el
  horario no pudiera reservarlo, el apartado sería un tiro en el pie. Por eso la
  resta ignora los holds del `visar_hold_owner` que venga en el contexto.
* **Un pago en vuelo congela el reloj** (`is_frozen`). Hoy el pago es simulado y
  resuelve al instante, así que nunca se activa; con Stripe una transacción puede
  quedar `pending` (3-D Secure, SPEI/OXXO) y soltar el horario a mitad del cobro
  sería el peor caso posible: el cliente paga y su lugar ya se lo dieron a otro.
  Ver `.context/33-whatsapp-agendado-design.md` §6.1 y §7.3.1.
"""
from odoo import api, fields, models

# Minutos que dura un apartado. Configurable sin tocar codigo desde
# Ajustes > Tecnico > Parametros del sistema.
HOLD_MINUTES_PARAM = 'visar.slot_hold_minutes'
DEFAULT_HOLD_MINUTES = 10


class VisarSlotHold(models.Model):
    _name = 'visar.slot.hold'
    _description = "Visar - Apartado temporal de horario"
    _order = 'expire_at desc, id desc'
    _rec_name = 'owner_key'

    appointment_resource_id = fields.Many2one(
        'appointment.resource', string="Técnico", required=True,
        ondelete='cascade', index=True)
    start = fields.Datetime(
        string="Inicio", required=True, index=True,
        help="Inicio del horario apartado, en UTC naive (misma convención que "
             "appointment.booking.line.event_start).")
    stop = fields.Datetime(string="Fin", required=True)
    capacity = fields.Integer(
        string="Capacidad apartada", default=1, required=True,
        help="Cuánta capacidad del técnico ocupa este apartado. Casi siempre 1.")
    expire_at = fields.Datetime(
        string="Vence", required=True, index=True,
        help="Cuándo deja de valer el apartado. Se ignora mientras `is_frozen`.")
    owner_key = fields.Char(
        string="Dueño", required=True, index=True,
        help="Teléfono en forma nacional (últimos 10 dígitos). Es la MISMA clave "
             "que usa el dedupe de reservas y el agente de WhatsApp, para que "
             "'el mismo cliente' signifique lo mismo en todos lados.")
    calendar_booking_id = fields.Many2one(
        'calendar.booking', string="Reserva pendiente", ondelete='cascade',
        index=True,
        help="Se llena al preparar el pago. Al confirmarse la reserva, el "
             "apartado se libera.")
    is_frozen = fields.Boolean(
        string="Pago en vuelo", default=False,
        help="Con un cobro en proceso el apartado NO vence: soltar el horario a "
             "mitad del pago dejaría al cliente pagado y sin cita.")

    # ------------------------------------------------------------------
    # Configuracion
    # ------------------------------------------------------------------

    @api.model
    def _visar_hold_minutes(self):
        """Duración del apartado en minutos (parámetro global, 10 por defecto)."""
        raw = self.env['ir.config_parameter'].sudo().get_param(
            HOLD_MINUTES_PARAM, DEFAULT_HOLD_MINUTES)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_HOLD_MINUTES

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    @api.model
    def _visar_active_domain(self):
        """Apartados que siguen valiendo: sin vencer, o congelados por un pago.

        La disponibilidad filtra por aquí SIEMPRE, así que la correccion no
        depende de que el cron haya corrido: un apartado vencido deja de estorbar
        en el momento en que vence, lo borren o no.
        """
        return ['|', ('is_frozen', '=', True),
                ('expire_at', '>', fields.Datetime.now())]

    @api.model
    def _visar_overlapping(self, resources, start, stop, exclude_owner=None,
                           exclude_ids=None):
        """Apartados vivos que pisan [start, stop) para esos técnicos.

        `exclude_owner` deja fuera los del propio cliente: quien apartó el horario
        tiene que poder reservarlo.
        """
        if not resources or not start or not stop:
            return self.browse()
        domain = self._visar_active_domain() + [
            ('appointment_resource_id', 'in', resources.ids),
            ('start', '<', stop),
            ('stop', '>', start),
        ]
        if exclude_owner:
            domain.append(('owner_key', '!=', exclude_owner))
        if exclude_ids:
            domain.append(('id', 'not in', list(exclude_ids)))
        return self.sudo().search(domain)

    @api.model
    def _visar_used_capacity(self, resources, start, stop, exclude_owner=None,
                             exclude_ids=None):
        """`{resource_id: capacidad apartada}` para el rango dado."""
        used = {}
        for hold in self._visar_overlapping(resources, start, stop,
                                            exclude_owner=exclude_owner,
                                            exclude_ids=exclude_ids):
            key = hold.appointment_resource_id.id
            used[key] = used.get(key, 0) + max(hold.capacity, 0)
        return used

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    @api.model
    def _visar_hold(self, resource, start, stop, owner_key, capacity=1,
                    minutes=None):
        """Aparta un horario para un cliente. Devuelve el registro creado.

        Un teléfono no puede tener varios apartados a la vez: pedir uno nuevo
        libera el anterior. Si no, un cliente indeciso podría bloquear la agenda
        entera saltando de horario en horario.
        """
        if not resource or not owner_key:
            return self.browse()
        self._visar_release(owner_key=owner_key)
        minutes = minutes or self._visar_hold_minutes()
        return self.sudo().create({
            'appointment_resource_id': resource.id,
            'start': start,
            'stop': stop,
            'capacity': capacity or 1,
            'owner_key': owner_key,
            'expire_at': fields.Datetime.add(fields.Datetime.now(),
                                             minutes=minutes),
        })

    @api.model
    def _visar_release(self, owner_key=None, booking=None):
        """Libera los apartados de un cliente y/o de una reserva."""
        conditions = []
        if owner_key:
            conditions.append([('owner_key', '=', owner_key)])
        if booking:
            conditions.append([('calendar_booking_id', '=', booking.id)])
        if not conditions:
            return 0
        domain = conditions[0]
        for extra in conditions[1:]:
            domain = ['|'] + domain + extra
        holds = self.sudo().search(domain)
        count = len(holds)
        holds.unlink()
        return count

    def _visar_freeze(self):
        """Congela el reloj: hay un cobro en proceso (ver docstring del módulo)."""
        return self.sudo().write({'is_frozen': True})

    @api.model
    def _visar_cron_gc(self):
        """Borra los apartados vencidos. Red de seguridad, no camino crítico.

        La disponibilidad ya ignora lo vencido (`_visar_active_domain`), así que
        esto solo evita que la tabla crezca sin fin.
        """
        expired = self.sudo().search([
            ('is_frozen', '=', False),
            ('expire_at', '<=', fields.Datetime.now()),
        ])
        count = len(expired)
        expired.unlink()
        return count
