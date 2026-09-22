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
             "0 = una visita por periodo pagado y sin fecha propuesta.\n\n"
             "También decide CUÁNTAS visitas genera cada factura pagada: los meses "
             "que cubre ÷ este número. Anual (12 meses) con 1 = 12 visitas; "
             "semestral = 6; mensual = 1 por mes, y 3 en el primer cobro si se "
             "cobran 3 meses de entrada.",
    )

    # Solo para enseñar en el plan lo que resulta de los números de arriba: sin esto
    # el que edita el plan tenía que hacer la cuenta de cabeza (22-sep-2026).
    visar_visits_per_invoice = fields.Integer(
        string="Visitas por factura", compute='_compute_visar_visits_preview',
        help="Visitas que genera cada factura pagada de renovación: meses del periodo "
             "÷ meses entre visitas. Se calcula; no se captura.")
    visar_visits_first_invoice = fields.Integer(
        string="Visitas en el primer cobro", compute='_compute_visar_visits_preview',
        help="Visitas que genera el primer cobro: periodos cobrados por adelantado × "
             "visitas por factura. Se calcula; no se captura.")

    @api.depends('billing_period_value', 'billing_period_unit',
                 'visar_visit_interval_months', 'visar_first_invoice_periods')
    def _compute_visar_visits_preview(self):
        for plan in self:
            por_factura = plan._visar_visits_per_period()
            plan.visar_visits_per_invoice = por_factura
            plan.visar_visits_first_invoice = max(1, plan.visar_first_invoice_periods or 1) * por_factura

    @api.constrains('visar_visit_interval_months')
    def _check_visar_visit_interval_months(self):
        for plan in self:
            if plan.visar_visit_interval_months < 0:
                raise ValidationError(_(
                    "Los meses entre visitas del plan '%s' no pueden ser negativos.",
                    plan.display_name))

    def _visar_period_months(self):
        """Meses que cubre UN periodo de facturación del plan (0 si es menos de uno)."""
        self.ensure_one()
        valor = self.billing_period_value or 0
        return {'month': valor, 'year': valor * 12}.get(self.billing_period_unit, 0)

    def _visar_visits_per_period(self):
        """Visitas que genera cada periodo pagado: meses del periodo ÷ meses entre
        visitas, y al menos una.

        Sustituye a "Visitas incluidas" (quitado el 22-sep-2026). Aquel campo contaba
        visitas por FACTURA y no por mes pagado, y se leyó al revés: la Suscripción
        Mensual lo tenía en 1 y cobra 3 meses de entrada, así que desde el 31-ago sus
        clientes pagaban 3 meses y recibían UNA visita (S00285, S00286). Además era
        una copia por póliza que no seguía al plan: 8 pólizas anuales y semestrales
        activas se quedaron en 0 y recibían una visita por factura en vez de 12 o 6.
        Derivarlo de lo que el cliente paga no deja nada que se pueda leer mal.
        """
        self.ensure_one()
        meses = self._visar_period_months()
        cada = self.visar_visit_interval_months
        if meses <= 0 or cada <= 0:
            return 1
        return max(1, meses // cada)
