# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # Orden de venta completa que originó este servicio externo.
    # Related sobre el nativo sale_order_id (que el core computa desde sale_line_id).
    # Reemplaza en la UI al campo nativo `sale_line_id`, que se conserva OCULTO y se
    # sigue asignando en _visar_create_grouped_tasks (no eliminar esa asignación).
    visar_sale_order_id = fields.Many2one(
        'sale.order',
        string="Orden de venta",
        related='sale_order_id',
        store=True,
        readonly=True,
        help="Orden de venta completa de la que proviene este servicio externo "
             "(incluye las dos podas, fumigaciones y add-ons de la misma cita).")

    # Técnicos asignados como EMPLEADOS (no usuarios). Es la asignación real del
    # servicio externo: se puebla desde la cita (recurso → empleado) en
    # _visar_enrich_fsm_tasks y es el campo por el que agrupa el Gantt de técnicos.
    # Sustituye al nativo user_ids, que quedaba vacío porque los técnicos de campo
    # no tienen usuario interno de Odoo.
    visar_technician_ids = fields.Many2many(
        'hr.employee',
        'visar_task_technician_rel', 'task_id', 'employee_id',
        string="Técnicos asignados",
        help="Empleados (técnicos de campo) asignados a este servicio externo. "
             "No requieren usuario interno de Odoo.")
