# -*- coding: utf-8 -*-
{
    'name': "Visar - Citas multi-servicio",
    'summary': "Wizard multi-servicio Visar: una cita con varios productos, "
               "agenda multi-técnico y precios del tabulador.",
    'description': """
Visar Appointment (D-03 / D-04 / D-05 / D-06 / D-07)
=============================================
Wizard multi-paso en el sitio web que resuelve variantes del tabulador,
filtra técnicos por zona, agenda una sola cita con varios recursos y
genera una orden de venta multi-línea con combo, valoración, add-ons
obligatorios y pricelist por zona.

Al confirmar el pago se generan automáticamente servicios externos (FSM)
agrupados por proyecto (Fumigación / Mantenimiento Áreas Verdes / Valoraciones),
con técnico asignado y fechas planeadas del slot de la cita.

Configuración desde backend: Grupos de servicio, Tabulador, Zonas, Reglas de combo,
add-ons opcionales (optional_product_ids + Obligatorio / Cantidad), proyectos FSM.
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Services/Appointment',
    'version': '19.0.2.0.16',
    'license': 'LGPL-3',
    'depends': [
        'visar_base',
        'visar_fsm',
        'website_appointment',
        'website_appointment_sale',
        'website_sale',
        'hr',
        'worksheet',
    ],
    'data': [
        'data/visar_questions_data.xml',
        'views/appointment_type_views.xml',
        'views/appointment_resource_views.xml',
        'views/calendar_event_views.xml',
        'views/product_template_views.xml',
        'views/wizard_templates.xml',
        'views/appointment_templates_appointments.xml',
        'views/valoracion_templates.xml',
    ],
    'installable': True,
    'application': False,
}
