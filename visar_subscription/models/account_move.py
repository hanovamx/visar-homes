from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _invoice_paid_hook(self):
        """Hook nativo que se dispara cuando una factura queda pagada (efectivo o
        tarjeta/Stripe, vía reconciliación). Generamos aquí las visitas de la póliza
        del periodo, para respetar 'la visita se crea cuando el cliente paga'."""
        res = super()._invoice_paid_hook()
        for move in self.filtered(lambda m: m.move_type == 'out_invoice'):
            orders = move.invoice_line_ids.mapped('subscription_id')
            for order in orders:
                if order.is_subscription:
                    order._visar_generate_period_visit(move)
        return res
