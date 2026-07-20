# -*- coding: utf-8 -*-
from odoo import api, models


class ReportFsmWorksheetCustom(models.AbstractModel):
    """Alimenta el reporte nativo de Field Service con una lectura LIMPIA de la
    worksheet (secciones por pestaña + valores formateados), que la plantilla
    Visar dibuja en lugar de la vista autogenerada. Si una tarea no produce
    secciones legibles, `visar_worksheet_map` no la incluye y la plantilla cae al
    render nativo (fallback), así que ningún reporte queda peor que antes."""
    _inherit = 'report.industry_fsm.worksheet_custom'

    @api.model
    def _get_report_values(self, docids, data=None):
        values = super()._get_report_values(docids, data)
        visar_map = {}
        tech_map = {}
        for task in values.get('docs', []):
            sections = task._visar_worksheet_report_sections()
            if sections:
                visar_map[task.id] = sections
            # Bloque "Técnico que realizó el servicio": el nativo tira de `user_ids`
            # (vacío en técnicos de campo, sin usuario), así que Visar lo alimenta
            # desde los técnicos ASIGNADOS como empleados (nombre + teléfono).
            techs = task._visar_report_technicians()
            if techs:
                tech_map[task.id] = techs
        values['visar_worksheet_map'] = visar_map
        values['visar_tech_map'] = tech_map
        return values
