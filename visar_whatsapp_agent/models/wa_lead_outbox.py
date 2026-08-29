# -*- coding: utf-8 -*-
"""Buzón de salida del recontacto de leads.

Mismo transporte que el resto de avisos (`visar.wa.outbox.mixin`, en
`visar_base`): reintentos, caducidad y traza los pone el mixin. Aquí solo está
lo propio de un recontacto — de qué lead cuelga y qué contexto viaja.

**Va a un endpoint distinto** (`/internal/lead-followup`) porque es el único
aviso cuyo TEXTO no lo escribe Odoo. Los demás mandan `fallback_text` y el
runtime lo reenvía; este manda la *foto* de la conversación y el runtime le pide
al modelo que redacte. El `fallback_text` viaja igual, como red: si el modelo no
está disponible, sale el texto genérico en vez de no salir nada.

**La caducidad es corta a propósito.** Un recontacto es perecedero por dos
razones que apuntan al mismo sitio: WhatsApp deja de entregar mensajes libres a
las 24 h del último mensaje del cliente, y un "¿seguimos?" que llega a las tres
de la mañana porque el runtime estuvo caído toda la tarde es peor que ninguno.
Si no salió dentro de la ventana, no sale.
"""
from odoo import api, fields, models

TEMPLATE_KEYS = [
    ('lead_followup', "Recontacto de lead frío"),
]

# Minutos. Cabe holgadamente dentro del horario hábil más corto que la validación
# de `visar.followup.config` permite configurar, y no llega a la noche.
TTL_MINUTES = 3 * 60


class VisarWaLeadMessage(models.Model):
    _name = 'visar.wa.lead.message'
    _inherit = ['visar.wa.outbox.mixin']
    _description = "Recontacto de lead por WhatsApp (buzón de salida)"
    _order = 'create_date desc, id desc'
    _rec_name = 'template_key'

    lead_id = fields.Many2one(
        'crm.lead', string="Lead", ondelete='cascade', index=True,
        help="El lead que se está recontactando. En cascada: sin lead no hay "
             "nada que recontactar ni contexto con el que redactar.")
    partner_id = fields.Many2one(
        'res.partner', string="Cliente", index=True,
        help="Solo para poder buscar por cliente; el envío usa `phone`.")

    # ------------------------------------------------------------------
    # Contrato del mixin
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_keys(self):
        return TEMPLATE_KEYS

    @api.model
    def _visar_wa_ttl_minutes(self, template_key):
        return TTL_MINUTES

    @api.model
    def _visar_wa_cron_xmlid(self):
        return 'visar_whatsapp_agent.visar_wa_lead_outbox_cron'

    @api.model
    def _visar_wa_endpoint(self):
        return '/internal/lead-followup'

    def _visar_wa_chatter(self):
        """El aviso fallido se anota en el lead: es donde mira el asesor."""
        self.ensure_one()
        return self.lead_id

    def _visar_wa_context(self):
        """La foto de la conversación, que es con lo que el modelo redacta."""
        self.ensure_one()
        return {
            'lead_id': self.lead_id.id or False,
            'contexto': self.lead_id._visar_wa_followup_data(),
        }

    # ------------------------------------------------------------------
    # Vuelta atrás
    # ------------------------------------------------------------------

    def _visar_attempt_send(self):
        """Al entregarse, el lead pasa a 'Enviado'. Antes no.

        El cron de leads lo deja en 'En cola': marcarlo enviado al encolar sería
        adelantarse a un POST que todavía puede fallar cinco veces.
        """
        enviado = super()._visar_attempt_send()
        if enviado and self.lead_id:
            self.lead_id.sudo().write({
                'visar_wa_followup_state': 'sent',
                'visar_wa_followup_sent_at': fields.Datetime.now(),
                'visar_wa_followup_due': False,
            })
        return enviado

    def _visar_warn_not_delivered(self, reason):
        """El recontacto no llegó: el lead vuelve a decir la verdad.

        Se engancha aquí y no en `_visar_mark_expired` porque este es el único
        punto por el que pasan **los dos** finales malos —caducidad e intentos
        agotados—. Un lead que se quedara en 'Enviado' con un mensaje que nunca
        salió es la peor de las dos mentiras posibles: el asesor lo leería como
        "el agente ya insistió" y no insistiría él.
        """
        resultado = super()._visar_warn_not_delivered(reason)
        if self.lead_id:
            self.lead_id.sudo().write({
                'visar_wa_followup_state': 'skipped',
                'visar_wa_followup_sent_at': False,
                'visar_wa_followup_skip_reason': "No se pudo entregar: %s" % reason,
            })
        return resultado
