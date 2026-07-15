from odoo import fields, models


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
        help="Línea de la póliza (servicio base) que generó esta visita. Permite "
             "idempotencia por línea y soporta pólizas combo (varias visitas/periodo).",
    )
    visar_is_warranty = fields.Boolean(
        string="Visita de garantía",
        default=False,
        copy=False,
        help="Visita adicional sin costo cubierta por la garantía de la póliza.",
    )
