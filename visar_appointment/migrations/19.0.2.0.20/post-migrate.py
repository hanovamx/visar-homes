# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Limpieza reforzada: quita las preguntas Visar (Zona, Metros, Dirección,
    # calificación) de TODOS los tipos de cita que las referencien, no solo del
    # maestro/valoración. Resuelve conflictos con preguntas configuradas a mano
    # que bloqueaban el formulario web.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['appointment.type']._visar_unlink_questions_from_entry_types()
