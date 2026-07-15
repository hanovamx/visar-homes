from odoo import models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        """Al registrar un pago manual (efectivo/transferencia) sobre facturas de
        póliza, generar de una vez las visitas del periodo. Complementa a
        account.move._invoice_paid_hook (que cubre Stripe/liquidación real). Ambos
        son idempotentes (cuentan por orden/factura/línea), así que no duplican."""
        payments = super()._create_payments()
        moves = self.line_ids.mapped('move_id')
        for move in moves.filtered(lambda m: m.move_type == 'out_invoice'):
            for order in move.invoice_line_ids.mapped('subscription_id'):
                if order.is_subscription:
                    order._visar_generate_period_visit(move)
        return payments
