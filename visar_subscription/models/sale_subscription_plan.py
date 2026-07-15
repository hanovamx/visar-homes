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
    visar_first_invoice_periods = fields.Integer(
        string="Periodos en la 1ª factura",
        default=1,
        help="Nº de mensualidades que se cobran por adelantado en la PRIMERA factura "
             "de la póliza (y nº de visitas generadas en ese primer ciclo). La próxima "
             "factura se emite hasta después de esos periodos; luego la cadencia es "
             "nativa. 1 = comportamiento normal. Para pólizas pon 2 (cobra 2 meses de "
             "entrada y crea 2 visitas).",
    )
