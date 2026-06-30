# -*- coding: utf-8 -*-
from odoo.addons.visar_fsm.hooks import _visar_setup_fsm_projects


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    _visar_setup_fsm_projects(env)
