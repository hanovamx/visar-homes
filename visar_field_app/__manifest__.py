# -*- coding: utf-8 -*-
{
    'name': "Visar - App de Campo (Técnicos)",
    'summary': "App web tipo POS para técnicos: identificación por PIN, sin usuario interno.",
    'description': """
App de campo para técnicos de Visar (patrón POS / pos_hr).

- Los técnicos NO necesitan usuario interno de Odoo (no consumen licencia).
- El dispositivo se identifica una sola vez; cada técnico abre su "sesión de trabajo"
  (turno) con un PIN (modelo `visar.field.session`).
- Cada técnico ve solo SUS servicios externos (tareas FSM) del día, filtrados por
  `project.task.visar_technician_ids` (empleados, no usuarios).
- Captura en campo escrita directamente sobre la tarea nativa: fotos (adjuntos),
  firma del cliente, notas y cierre del servicio.
- Atribución por técnico (`visar_field_closed_by_id`) para comisiones y auditoría.

Construido con portal/website + QWeb + controladores (mismo patrón que el wizard de
citas), sin tocar el frontend OWL nativo.
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Services/Field Service',
    'version': '19.0.1.0.1',
    'license': 'LGPL-3',
    'depends': [
        'visar_fsm',
        'website',
        'industry_fsm_report',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/field_app_templates.xml',
        'views/hr_employee_views.xml',
        'views/project_task_views.xml',
        'views/field_session_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'visar_field_app/static/src/css/field_app.css',
            'visar_field_app/static/src/js/field_app.js',
        ],
    },
    'installable': True,
    'application': True,
}
