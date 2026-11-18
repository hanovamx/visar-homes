# -*- coding: utf-8 -*-
"""Descartadas (19-nov-2026): el cron de caducidad cambia de nombre y de oficio.

El registro es noupdate, así que el nombre nuevo no llega con el XML. Los
parámetros `visar.crm.lost_days_*` ya no los lee nadie (estaban vacíos).
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_cron SET cron_name = 'Visar CRM: pasar a Descartadas las oportunidades sin respuesta'
         WHERE id = (SELECT res_id FROM ir_model_data
                      WHERE module = 'visar_crm' AND name = 'visar_crm_expire_cron')
    """)
    cr.execute("DELETE FROM ir_config_parameter WHERE key LIKE 'visar.crm.lost_days_%%'")
