# -*- coding: utf-8 -*-
{
    'name': "Visar - CRM (leads de WhatsApp)",
    'summary': "Pipeline de CRM para los leads que genera el agente de WhatsApp.",
    'description': """
Visar CRM
=========
Extiende el CRM **nativo** de Odoo (no lo reemplaza) con lo que Visar necesita
para convertir interacciones del agente de WhatsApp en leads:

- Un pipeline dedicado (crm.team "WhatsApp") con 5 etapas sembradas:
  Nuevo -> Visita de valoracion agendada -> Cotizacion enviada ->
  Servicio programado -> Cerrado (won).
- Campos en crm.lead para dedupe e identidad: grupo de servicio, telefono
  normalizado y origen.
- Helper de avance de etapa *forward-only* (la etapa solo sube).

El agente (visar_whatsapp_agent) SOLO crea leads en 'Nuevo' via el metodo RPC
agent_track_lead. El avance a etapas posteriores lo hace Odoo por eventos reales
(pago, valoracion, tarea FSM terminada). Ver
.context/31-whatsapp-crm-lead-mapping.md (diseno) y
.context/32-whatsapp-crm-lead-implementation.md (plan).
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Sales/CRM',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    # crm: el pipeline y crm.lead. visar_base: visar.service.group (grupo del lead).
    'depends': ['crm', 'visar_base'],
    'data': [
        'data/crm_pipeline_data.xml',
    ],
    'installable': True,
    'application': False,
}
