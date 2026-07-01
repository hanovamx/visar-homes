# -*- coding: utf-8 -*-
{
    'name': "Visar - Base",
    'summary': "Catálogos Visar: zonas, grupos, tabulador, combos y add-ons.",
    'description': """
Catálogos y configuración del negocio VISAR:
- Zonas geográficas y listas de precios
- Grupos de servicio, dimensiones y tramos (tabulador)
- Reglas de combo y add-ons obligatorios en productos
- Extensión de pedidos de venta para add-ons automáticos
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Services/Appointment',
    'version': '19.0.1.1.0',
    'license': 'LGPL-3',
    'depends': [
        'sale',
        'product',
        'appointment',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/visar_tabulador_data.xml',
        'data/visar_zone_cp_data.xml',
        'views/visar_zone_views.xml',
        'views/visar_zone_cp_views.xml',
        'views/visar_service_group_views.xml',
        'views/visar_service_tier_views.xml',
        'views/visar_combo_rule_views.xml',
        'views/product_template_views.xml',
        'views/product_product_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
}
