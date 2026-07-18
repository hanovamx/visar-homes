# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.7.0, deja lista la etapa "Pendiente de firma" (activa,
entre En ejecución y Completado, con xmlid propio). Idempotente.

`post_init_hook` solo corre en instalación limpia; esta migración cubre el caso de
una BD donde el módulo YA está instalado (producción) — que es justo donde la etapa
existe a mano y archivada."""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_signature_stage


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_signature_stage(env)
