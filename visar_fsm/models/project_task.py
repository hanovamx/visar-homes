# -*- coding: utf-8 -*-
from odoo import api, fields, models


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

    # Líneas de la orden atendidas por esta tarea. Campo TÉCNICO: es la dependencia
    # del cómputo de `visar_service_group_ids` (el core no trae el inverso de
    # `sale.order.line.task_id`). NO se muestra en la vista: la decisión de
    # 2026-06-26 descartó la pestaña one2many, no el campo.
    visar_sale_line_ids = fields.One2many(
        'sale.order.line', 'task_id', string="Líneas de la orden")

    # Etiqueta de servicio: qué grupos de servicio Visar cubre esta tarea.
    # Es lo que permite consolidar el combo en UN servicio externo sin perder el
    # conteo por línea de negocio: una tarea combo lleva los dos grupos y aparece
    # bajo cada uno al agrupar (igual que las etiquetas nativas). Sustituye a
    # `project_id` como eje de conteo por servicio, que dejó de ser fiel el día que
    # una tarea puede cubrir varios.
    visar_service_group_ids = fields.Many2many(
        'visar.service.group',
        'visar_task_service_group_rel', 'task_id', 'group_id',
        string="Servicios",
        compute='_compute_visar_service_group_ids', store=True,
        help="Grupos de servicio Visar que cubre este servicio externo. Un combo "
             "cubre varios y cuenta en cada uno.")

    @api.depends('visar_sale_line_ids.product_id', 'visar_sale_line_ids.order_id',
                 'sale_order_id')
    def _compute_visar_service_group_ids(self):
        """Grupo(s) de servicio derivados de las líneas atendidas por la tarea.

        Delega en `product.template._visar_service_groups()` (enlace autoritativo
        dimensión -> producto), el mismo primitivo que usa el fan-out de CRM. A
        diferencia de `crm.lead._visar_order_service_groups`, NO filtra por
        `visar_is_service`: el helper ya devuelve vacío para un producto sin
        dimensión (los add-ons no aportan grupo por su cuenta), y la versión laxa es
        la que servirá también para las visitas de póliza, cuyos productos llevan
        `visar_generates_visit` pero muchas veces no `visar_is_service`.

        Solo cuentan las líneas del pedido PROPIO de la tarea. Otras órdenes pueden
        apuntar aquí por `task_id` —hoy el pedido de adicionales que levanta el
        técnico en campo (`visar_field_app`), mañana cualquier venta que se cuelgue
        del servicio— y este campo es el eje de conteo por línea de negocio: lo que
        mide es qué se AGENDÓ, no qué se vendió de paso. Sin el filtro, vender un
        corte de pasto durante una fumigación haría que la visita contara también
        como servicio de áreas verdes.
        """
        for task in self:
            lines = task.visar_sale_line_ids
            if task.sale_order_id:
                lines = lines.filtered(lambda l: l.order_id == task.sale_order_id)
            templates = lines.mapped('product_id.product_tmpl_id')
            task.visar_service_group_ids = (
                templates._visar_service_groups() if templates else False)

    def _visar_rename_from_services(self):
        """Renombra una tarea CONSOLIDADA con todos los servicios que cubre.

        El nombre nativo lo arma `_timesheet_create_task_prepare_values` con el
        producto de la línea representante, así que una tarea que junta fumigación y
        áreas verdes se llamaría solo "Fumigación…". Se conserva el prefijo de la
        orden (mismo formato que el core: "S00123 - <servicios>").
        """
        for task in self:
            groups = task.visar_service_group_ids
            if len(groups) < 2:
                continue
            label = ' + '.join(groups.mapped('name'))
            order_name = task.sale_order_id.name or ''
            task.name = '%s - %s' % (order_name, label) if order_name else label
