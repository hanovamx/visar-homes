from odoo import api, fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_subscription_order_id = fields.Many2one(
        'sale.order',
        string="Póliza (suscripción)",
        index=True,
        ondelete='set null',
        copy=False,
        help="Póliza / suscripción que originó esta visita de servicio.",
    )
    visar_source_invoice_id = fields.Many2one(
        'account.move',
        string="Factura de periodo",
        index=True,
        ondelete='set null',
        copy=False,
        help="Factura del periodo de la póliza que generó esta visita.",
    )
    visar_source_line_id = fields.Many2one(
        'sale.order.line',
        string="Línea de póliza",
        index=True,
        ondelete='set null',
        copy=False,
        help="Línea REPRESENTANTE de la póliza que generó esta visita. En una visita "
             "consolidada (fumigación + áreas verdes en la misma vuelta) es solo una "
             "de las dos; el conjunto completo está en «Líneas de póliza cubiertas».",
    )
    # Todas las líneas que atiende la visita. Es la que manda: la idempotencia por
    # (orden, factura, grupo) y el etiquetado de grupos de servicio salen de aquí.
    #
    # No se reusa `visar_sale_line_ids` (el o2m sobre `sale.order.line.task_id` del
    # que cuelga la venta puntual): la línea de una póliza genera N visitas a lo
    # largo del contrato y ese m2o solo puede apuntar a una.
    visar_source_line_ids = fields.Many2many(
        'sale.order.line',
        'visar_task_poliza_line_rel', 'task_id', 'line_id',
        string="Líneas de póliza cubiertas",
        copy=False,
        help="Todas las líneas de la póliza que atiende esta visita. Una visita "
             "consolidada lleva las dos (o más); una normal, solo la suya.",
    )
    visar_is_warranty = fields.Boolean(
        string="Visita de garantía",
        default=False,
        copy=False,
        help="Visita adicional sin costo cubierta por la garantía de la póliza.",
    )

    @api.depends('visar_sale_line_ids.product_id', 'visar_sale_line_ids.order_id',
                 'sale_order_id', 'visar_source_line_ids.product_id')
    def _compute_visar_service_group_ids(self):
        """Las visitas de póliza etiquetan sus grupos desde `visar_source_line_ids`.

        El cómputo de `visar_fsm` sale de `sale.order.line.task_id`, que una visita de
        póliza nunca tiene (ver arriba). Sin esto una visita consolidada no contaría en
        ninguna línea de negocio y el tablero de FSM la dejaría fuera al agrupar por
        servicio — justo la métrica que la consolidación existe para no romper.

        El decorador REPITE las dependencias de la superclase a propósito: Odoo resuelve
        `depends` desde el método que encuentra en el modelo final, así que las de allá
        se perderían si aquí no se listan.
        """
        super()._compute_visar_service_group_ids()
        for task in self.filtered('visar_source_line_ids'):
            templates = task.visar_source_line_ids.mapped('product_id.product_tmpl_id')
            task.visar_service_group_ids = (
                templates._visar_service_groups() if templates else False)
