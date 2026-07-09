from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

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
