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
        time_map = {}
        for task in values.get('docs', []):
            sections = task._visar_worksheet_report_sections()
            if sections:
                visar_map[task.id] = sections
            # Req 8: bloque "Tiempo en sitio" (llegada → última guarda de la hoja).
            onsite = task._visar_onsite_report()
            if onsite:
                time_map[task.id] = onsite
        values['visar_worksheet_map'] = visar_map
        values['visar_time_map'] = time_map
        return values
