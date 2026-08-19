# -*- coding: utf-8 -*-
"""El apartado se congela mientras hay un cobro en proceso.

Hoy el pago es SIMULADO (proveedor Demo) y resuelve al instante, así que nada de
esto llega a activarse. Existe igual, y a propósito: cuando entre Stripe una
transacción podrá quedar `pending` —3-D Secure, o métodos locales tipo SPEI/OXXO
que tardan— y si el apartado venciera a los 10 minutos con el cobro en vuelo se
caería en el peor caso posible: **el cliente paga y su horario ya se lo dieron a
otro**. Meter esto después obligaría a reabrir la pieza más delicada.

Regla (ver `.context/33-whatsapp-agendado-design.md` §7.3.1): el reloj del
apartado se mide contra el INICIO del pago, no contra su final.

  * pendiente / autorizado  → congelar (deja de vencer)
  * cancelado / con error   → descongelar y **reiniciar el reloj**, para que el
                              cliente pueda reintentar sin perder su lugar
  * pagado                  → lo libera `calendar.booking`, al crear la cita
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # ------------------------------------------------------------------
    # La liga de pago vive y muere con el apartado (diseño 33 §6.1)
    # ------------------------------------------------------------------
    #
    # Estaba **decidido y sin implementar**, y el hueco era real: la reserva
    # pendiente de pago no consume capacidad, así que un pago tardío pasaba, el
    # nativo descartaba la reserva en `_filter_unavailable_bookings` y el cliente
    # se quedaba **pagado y sin cita**, en silencio. Es el peor final del flujo.
    #
    # La regla no es "al vencer, la liga muere". Es más útil y más honesta:
    #
    #   * el apartado venció y **nadie tomó el horario** → se vuelve a apartar y
    #     el pago pasa. El cliente ni se entera; su lugar seguía ahí.
    #   * el apartado venció y **el horario ya es de otro** → se rechaza ANTES de
    #     cobrar, con un mensaje que dice qué pasó.
    #
    # Se engancha en `create` y no en el controlador del portal porque por ahí no
    # pasan todas las rutas de pago: aquí pasan todas. La transacción se crea en
    # borrador antes de hablar con el proveedor, así que rechazar aquí es
    # rechazar antes de que haya dinero de por medio.

    @api.model_create_multi
    def create(self, vals_list):
        transactions = super().create(vals_list)
        for transaction in transactions:
            transaction._visar_check_slot_available()
        return transactions

    def _visar_check_slot_available(self):
        """Rechaza el cobro si el horario de la reserva ya es de otro cliente.

        Silencioso para todo lo que no sea una reserva Visar con apartado: el
        canal web no crea apartados y no tiene por qué pasar por aquí.
        """
        self.ensure_one()
        bookings = (self.sale_order_ids | self.source_transaction_id.sale_order_ids
                    ).order_line.calendar_booking_ids
        for booking in bookings:
            if not booking._visar_ensure_hold_for_payment():
                # Sin la hora: aquí solo hay UTC, y decírsela al cliente sería
                # peor que no decirle ninguna. Ya la tiene en el chat.
                raise ValidationError(_(
                    "El horario que habías apartado ya no está disponible: se "
                    "acabó el tiempo del apartado y lo tomó otro cliente. No se "
                    "hizo ningún cargo. Escríbenos por WhatsApp y elegimos otro "
                    "horario."))

    def _visar_holds(self):
        """Apartados ligados a las órdenes de estas transacciones."""
        orders = self.sale_order_ids | self.source_transaction_id.sale_order_ids
        if not orders:
            return self.env['visar.slot.hold'].browse()
        bookings = orders.order_line.calendar_booking_ids
        if not bookings:
            return self.env['visar.slot.hold'].browse()
        return self.env['visar.slot.hold'].sudo().search([
            ('calendar_booking_id', 'in', bookings.ids),
        ])

    def _set_pending(self, *, state_message=None, extra_allowed_states=()):
        result = super()._set_pending(
            state_message=state_message, extra_allowed_states=extra_allowed_states)
        self._visar_holds()._visar_freeze()
        return result

    def _set_authorized(self, *, state_message=None, extra_allowed_states=()):
        result = super()._set_authorized(
            state_message=state_message, extra_allowed_states=extra_allowed_states)
        self._visar_holds()._visar_freeze()
        return result

    def _visar_thaw_and_restart(self):
        """Descongela y reinicia el reloj: el cliente puede reintentar el pago."""
        holds = self._visar_holds()
        if not holds:
            return
        minutes = self.env['visar.slot.hold']._visar_hold_minutes()
        holds.sudo().write({
            'is_frozen': False,
            'expire_at': fields.Datetime.add(fields.Datetime.now(),
                                             minutes=minutes),
        })

    def _set_canceled(self, state_message=None, extra_allowed_states=()):
        result = super()._set_canceled(
            state_message=state_message, extra_allowed_states=extra_allowed_states)
        self._visar_thaw_and_restart()
        return result

    def _set_error(self, state_message, extra_allowed_states=()):
        result = super()._set_error(
            state_message, extra_allowed_states=extra_allowed_states)
        self._visar_thaw_and_restart()
        return result
