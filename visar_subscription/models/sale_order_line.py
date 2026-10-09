from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # ------------------------------------------------------------------
    # Mensualidades adelantadas (Fase 1, Modelo A)
    #
    # La regla "la póliza se paga 2 meses por adelantado" se materializa como una
    # LÍNEA REAL en el carrito, no como un multiplicador al facturar. El sitio web
    # cobra exactamente `order.amount_total` (website_sale rechaza cualquier otro
    # importe), así que el segundo mes tiene que existir como línea para poder
    # cobrarse en línea.
    # ------------------------------------------------------------------
    visar_anticipo_for_line_id = fields.Many2one(
        'sale.order.line', string="Anticipo de la línea",
        ondelete='cascade', copy=False, index=True,
        help="Línea de servicio recurrente que esta línea de anticipo espeja. Al "
             "borrarse el servicio se borra su anticipo (ondelete cascade).",
    )
    visar_anticipo_periods = fields.Integer(
        string="Periodos adelantados", default=0, copy=False,
        help="Nº de periodos ADICIONALES que cobra esta línea de anticipo (N-1: el "
             "primer periodo lo cobra la propia línea recurrente).",
    )

    # ------------------------------------------------------------------
    # Descuento de combo: sobrevive a cualquier recálculo de precios
    #
    # El descuento de combo NO sale de la lista de precios: lo escribe a mano el
    # flujo Visar (wizard web, agente, `_visar_apply_combo_discount`). Y Odoo, cada
    # vez que reprecia (`_recompute_prices`), pone `discount` en cero y lo vuelve a
    # calcular desde la lista — o sea, lo borra. `/shop/payment` reprecia SIEMPRE
    # antes de enseñar los métodos de pago, así que con "Finalizar compra" una
    # póliza combo cotizada en 2,850 se cobraba en 3,230: la línea de servicio
    # perdía el 50% y su mensualidad adelantada (que no se reprecia) lo conservaba.
    # Al confirmar, el anticipo se re-espejaba ya sin descuento y el pedido quedaba
    # en 3,990: "el monto pagado es diferente al total del carrito" (S00024,
    # 8-oct-2026).
    #
    # Guardar el porcentaje en la línea lo vuelve parte del cálculo en vez de un
    # valor que hay que vigilar en cada sitio que reprecia.
    # ------------------------------------------------------------------
    visar_combo_discount = fields.Float(
        string="Descuento de combo (%)", digits='Discount', default=0.0,
        help="Descuento de combo fijado por el flujo Visar. Se vuelve a aplicar "
             "cada vez que Odoo recalcula el descuento de la línea, que de otro "
             "modo lo dejaría en lo que diga la lista de precios (cero).",
    )

    @api.depends('visar_combo_discount')
    def _compute_discount(self):
        super()._compute_discount()
        for line in self:
            # En un upsell el descuento es el prorrateo del periodo en curso
            # (sale_subscription); ahí manda el core.
            if line.visar_combo_discount and line.order_id.subscription_state != '7_upsell':
                line.discount = line.visar_combo_discount

    def _prepare_invoice_line(self, **optional_values):
        """Da a la línea de anticipo el periodo diferido que realmente cubre.

        `sale_subscription` avanza `next_invoice_date` tomando el MÁXIMO
        `deferred_end_date` de los apuntes de la factura (`_get_max_invoiced_date`,
        que ignora los apuntes sin fecha). Al fechar aquí el anticipo con el mes 2,
        la próxima factura cae sola en el mes 3: no hace falta tocar
        `_update_next_invoice_date` (hacer ambas cosas lo adelantaría el doble).

        De paso el ingreso diferido queda bien por primera vez —mes 1 en el mes 1 y
        mes 2 en el mes 2— en vez de un único periodo estirado de 62 días.
        """
        res = super()._prepare_invoice_line(**optional_values)
        source = self.visar_anticipo_for_line_id
        if not source or not self.order_id.plan_id:
            return res
        period = self.order_id.plan_id.billing_period
        base_start, base_stop, _ratio, _days = source._get_invoice_line_parameters()
        periods = max(1, self.visar_anticipo_periods)
        start = base_stop + relativedelta(days=1)
        stop = base_start + (periods + 1) * period - relativedelta(days=1)
        res.update({
            'subscription_id': self.order_id.id,
            'deferred_start_date': start,
            'deferred_end_date': stop,
            'name': _("%(name)s — periodo %(start)s a %(stop)s",
                      name=res.get('name') or self.name, start=start, stop=stop),
        })
        return res

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
