from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
    visar_included_visits = fields.Integer(
        string="Visitas incluidas",
        default=0,
        help="Nº de visitas que incluye el plan por cada factura, independiente de "
             "cuántos periodos se cobren por adelantado. No afecta el precio ni "
             "genera cargos adicionales.\n\n"
             "Es la forma de vender un plan anual de un SOLO pago con 12 visitas: "
             "periodos cobrados por adelantado en 1 (una factura al año) y visitas "
             "incluidas en 12.\n\n"
             "0 = derivar el nº de visitas de los periodos realmente cobrados por "
             "adelantado (comportamiento por defecto: 1 visita por periodo facturado).",
    )

    visar_visit_interval_months = fields.Integer(
        string="Meses entre visitas",
        default=1,
        help="Separación entre las visitas PREVENTIVAS de la póliza, para proponer "
             "fecha a las que todavía no están agendadas. La serie se ancla en la "
             "fecha REAL de la primera visita (la que el cliente eligió al contratar), "
             "no en la factura ni en el pago.\n\n"
             "1 = una visita al mes, que es lo normal en todos los planes. Las visitas "
             "correctivas y las de garantía no entran en la serie ni la recorren: son "
             "adicionales y no consumen las visitas del cliente.\n\n"
             "0 = no proponer fechas para este plan.",
    )

    @api.constrains('visar_visit_interval_months')
    def _check_visar_visit_interval_months(self):
        for plan in self:
            if plan.visar_visit_interval_months < 0:
                raise ValidationError(_(
                    "Los meses entre visitas del plan '%s' no pueden ser negativos.",
                    plan.display_name))

    @api.constrains('visar_included_visits')
    def _check_visar_included_visits(self):
        for plan in self:
            if plan.visar_included_visits < 0:
                raise ValidationError(_(
                    "Las visitas incluidas del plan '%s' no pueden ser negativas.",
                    plan.display_name))
