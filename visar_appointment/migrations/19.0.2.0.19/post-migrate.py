# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # El paso de zona fue reemplazado por la dirección de entrega (la zona se
    # deriva del código postal). Quita del formulario nativo tanto las preguntas
    # Visar como la pregunta manual de dirección (Dirección/Domicilio/Address),
    # que ahora se captura de forma estructurada en el wizard.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['appointment.type']._visar_unlink_questions_from_entry_types()
