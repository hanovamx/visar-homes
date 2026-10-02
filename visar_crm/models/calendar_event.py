# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    # ==================================================================
    # Una cita de Visar no abre su propia oportunidad de CRM (2-oct-2026)
    # ==================================================================
    #
    # `appointment_crm` crea una oportunidad en `calendar.event.create` por cada
    # cita cuyo tipo lleve `lead_create`, y en produccion **los cuatro tipos lo
    # llevan**. Ese lead nace POBRE: `_get_lead_values()` solo pone
    # `{name, partner_id, type, user_id, description}` — sin
    # `visar_wa_phone_norm` y sin `visar_service_group_id`, que son justamente la
    # pareja por la que buscan los dos automatismos de avance. Resultado medido:
    # 18 fichas que ningun automatismo podia encontrar y que oficina movia a mano.
    #
    # Y el equipo tampoco era una decision: sale del computo sobre `user_id`, o
    # sea **del tecnico que quedo como organizador de la cita**.
    #
    # Ahora la ficha la abre `visar_crm` desde la ORDEN y en borrador
    # (`sale.order._visar_crm_after_fill`), con identidad completa y en el equipo
    # del canal. Esta la suprime para que no haya dos.
    #
    # ## Que deja de pasar, y por que es inocuo
    #
    # Leido `appointment_crm/models/calendar_event.py` entero: las tres cosas que
    # se pierden cuelgan todas de `for meeting in events.filtered('opportunity_id')`,
    # que con un recordset vacio no itera —
    #   * el `activity_schedule('mail.mail_activity_data_meeting')`,
    #   * el `_message_log` "Meeting linked to Lead/Opportunity",
    #   * el `res_model_id`/`res_id` que escribe `_link_with_lead`.
    # El enlace cita <-> ficha NO se pierde: lo repone
    # `calendar.booking._make_event_from_paid_booking` apuntando a la ficha buena.
    #
    # Residuo conocido: `_compute_opportunity_id` es `@api.depends(
    # 'appointment_invite_id')` y `store=True`, asi que una `appointment.invite`
    # con oportunidad la impondria igual. El flujo Visar no usa invites (el agente
    # pasa `appointment_invite=None`), pero conviene saberlo.

    def _create_lead_from_appointment(self):
        """Sin oportunidad para las citas de Visar; el resto sigue igual.

        Se filtra por los tipos MAESTRO y de VALORACION, resueltos por sus
        helpers, y no por "los tipos Visar": ese conjunto no existe. En
        produccion `lead_create` esta en cuatro tipos, y **dos de ellos (12 y 15,
        "Fumigacion interior o exterior" duplicados legacy) no son ninguno de los
        dos**. Esos hay que apagarlos por configuracion; aqui no se pueden
        reconocer.

        El `super()` se conserva para cualquier otro tipo de cita — y es tambien
        lo que mantiene en pie el guardia de la pre-agenda de
        `visar_appointment/models/calendar_event.py`, que encadena por aqui.
        """
        AptType = self.env['appointment.type'].sudo()
        visar_types = (AptType._visar_get_master_appointment_type()
                       | AptType._visar_get_valuation_appointment_type())
        ajenas = self.filtered(
            lambda event: event.appointment_type_id not in visar_types)
        if ajenas:
            return super(CalendarEvent, ajenas)._create_lead_from_appointment()
        return self.env['crm.lead'].browse()
