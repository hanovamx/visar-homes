# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Quita las preguntas de calificación (plaga/roedores/tipo) del formulario nativo;
    # se capturan en el wizard y se muestran en Questions & Answers.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['appointment.type']._visar_unlink_questions_from_entry_types()
