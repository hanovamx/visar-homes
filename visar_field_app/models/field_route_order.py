# -*- coding: utf-8 -*-
from odoo import api, fields, models


class VisarFieldRouteOrder(models.Model):
    """Orden manual de la ruta del técnico (arrastrar y soltar en la app de campo).

    Una fila por (técnico, servicio): el número de parada que el técnico eligió
    arrastrando las tarjetas. Persiste entre sesiones y entre dispositivos.

    **Por qué un modelo y no un entero en `project.task`:** un servicio puede
    tener VARIOS técnicos (`visar_technician_ids`, citas multi-técnico del
    maestro). Con un solo entero en la tarea, el orden que elige un técnico
    reordenaría la lista de los demás, y los números que asigna sobre SU lista
    (1..N) chocarían con los servicios que solo ve el otro. Cada técnico ordena
    su propia ruta.

    El orden es **relativo dentro de un día**: la app agrupa por día agendado y,
    dentro de cada día, ordena por esta secuencia. Los servicios sin fila aquí
    (p. ej. uno agendado después del último arrastre) caen al final del día,
    ordenados por hora.
    """
    _name = 'visar.field.route.order'
    _description = 'Visar - Orden manual de ruta del técnico'
    _order = 'sequence, id'

    employee_id = fields.Many2one(
        'hr.employee', string='Técnico', required=True, ondelete='cascade',
        index=True)
    task_id = fields.Many2one(
        'project.task', string='Servicio', required=True, ondelete='cascade',
        index=True)
    sequence = fields.Integer(string='Parada', default=0)

    _employee_task_uniq = models.Constraint(
        'UNIQUE(employee_id, task_id)',
        'Cada técnico tiene un solo número de parada por servicio.',
    )

    # Secuencia de los servicios que el técnico NO ha ordenado a mano. Alta a
    # propósito: caen al final del día (después de los arrastrados), donde se
    # desempatan por hora agendada.
    UNORDERED_SEQUENCE = 9999

    @api.model
    def _visar_order_map(self, employee, tasks):
        """`{task_id: sequence}` con el orden manual del técnico.

        Solo incluye los servicios que el técnico ordenó; el resto lo resuelve
        quien llama con `UNORDERED_SEQUENCE`.
        """
        if not employee or not tasks:
            return {}
        rows = self.sudo().search([
            ('employee_id', '=', employee.id),
            ('task_id', 'in', tasks.ids),
        ])
        return {row.task_id.id: row.sequence for row in rows}

    @api.model
    def _visar_set_order(self, employee, task_ids):
        """Guarda `task_ids` (ya en el orden deseado) como paradas 1..N.

        Crea o actualiza una fila por servicio; idempotente. El llamador debe
        haber validado que los servicios son del técnico.
        """
        if not employee or not task_ids:
            return
        existing = {
            row.task_id.id: row
            for row in self.sudo().search([
                ('employee_id', '=', employee.id),
                ('task_id', 'in', task_ids),
            ])
        }
        to_create = []
        for position, task_id in enumerate(task_ids, start=1):
            row = existing.get(task_id)
            if row:
                if row.sequence != position:
                    row.sequence = position
            else:
                to_create.append({
                    'employee_id': employee.id,
                    'task_id': task_id,
                    'sequence': position,
                })
        if to_create:
            self.sudo().create(to_create)
