# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    # Autorización de reagenda por INCIDENCIA. La escribe la app de campo (o el
    # coordinador desde el backend) al pedirle al cliente que elija otro horario;
    # la lee `visar_appointment` para relajar los bloqueos que no aplican cuando
    # la culpa no es del cliente. Vive AQUÍ, en el módulo común, porque quien la
    # escribe (`visar_field_app`) y quien la lee (`visar_appointment`) son módulos
    # HERMANOS: ninguno depende del otro.
    #
    # Es una fecha y no un booleano para que quede el rastro de cuándo se
    # autorizó, y se consume (se borra) en cuanto la cita se mueve: autoriza UN
    # cambio, no una barra libre.
    visar_reschedule_granted_at = fields.Datetime(
        string="Reagenda autorizada el", readonly=True, copy=False,
        help="Fecha en que Visar autorizó al cliente a mover esta cita por una "
             "incidencia (el técnico acudió y no pudo realizarse el servicio). "
             "Se borra al moverla: autoriza un solo cambio.")

    visar_fsm_task_ids = fields.Many2many(
        'project.task',
        string="Servicios externos (Visar)",
        compute='_compute_visar_fsm_task_ids',
        help="Tareas FSM generadas para esta cita (via la orden de venta).")

    # El inverso del enlace directo. Lo que hace falta para que una cita sepa qué
    # visita de póliza es la suya: por la línea de la orden no se puede, porque esa
    # línea apunta a la cita de la PRIMERA visita del ciclo.
    visar_visit_task_ids = fields.One2many(
        'project.task', 'visar_visit_event_id',
        string="Visitas de esta cita",
        help="Visitas que tienen esta cita como cita propia (pre-agenda de póliza).")

    def _visar_appointment_partners(self):
        """De quién es esta cita: los clientes de los pedidos que la originaron.

        Es la regla de pertenencia del reagendado, y vive aquí —en el módulo
        común— porque la usan dos módulos HERMANOS: `visar_whatsapp_agent` para
        decidir si quien escribe puede mover la cita, y `visar_field_app` para no
        invitar a reagendar a un número al que luego se le diría que no es suya.
        Escrita dos veces, divergen en cuanto alguien toque una.

        **No es `partner_id` de la tarea.** En producción el contacto de servicio
        y el cliente del pedido son distintos en 79 de 80 casos.

        Se suman los dueños de las visitas enganchadas por `visar_visit_task_ids`
        (1-oct-2026). Sin eso **ninguna** pre-agenda de póliza sería de nadie: su
        cita no la originó ninguna línea de orden —la creó el cron— así que la
        búsqueda de arriba devuelve vacío y el cliente recibiría «no encontré esa
        cita a tu nombre» justo al intentar mover la visita a la que le acabamos de
        invitar. Aquí solo se mira `visar_sale_order_id`, que es lo que este módulo
        define; `visar_subscription` extiende el método para sumar el dueño de la
        póliza, porque el campo que lo sabe es suyo y depende de nosotros.
        """
        self.ensure_one()
        lineas = self.env['sale.order.line'].sudo().search([
            ('calendar_event_id', '=', self.id),
        ])
        duenos = lineas.mapped('order_id.partner_id')
        duenos |= self.sudo().visar_visit_task_ids.mapped('visar_sale_order_id.partner_id')
        return duenos

    @api.depends('sale_order_line_ids.task_id', 'visar_visit_task_ids')
    def _compute_visar_fsm_task_ids(self):
        for event in self:
            tareas = event.sale_order_line_ids.mapped('task_id') | event.visar_visit_task_ids
            event.visar_fsm_task_ids = tareas.filtered(lambda t: t.project_id.is_fsm)
