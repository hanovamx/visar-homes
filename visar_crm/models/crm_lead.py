# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # Grupo de servicio que acota el lead (Fumigacion vs Areas Verdes). Es, junto
    # con el telefono, la clave de deduplicacion: un cliente de fumigacion que
    # pregunta por jardineria abre un lead NUEVO en Areas Verdes. Ver
    # .context/31-whatsapp-crm-lead-mapping.md seccion 4.
    visar_service_group_id = fields.Many2one(
        'visar.service.group',
        string="Grupo de servicio (Visar)",
        index=True,
        help="Grupo de servicio que acota este lead. Clave de dedupe junto con "
             "el telefono normalizado.",
    )

    # Telefono normalizado a los ultimos 10 digitos (numero nacional MX). Misma
    # normalizacion que el resto del agente (_agent_normalize_phone ->
    # res.partner._visar_phone_nat10_value), asi "mismo numero" significa lo mismo
    # para el lead, el partner y el dedupe de reservas. Indexado: agent_track_lead
    # busca por igualdad en cada cotizacion.
    visar_wa_phone_norm = fields.Char(
        string="Telefono WhatsApp (nat. 10)",
        index=True,
        copy=False,
        help="Ultimos 10 digitos del telefono; clave de dedupe del pipeline WhatsApp.",
    )

    # Origen del lead. Hoy solo 'whatsapp'; Selection para poder crecer sin migrar.
    visar_source = fields.Selection(
        selection=[('whatsapp', "WhatsApp")],
        string="Origen (Visar)",
        copy=False,
    )

    def _visar_advance_stage(self, target_stage):
        """Mueve el lead a `target_stage` solo si es un AVANCE (forward-only).

        Compara por `sequence` de la etapa: nunca regresa a una etapa anterior
        (un cliente que re-cotiza no vuelve de 'Agendando' a 'Cotizado'; un evento
        tardio no deshace un avance). Devuelve True si hubo cambio.

        Lo usan agent_track_lead (crear en Nuevo) y las automatizaciones de avance
        (valoracion / servicio programado). Ver .context/32-...-implementation.md.
        """
        self.ensure_one()
        current = self.stage_id
        if current and target_stage.sequence <= current.sequence:
            return False
        self.stage_id = target_stage.id
        return True
