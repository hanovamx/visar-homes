# -*- coding: utf-8 -*-
"""El recontacto que el cliente todavía no contesta, para poder retomarlo.

El recontacto sale temprano y el cliente contesta cuando puede: el 21-oct-2026,
diez horas después. Para entonces el runtime ya caducó la conversación (3 h) y
lo que contestaba —«¿qué quedó pendiente?»— le llegaba al saludo de un cliente
nuevo. Odoo sí lo recuerda: el lead tiene la foto, el texto que se mandó y
cuándo. Aquí se le devuelve al runtime, con el contexto del cliente, para que
retome en vez de empezar de cero.

Vale mientras:

* el recontacto siga **Enviado** (si el cliente volvió a escribir con algo de
  interés, ya se reprogramó: hay conversación nueva y nada que retomar);
* no hayan pasado más de `vigencia_horas` (Recontacto de leads, 48 h);
* el lead siga en **Nuevo** y nadie lo haya escalado después de mandarlo.
"""
import logging
from datetime import timedelta

from odoo import api, fields, models

from .crm_lead import WA_STAGE_NUEVO_XMLID

_logger = logging.getLogger(__name__)

VIGENCIA_HORAS_DEFAULT = 48


class VisarAgentRecontacto(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    @api.model
    def agent_partner_facts(self, payload):
        """Lo de siempre, más `recontacto` si hay uno sin contestar.

        `recontacto` = {"texto": str, "contexto": dict, "enviado": iso}. Sin
        recontacto vigente la clave no va. Un runtime viejo la ignora.
        """
        datos = super().agent_partner_facts(payload)
        try:
            with self.env.cr.savepoint():
                recontacto = self._agent_recontacto_vigente(
                    (payload or {}).get('phone'))
        except Exception:  # noqa: BLE001 - un dato de contexto no tumba nada
            _logger.exception("agent_partner_facts: no se pudo leer el recontacto")
            recontacto = None
        if recontacto:
            datos['recontacto'] = recontacto
        return datos

    @api.model
    def _agent_recontacto_vigente(self, phone, ahora=None):
        """El último recontacto enviado a este teléfono que se puede retomar, o None."""
        nat = self._agent_normalize_phone(phone)
        if len(nat) != 10:
            return None
        nuevo = self.env.ref(WA_STAGE_NUEVO_XMLID, raise_if_not_found=False)
        if not nuevo:
            return None
        config = self.env['visar.followup.config'].sudo()._visar_active()
        horas = config.vigencia_horas if config else VIGENCIA_HORAS_DEFAULT
        ahora = ahora or fields.Datetime.now()
        leads = self.env['crm.lead'].sudo().search([
            ('visar_wa_phone_norm', '=', nat),
            ('visar_wa_followup_state', '=', 'sent'),
            ('visar_wa_followup_sent_at', '>=', ahora - timedelta(hours=horas)),
            ('stage_id', '=', nuevo.id),
        ], order='visar_wa_followup_sent_at desc, id desc')
        for lead in leads:
            # Un asesor lo tomó después de mandarlo: es suyo, no del agente.
            if lead.visar_wa_handoff_at \
                    and lead.visar_wa_handoff_at >= lead.visar_wa_followup_sent_at:
                continue
            contexto = lead._visar_wa_followup_data()
            contexto.pop('wa_id', None)
            texto = (lead.visar_wa_followup_texto or '').strip() \
                or lead._visar_wa_followup_fallback()
            return {
                'lead_id': lead.id,
                'texto': texto,
                'contexto': contexto,
                'enviado': fields.Datetime.to_string(
                    lead.visar_wa_followup_sent_at),
            }
        return None
