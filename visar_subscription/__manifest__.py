# Part of VISAR Homes. See LICENSE file for full copyright and licensing details.
{
    'name': 'VISAR - Suscripciones / Pólizas (visitas FSM)',
    'version': '19.0.1.1.0',
    'category': 'Sales/Subscriptions',
    'summary': 'Genera visitas de servicio (FSM) por cada periodo facturado de una póliza',
    'description': """
VISAR - Pólizas de servicio
===========================
Puente entre Suscripciones (sale_subscription) y Field Service (industry_fsm).

Cada vez que una póliza (suscripción) factura un periodo, se genera automáticamente
una visita de servicio (tarea FSM) en el proyecto configurado. También permite crear
visitas de garantía adicionales sin costo, para medir el consumo de garantía y ajustar
el precio en la renovación.
""",
    'author': 'Hanova Consulting',
    'website': 'https://hanova.consulting',
    'license': 'LGPL-3',
    'depends': [
        'visar_base',
        'visar_fsm',
        'sale_subscription',
        'industry_fsm',
    ],
    'data': [
        'data/product_anticipo.xml',
        'views/sale_subscription_plan_views.xml',
        'views/product_template_views.xml',
        'views/sale_order_views.xml',
        'views/project_task_views.xml',
    ],
    'installable': True,
    'application': False,
}
