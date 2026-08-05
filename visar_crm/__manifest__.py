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
agent_track_lead. El avance a etapas posteriores lo hace Odoo por eventos reales:

- Servicio programado: al confirmarse (state 'sale') una orden con lineas de
  servicio Visar; fan-out por grupo (combo -> varios leads).
- Cerrado (won): al cerrarse la tarea FSM (project.task.state == '1_done').
- Valoracion agendada / Cotizacion enviada: botones de staff en el lead.
- Caducidad (lost): ir.cron diario con ventanas por etapa (ir.config_parameter).

Ver .context/31-whatsapp-crm-lead-mapping.md (diseno) y
.context/32-whatsapp-crm-lead-implementation.md (plan).
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Sales/CRM',
    'version': '19.0.1.1.0',
    'license': 'LGPL-3',
    # crm: pipeline y crm.lead. visar_appointment: la normalizacion canonica de
    # telefono (res.partner._visar_phone_nat10_value), producto->dimension->grupo
    # (visar_base) y project.task.visar_sale_order_id (visar_fsm). Arrastra ambos.
    'depends': ['crm', 'visar_appointment'],
    'data': [
        'data/crm_pipeline_data.xml',
        'data/crm_cron.xml',
        'views/crm_lead_views.xml',
    ],
    'installable': True,
    'application': False,
}
