# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Guión de calificación (D-08): desliga también las preguntas nuevas
    # (Motivo, Plagas a tratar, Motivo de valoración) de todos los tipos de
    # cita para que no aparezcan en el formulario web (se responden por código).
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['appointment.type']._visar_unlink_questions_from_entry_types()
