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

    def _visar_appointment_partners(self):
        """De quién es esta cita: los clientes de los pedidos que la originaron.

        Es la regla de pertenencia del reagendado, y vive aquí —en el módulo
        común— porque la usan dos módulos HERMANOS: `visar_whatsapp_agent` para
        decidir si quien escribe puede mover la cita, y `visar_field_app` para no
        invitar a reagendar a un número al que luego se le diría que no es suya.
        Escrita dos veces, divergen en cuanto alguien toque una.

        **No es `partner_id` de la tarea.** En producción el contacto de servicio
        y el cliente del pedido son distintos en 79 de 80 casos.
        """
        self.ensure_one()
        lineas = self.env['sale.order.line'].sudo().search([
            ('calendar_event_id', '=', self.id),
        ])
        return lineas.mapped('order_id.partner_id')

    @api.depends('sale_order_line_ids.task_id')
    def _compute_visar_fsm_task_ids(self):
        for event in self:
            event.visar_fsm_task_ids = event.sale_order_line_ids.mapped('task_id').filtered(
                lambda t: t.project_id.is_fsm
            )
