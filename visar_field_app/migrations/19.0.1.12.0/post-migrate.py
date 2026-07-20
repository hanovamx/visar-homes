# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.12.0, retira la etapa "Pendiente de firma" del flujo:

- mueve tareas que aún la tengan → En ejecución
- archiva la etapa

La firma sigue gated por `visar_worksheet_saved_at` (Req 6). Idempotente.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import archive_signature_stage


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    archive_signature_stage(env)
