# -*- coding: utf-8 -*-
{
    'name': "Visar - Agente WhatsApp (API)",
    'summary': "API de solo lectura para el agente de IA por WhatsApp.",
    'description': """
Visar WhatsApp Agent
====================
Superficie RPC acotada que consume el servicio externo `visar_fastapi`
(FastAPI + LLM). Expone tres metodos de solo lectura sobre el modelo
abstracto `visar.agent.tools`:

- agent_catalog_snapshot(): grupos, dimensiones, tramos y zonas.
- agent_resolve_zone(cp): codigo postal -> zona y cobertura.
- agent_quote_service(payload): (dimension, CP, m2) -> tramo y precio.
- agent_customer_services(payload): telefono -> servicios del cliente (lectura).
- agent_track_lead(payload): registra la interaccion como lead de CRM en la
  etapa 'Nuevo' (ESCRITURA acotada; unico metodo que escribe, con sudo() solo
  sobre crm.lead). El pipeline/etapas viven en el modulo visar_crm; el avance a
  etapas posteriores lo hace Odoo, no el runtime.

Salvo agent_track_lead, ningun metodo escribe ni acepta nombres de modelo,
dominios o SQL: el agente solo puede pedir cosas concretas con parametros
tipados. Las lecturas corren como el usuario que llama (sin sudo), asi que las
ACLs del grupo "Agente WhatsApp / Solo lectura" son el limite real.

Fase 1: solo lectura + creacion de leads en 'Nuevo'.
Ver .context/31-whatsapp-crm-lead-mapping.md y 32-...-implementation.md.
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Services/Appointment',
    'version': '19.0.1.2.0',
    'license': 'LGPL-3',
    # visar_appointment: motor de precios (_visar_quote_booking). visar_crm:
    # pipeline/etapas y campos de crm.lead que llena agent_track_lead.
    'depends': ['visar_appointment', 'visar_crm'],
    'data': [
        'security/visar_whatsapp_agent_groups.xml',
        'security/ir.model.access.csv',
        'views/visar_agent_config_views.xml',
    ],
    'installable': True,
    'application': False,
}
