# -*- coding: utf-8 -*-
from odoo import api, models


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
        sin_proyecto = self.mapped('order_id').filtered(lambda o: not o.project_id)

        if visar_service_lines:
            # `visar_sin_aviso_ruta`: estas tareas nacen de una reserva, y ese
            # camino YA paso por el filtro de traslados al listar el horario
            # (`visar_appointment/models/project_task_travel.py`). Revisarlas otra
            # vez seria pagar una llamada a Mapbox dentro del cobro para
            # confirmar lo que ya se sabe.
            callado = self.with_context(visar_sin_aviso_ruta=True)
            task_by_project = callado._visar_create_grouped_tasks(visar_service_lines)
            callado._visar_assign_addon_tasks(visar_service_lines, task_by_project)
            for order in callado.mapped('order_id'):
                order._visar_enrich_fsm_tasks(list(task_by_project.values()))

        res = super()._timesheet_service_generation()
        sin_proyecto._visar_fix_auto_project()
        return res

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

        La regla (qué proyectos comparten visita, y los guardias sobre un combinado
        inutilizable) vive en `project.project._visar_effective_projects` porque la
        comparten las visitas de póliza, que llegan al proyecto por otro campo. Aquí
        solo se traduce línea -> proyecto.
        """
        line_project = {
            line.id: self._visar_line_project(line) for line in visar_service_lines}
        projects = self.env['project.project'].browse(
            sorted({project.id for project in line_project.values()}))
        effective = projects._visar_effective_projects(projects)
        return {line_id: effective[project.id]
                for line_id, project in line_project.items()}

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

    # ------------------------------------------------------------------
    # El botón "Proyectos" del pedido: dónde está el trabajo DE VERDAD
    # ------------------------------------------------------------------
    #
    # El botón es nativo (`sale_project`) y arma su lista con el proyecto
    # CONFIGURADO en cada producto del pedido. Nunca mira dónde cayó la tarea. Con
    # el combo eso miente dos veces: enseña "Fumigación" y "Mantenimiento Áreas
    # Verdes", que de esta venta no tienen nada, y calla "Servicios combinados",
    # que es donde está el servicio externo. Quien entra desde el pedido a buscar
    # el trabajo no lo encuentra.
    #
    # Se corrige el CÓMPUTO y no se rellena `sale.order.project_id` (la propuesta
    # original): rellenarlo añadiría el combinado pero dejaría los dos vacíos a la
    # vista —Odoo los suma desde los productos igual—, y ese campo además decide a
    # dónde van las tareas de los productos "tarea en el proyecto del pedido". El
    # campo no se guarda, así que los pedidos ya confirmados salen bien sin migrar.

    def _visar_fix_auto_project(self):
        """El "proyecto del pedido" que Odoo acaba de poner solo, llevado al real.

        Al confirmar, el generador nativo rellena `project_id` del pedido con el
        proyecto del PRODUCTO de la primera línea (`sale_project`, bloque
        "task_global_project: if not set…"). En un combo ese es "Fumigación", donde
        no quedó nada: la tarea está en el combinado. Se corrige a donde está el
        trabajo de esa misma línea.

        Solo se llama para pedidos que NO tenían proyecto antes de confirmar: un
        proyecto puesto a mano no se toca.
        """
        Line = self.env['sale.order.line']
        for order in self:
            auto = order.project_id
            if not auto:
                continue
            work = order._visar_work_projects()
            real_ids = {pid for ps in work.values() for pid in ps.ids}
            if not work or auto.id in real_ids:
                continue
            for line in order.order_line.browse(list(work)).sorted(
                    lambda l: (l.sequence, l.id)):
                if Line._visar_line_project(line) == auto and work[line.id]:
                    order.project_id = work[line.id][:1]
                    break

    def _visar_work_projects(self):
        """{id de línea: proyectos donde está (o estará) el trabajo de esa línea}.

        Solo líneas de servicio Visar con proyecto configurado. Con tarea, manda la
        tarea: es un hecho, y sigue siendo verdad aunque después cambie la
        configuración de combinados. Sin tarea (cotización) se anticipa con la
        MISMA regla que usará la confirmación, para que el botón no diga una cosa
        antes de confirmar y otra después.

        `visar_subscription` lo extiende: las visitas de una póliza no cuelgan de
        `task_id` sino de la póliza.
        """
        self.ensure_one()
        Line = self.env['sale.order.line']
        lines = self.order_line.filtered(
            lambda sol: not sol.display_type and sol.product_id.visar_is_service
            and Line._visar_line_project(sol))
        if not lines:
            return {}
        Project = self.env['project.project']
        work = {}
        sin_tarea = Line
        for line in lines:
            project = line.sudo().task_id.project_id
            if project:
                work[line.id] = Project.browse(project.id)
            else:
                sin_tarea |= line
        if sin_tarea:
            for line_id, project in Line._visar_effective_project_map(sin_tarea).items():
                work[line_id] = Project.browse(project.id)
        return work

    @api.depends('order_line.task_id.project_id')
    def _compute_project_ids(self):
        super()._compute_project_ids()
        Line = self.env['sale.order.line']
        Project = self.env['project.project']
        for order in self:
            work = order._visar_work_projects()
            if not work:
                continue
            lines = order.order_line.browse(list(work))
            real = Project.browse(sorted({pid for ps in work.values() for pid in ps.ids}))
            nominal = Project.browse(sorted(
                {Line._visar_line_project(line).id for line in lines} - {False}))
            # Un proyecto nominal sin trabajo se quita SOLO si no hay otra razón
            # para que esté: que sea el proyecto del pedido, el de alguna línea, el
            # de un servicio que no es Visar, o un proyecto nacido de este pedido.
            otras = order.order_line - lines
            otros = (order.project_id | order.order_line.mapped('project_id')
                     | otras.filtered('is_service').mapped('product_id.project_id'))
            vacios = (nominal - real - otros).sudo().filtered(
                lambda p: p.sale_order_id != order
                and p.reinvoiced_sale_order_id != order)
            projects = ((order.project_ids - Project.browse(vacios.ids)) | real)
            projects = projects._filtered_access('read')
            order.project_ids = projects
            order.project_count = len(projects.filtered('active'))

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
            # El enlace DIRECTO tarea→cita, solo cuando no hay ambigüedad posible
            # (1-oct-2026). Con un único evento en el pedido, `event` ES la cita de
            # esta tarea y dejarlo escrito ahorra deducirlo después; con varios
            # —una reserva multi-servicio, una cita por servicio— cuál le toca a
            # cada tarea se decide por hora de inicio, y eso ya lo hace
            # `_visar_ruta_cita`. Escribir aquí el primero de la lista sería
            # exactamente el error que ese desempate existe para evitar.
            if len(events) == 1 and not task.visar_visit_event_id:
                vals['visar_visit_event_id'] = event.id
            if vals:
                task.sudo().write(vals)
