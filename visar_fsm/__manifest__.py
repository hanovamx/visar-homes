# -*- coding: utf-8 -*-
{
    'name': "Visar - FSM",
    'summary': "Generación de tareas FSM agrupadas por proyecto al confirmar pedidos Visar.",
    'description': """
Integración con Field Service Management para VISAR:
- Agrupa líneas de servicio en una tarea FSM por proyecto
- Asigna add-ons como materiales en la tarea correspondiente
- Copia técnicos y fechas planeadas desde la cita al confirmar el pago
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Services/Field Service',
    'version': '19.0.1.0.4',
    'license': 'LGPL-3',
    'depends': [
        'visar_base',
        'appointment',
        'hr',
        'industry_fsm',
        'industry_fsm_sale',
    ],
    'data': [
        'views/appointment_resource_views.xml',
        'views/calendar_event_views.xml',
        'views/project_task_views.xml',
        'views/fsm_technician_planning.xml',
    ],
    'installable': True,
    'application': False,
    'post_init_hook': 'post_init_hook',
}
