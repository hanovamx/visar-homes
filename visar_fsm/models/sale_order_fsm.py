# -*- coding: utf-8 -*-
from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _timesheet_service_generation(self):
        """Group Visar service lines by FSM project → one task per project.

        Lines that already have task_id are skipped by super(), so we pre-assign
        task_id to all Visar service lines within a project group before calling
        the native generation, which handles any remaining non-Visar lines.
        """
        visar_service_lines = self.filtered(
            lambda sol: sol.product_id.visar_is_service
            and self._visar_line_project(sol)
            and not sol.task_id
        )

        if visar_service_lines:
            task_by_project = self._visar_create_grouped_tasks(visar_service_lines)
            self._visar_assign_addon_tasks(visar_service_lines, task_by_project)
            for order in self.mapped('order_id'):
                order._visar_enrich_fsm_tasks(list(task_by_project.values()))

        return super()._timesheet_service_generation()

    @staticmethod
    def _visar_line_project(line):
        """Proyecto FSM configurado en el producto de la línea.

        `product.template.project_id` es `company_dependent`, así que se lee con la
        compañía de la línea — igual que el generador nativo
        (`sale_project/models/sale_order_line.py`). Sin esto, en una BD multi-compañía
        se leería el valor de la compañía del usuario que confirma."""
        return line.product_id.with_company(line.company_id).project_id

    def _visar_effective_project_map(self, visar_service_lines):
        """{line.id: proyecto EFECTIVO} — el propio, o el combinado si aplica.

        Un proyecto declara con qué otros comparte visita apuntando al mismo
        `visar_fsm_combined_project_id` (ver `models/project_project.py`). La
        consolidación solo se activa cuando la cita trae trabajo de **dos o más**
        proyectos distintos que apuntan a ese mismo combinado: una fumigación sola
        no debe caer en "Servicios combinados" (recibiría la hoja combinada y se le
        exigiría captura de áreas verdes que nadie contrató).

        Es configuración pura: ningún nombre ni id de proyecto vive en el código.
        """
        projects_by_combined = {}
        for line in visar_service_lines:
            project = self._visar_line_project(line)
            combined = project.visar_fsm_combined_project_id
            # Un combinado archivado, que dejó de ser FSM o de otra compañía no puede
            # recibir tareas: mejor dos servicios externos que uno roto.
            if (not combined or not combined.active or not combined.is_fsm
                    or (combined.company_id
                        and combined.company_id != project.company_id)):
                continue
            projects_by_combined.setdefault(combined, set()).add(project.id)

        active_combined = {
            combined for combined, source_ids in projects_by_combined.items()
            if len(source_ids) > 1
        }

        effective = {}
        for line in visar_service_lines:
            project = self._visar_line_project(line)
            combined = project.visar_fsm_combined_project_id
            effective[line.id] = (
                combined if combined in active_combined else project)
        return effective

    def _visar_create_grouped_tasks(self, visar_service_lines):
        """Create one FSM task per effective project group; returns {project_id: task}.

        "Efectivo" = el proyecto propio del producto, salvo que la cita active una
        regla de consolidación (ver `_visar_effective_project_map`), en cuyo caso
        todas las líneas combinables caen en una sola tarea.
        """
        effective = self._visar_effective_project_map(visar_service_lines)
        groups = {}
        for line in visar_service_lines.sorted(lambda l: (l.sequence, l.id)):
            pid = effective[line.id].id
            groups.setdefault(pid, self.env['sale.order.line'])
            groups[pid] |= line

        task_by_project = {}
        for project_id, lines in groups.items():
            project = self.env['project.project'].browse(project_id)
            rep_line = lines.sorted(lambda l: (l.sequence, l.id))[0]
            task = rep_line._timesheet_create_task(project)
            remaining = lines - rep_line
            if remaining:
                remaining.write({'task_id': task.id})
            # Tarea CONSOLIDADA (líneas de dos proyectos distintos). El nombre
            # nativo sale del producto de la línea REPRESENTANTE ("S00123 -
            # Fumigación interior o exterior"), que aquí nombra solo una parte del
            # trabajo — y es lo que el técnico lee en su tarjeta. Se reescribe con
            # todos los servicios. Las tareas de un solo proyecto (p. ej. dos podas
            # de la misma cita) conservan el nombre nativo de siempre.
            sources = {self._visar_line_project(line).id for line in lines}
            if len(sources) > 1:
                task._visar_rename_from_services()
            task_by_project[project_id] = task
        return task_by_project

    def _visar_assign_addon_tasks(self, visar_service_lines, task_by_project):
        """Assign task_id to add-on lines so they appear as materials on the FSM task."""
        if not task_by_project:
            return
        primary_task = next(iter(task_by_project.values()))

        addon_lines = self.filtered(
            lambda sol: not sol.product_id.visar_is_service
            and not sol.task_id
            and not sol.display_type
            and bool(sol.product_id)
        )
        for addon_line in addon_lines:
            task = self._visar_resolve_addon_task(
                addon_line, visar_service_lines, task_by_project
            ) or primary_task
            if task:
                addon_line.sudo().write({'task_id': task.id})

    def _visar_resolve_addon_task(self, addon_line, visar_service_lines, task_by_project):
        """Return the task whose service product declares the add-on as an optional line.

        `task_by_project` está indexado por el proyecto EFECTIVO (el combinado si la
        cita activó la consolidación), así que el add-on se resuelve por el mismo
        mapa que usó el agrupado — si se buscara por `product_id.project_id` a secas,
        el add-on de una línea consolidada no encontraría tarea y caería al
        `primary_task` por accidente.
        """
        addon_tmpl = addon_line.product_id.product_tmpl_id
        effective = self._visar_effective_project_map(visar_service_lines)
        for service_line in visar_service_lines:
            optional_tmpls = service_line.product_id.product_tmpl_id.visar_optional_line_ids.mapped(
                'optional_product_id'
            )
            if addon_tmpl in optional_tmpls:
                return task_by_project.get(effective[service_line.id].id)
        return None


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _visar_enrich_fsm_tasks(self, tasks):
        """Copy technicians and planned dates from the booking's calendar event to FSM tasks."""
        self.ensure_one()
        if not tasks:
            return

        events = self.order_line.mapped('calendar_event_id').filtered(lambda e: e.id)
        if not events:
            return
        event = events[0]

        date_vals = {}
        if event.start:
            date_vals['planned_date_begin'] = event.start
        if event.stop:
            date_vals['date_deadline'] = event.stop

        employees = (
            event.appointment_resource_ids
            .mapped('visar_employee_id')
            .filtered(lambda e: e.id)
        )
        # Asignación nativa por usuario (solo aplica a técnicos que tengan usuario).
        user_ids = employees.mapped('user_id').filtered(lambda u: u.id).ids

        for task in tasks:
            vals = dict(date_vals)
            # Asignación real por empleado (técnicos de campo sin usuario interno).
            if employees:
                vals['visar_technician_ids'] = [(6, 0, employees.ids)]
            if user_ids:
                vals['user_ids'] = [(6, 0, user_ids)]
            if vals:
                task.sudo().write(vals)
