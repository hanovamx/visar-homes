# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.1.0, siembra la consolidación de servicios combinados.

Crea (o encuentra) el proyecto FSM **Servicios combinados** y apunta hacia él los
proyectos de servicio existentes (Fumigación / Mantenimiento Áreas Verdes) con
`visar_fsm_combined_project_id`. A partir de ahí, una cita que traiga trabajo de
ambos genera UNA sola tarea en ese proyecto.

`post_init_hook` solo corre en instalación limpia; esta migración cubre la BD donde
el módulo YA está instalado. Idempotente: `_visar_setup_fsm_projects` busca-o-crea
los proyectos y solo escribe el puntero donde está vacío (si negocio lo limpió a
mano para dejar de combinar un servicio, re-ejecutar no se lo revive).

Las tareas combo que YA existan se quedan como están (dos tareas separadas): la
consolidación aplica a pedidos nuevos. Mezclar hojas de trabajo ya capturadas no
tiene una respuesta limpia y los datos actuales son de prueba.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_fsm.hooks import _visar_setup_fsm_projects


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _visar_setup_fsm_projects(env)
