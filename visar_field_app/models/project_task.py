# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # Nota: `visar_technician_ids` (técnicos asignados) ahora vive en visar_fsm,
    # porque la asignación es responsabilidad de FSM y la usa el Gantt de técnicos.
    # Esta app solo lo consume (lista de servicios del técnico).

    # --- Atribución del cierre en campo ---
    # La captura (worksheet, firma) usa los modelos/campos NATIVOS para que los
    # reportes nativos funcionen. Aquí solo guardamos QUIÉN cerró (empleado), que
    # el flujo nativo no registra y Visar necesita para comisiones de upsell.
    visar_field_closed_by_id = fields.Many2one(
        'hr.employee', string="Cerrado por (técnico)", readonly=True,
        help="Técnico que cerró el servicio desde la app de campo. "
             "Base para comisiones de upsell y auditoría.")
    visar_field_closed_at = fields.Datetime(
        string="Cerrado en campo", readonly=True)
