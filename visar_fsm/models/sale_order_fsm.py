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
            and sol.product_id.project_id
            and not sol.task_id
        )

        if visar_service_lines:
            task_by_project = self._visar_create_grouped_tasks(visar_service_lines)
            self._visar_assign_addon_tasks(visar_service_lines, task_by_project)
            for order in self.mapped('order_id'):
                order._visar_enrich_fsm_tasks(list(task_by_project.values()))

        return super()._timesheet_service_generation()

    def _visar_create_grouped_tasks(self, visar_service_lines):
        """Create one FSM task per project group; returns {project_id: task}."""
        groups = {}
        for line in visar_service_lines.sorted(lambda l: (l.sequence, l.id)):
            pid = line.product_id.project_id.id
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
        """Return the task whose service product declares the add-on as an optional line."""
        addon_tmpl = addon_line.product_id.product_tmpl_id
        for service_line in visar_service_lines:
            optional_tmpls = service_line.product_id.product_tmpl_id.visar_optional_line_ids.mapped(
                'optional_product_id'
            )
            if addon_tmpl in optional_tmpls:
                project_id = service_line.product_id.project_id.id
                return task_by_project.get(project_id)
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
