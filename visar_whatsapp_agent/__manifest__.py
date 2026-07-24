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

Ningun metodo acepta nombres de modelo, dominios ni SQL: el agente solo
puede pedir estas tres cosas con parametros tipados. Los metodos corren
como el usuario que llama (sin sudo), asi que las ACLs del grupo
"Agente WhatsApp / Solo lectura" son el limite real.

Fase 1: solo lectura. No agenda citas.
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Services/Appointment',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    # Depende de visar_appointment porque reutiliza su motor de precios
    # (_visar_quote_booking) para que el agente y el wizard web den el mismo
    # total: variante combinada de fumigacion, descuentos de combo y add-ons.
    'depends': ['visar_appointment'],
    'data': [
        'security/visar_whatsapp_agent_groups.xml',
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': False,
}
