from dateutil.relativedelta import relativedelta

from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # ------------------------------------------------------------------
    # Primera factura = N mensualidades (Fase 1, Modelo A)
    # ------------------------------------------------------------------
    def _visar_should_extend_first_invoice(self):
        """Solo la línea recurrente del servicio base, en la PRIMERA factura de una
        póliza con N>1 periodos iniciales."""
        self.ensure_one()
        return bool(
            self.recurring_invoice
            and not self._is_postpaid_line()
            and self.order_id.subscription_state != '7_upsell'
            and self.product_id.product_tmpl_id.visar_generates_visit
            and self.order_id._visar_first_invoice_periods() > 1
            and self.order_id._visar_is_first_poliza_invoice()
        )

    def _get_invoice_line_parameters(self):
        new_period_start, new_period_stop, ratio, number_of_days = \
            super()._get_invoice_line_parameters()
        self.ensure_one()
        if not self._visar_should_extend_first_invoice():
            return new_period_start, new_period_stop, ratio, number_of_days
        # Extender el periodo a N mensualidades: cobra N× (vía ratio) y empuja el
        # deferred_end_date → next_invoice_date cae tras esos N periodos.
        n = self.order_id._visar_first_invoice_periods()
        period = self.order_id.plan_id.billing_period
        extended_stop = new_period_start + n * period
        # Respetar la fecha de fin del compromiso si aplica.
        if self.order_id.end_date and extended_stop > self.order_id.end_date:
            extended_stop = self.order_id.end_date + relativedelta(days=1)
        days = (extended_stop - new_period_start).days
        return new_period_start, extended_stop - relativedelta(days=1), n, days

    def _visar_is_poliza_line(self):
        """Línea de suscripción cuyo producto genera visita por periodo. Sus visitas
        las crea este módulo vía _post_invoice_hook (una por periodo), NO la
        generación de tareas al confirmar (ni la nativa ni la de visar_fsm)."""
        self.ensure_one()
        return self.order_id.is_subscription and self.product_id.product_tmpl_id.visar_generates_visit

    def _timesheet_service_generation(self):
        # Quitar las líneas de póliza ANTES de la generación de tareas (nativa o visar_fsm),
        # para no crear una tarea única al confirmar. super() con el resto del recordset.
        poliza = self.filtered(lambda l: l._visar_is_poliza_line())
        remaining = self - poliza
        return super(SaleOrderLine, remaining)._timesheet_service_generation()

    # Defensa adicional en los filtros nativos de sale_project.
    def _get_so_lines_task_global_project(self):
        return super()._get_so_lines_task_global_project().filtered(
            lambda sol: not sol._visar_is_poliza_line())

    def _get_so_lines_new_project(self):
        return super()._get_so_lines_new_project().filtered(
            lambda sol: not sol._visar_is_poliza_line())
