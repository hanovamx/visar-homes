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
        string="Periodos cobrados por adelantado",
        default=1,
        help="Nº de periodos que el PEDIDO cobra por adelantado. Con 2, el carrito "
             "añade una línea de mensualidad adelantada junto al servicio, de modo "
             "que el cliente paga los meses 1 y 2 de entrada, la próxima factura cae "
             "en el mes 3 y se generan 2 visitas en el primer ciclo.\n\n"
             "1 = comportamiento normal. Para la Póliza Mensual pon 2. Los planes "
             "bimestral/trimestral van en 1: su propio periodo ya cubre dos meses o "
             "más. En planes anuales NO pongas 2: cobraría dos años de entrada.",
    )
