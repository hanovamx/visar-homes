# -*- coding: utf-8 -*-
"""Al vencer un apartado se le dice al cliente. Antes moría en silencio.

El cliente elegía horario, recibía la liga, y a los diez minutos su lugar volvía
al inventario **sin que nadie se lo dijera**. Se quedaba con una liga en el chat
que él seguía creyendo buena y un horario que ya no era suyo; y si tardaba en
pagar, el peor final posible: pagar y descubrir después que no había cita.

**Qué se le dice depende de si el horario sigue libre**, y eso se comprueba en el
momento (decisión de negocio, agosto 2026: *la liga sigue pagando mientras nadie
haya tomado el lugar*). Decirle "tu liga ya no sirve" cuando sí sirve es tan malo
como no decirle nada.

  * **Sigue libre** → "se acabó el apartado, pero el horario sigue ahí: si pagas
    con la liga que te mandé lo confirmamos igual".
  * **Lo tomó alguien** → "la liga ya no sirve, ¿elegimos otro?".
  * **Sin liga todavía** (venció en la pantalla de revisión) → "se soltó tu
    horario, ¿buscamos otro?".

El aviso se encola ANTES de borrar: después del `unlink` no queda de dónde sacar
ni el teléfono ni la hora.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class VisarSlotHold(models.Model):
    _inherit = 'visar.slot.hold'

    visar_wa_phone = fields.Char(
        string="WhatsApp de origen", index=True, copy=False,
        help="El wa_id EXACTO del cliente que apartó por WhatsApp. Vacío = el "
             "apartado no vino del chat y no hay a quién avisar.")

    @api.model
    def _visar_cron_gc(self):
        """Avisa a quien se le venció el apartado, y luego barre como siempre."""
        expirados = self.sudo().search([
            ('is_frozen', '=', False),
            ('expire_at', '<=', fields.Datetime.now()),
            ('visar_wa_phone', '!=', False),
        ])
        for hold in expirados:
            hold._visar_wa_notify_expired()
        return super()._visar_cron_gc()

    def _visar_slot_sigue_libre(self):
        """¿Alguien más tomó este horario? Se pregunta al soltar, no antes.

        El apartado propio ya no cuenta (venció, y `_visar_active_domain` filtra
        por fecha), así que lo que quede ocupado lo ocupa otro.
        """
        self.ensure_one()
        apt_type = (self.calendar_booking_id.appointment_type_id
                    or self.env['appointment.type'].sudo()
                    ._visar_get_master_appointment_type())
        if not apt_type or self.appointment_resource_id not in apt_type.resource_ids:
            # Sin tipo de cita no se puede afirmar que siga libre; se asume que no
            # para no prometerle al cliente un horario que quizá no exista.
            return False
        remaining = apt_type._get_resources_remaining_capacity(
            self.appointment_resource_id, self.start, self.stop,
            with_linked_resources=False)
        return remaining.get('total_remaining_capacity', 0) >= (self.capacity or 1)

    def _visar_wa_notify_expired(self):
        """Encola el aviso de apartado vencido. **Nunca lanza**: corre en un cron
        cuya obligación es barrer, y un aviso no puede impedir el barrido."""
        self.ensure_one()
        try:
            Tools = self.env['visar.agent.tools'].sudo()
            cuando = Tools._agent_window_label(self.start, self.stop)
            booking = self.calendar_booking_id
            if not booking:
                clave = 'hold_expired'
                texto = (
                    "Se acabó el tiempo que tenía apartado el horario que elegiste "
                    "(*%s*) y ya volvió a estar disponible para otros clientes.\n\n"
                    "¿Buscamos otro?" % cuando)
            elif self._visar_slot_sigue_libre():
                clave = 'hold_expired_link'
                texto = (
                    "Se acabó el tiempo del apartado, pero *ese horario sigue "
                    "libre* (%s).\n\n"
                    "Si todavía lo quieres, paga con la liga que te mandé y lo "
                    "confirmamos igual. ¿O prefieres elegir otro?" % cuando)
            else:
                clave = 'hold_expired_link'
                texto = (
                    "Se acabó el tiempo del apartado y alguien más tomó ese "
                    "horario (%s), así que *la liga de pago que te mandé ya no "
                    "sirve*.\n\n"
                    "¿Elegimos otro?" % cuando)
            self.env['visar.wa.booking.message'].sudo()._visar_wa_enqueue(
                clave, self.visar_wa_phone, texto, params=[cuando],
                values={'calendar_booking_id': booking.id or False,
                        'partner_id': booking.partner_id.id or False})
        except Exception:  # noqa: BLE001 - un aviso no bloquea el barrido
            _logger.exception(
                "No se pudo encolar el aviso de apartado vencido %s", self.id)
