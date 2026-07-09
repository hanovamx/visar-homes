from odoo import fields, models


class SaleSubscriptionPlan(models.Model):
    _inherit = 'sale.subscription.plan'

    visar_commitment_months = fields.Integer(
        string="Duración del compromiso (meses)",
        default=0,
        help="Meses de compromiso de la póliza. Al elegir el plan en una orden, la "
             "fecha 'hasta' (fin) se calcula como fecha de inicio + esta duración. "
             "0 = sin fecha de fin automática (déjalo así para planes que no sean "
             "póliza; para pólizas anuales pon 12).",
    )
