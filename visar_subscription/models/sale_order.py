from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visar_visit_ids = fields.One2many(
        'project.task', 'visar_subscription_order_id',
        string="Visitas de la póliza", copy=False,
    )
    visar_visit_count = fields.Integer(
        string="Nº de visitas", compute='_compute_visar_visit_count',
    )
    visar_is_poliza = fields.Boolean(
        string="Es póliza (genera visitas)", compute='_compute_visar_is_poliza',
    )

    def _compute_visar_visit_count(self):
        data = self.env['project.task']._read_group(
            [('visar_subscription_order_id', 'in', self.ids)],
            ['visar_subscription_order_id'], ['__count'],
        )
        counts = {order.id: count for order, count in data}
        for order in self:
            order.visar_visit_count = counts.get(order.id, 0)

    @api.depends('is_subscription', 'order_line.product_id')
    def _compute_visar_is_poliza(self):
        for order in self:
            order.visar_is_poliza = order._visar_is_poliza()

    def _visar_is_poliza(self):
        self.ensure_one()
        return bool(self.is_subscription and any(
            l.product_id.product_tmpl_id.visar_generates_visit
            for l in self.order_line))

    # ------------------------------------------------------------------
    # Fecha "hasta" (fin) automática según la duración del plan/póliza
    # ------------------------------------------------------------------
    def _visar_compute_end_date(self):
        """Devuelve la fecha de fin = inicio + duración del plan (o None)."""
        self.ensure_one()
        months = self.plan_id.visar_commitment_months if self.plan_id else 0
        if not months:
            return None
        base = self.start_date or fields.Date.context_today(self)
        return base + relativedelta(months=months)

    @api.onchange('plan_id', 'start_date')
    def _onchange_visar_end_date(self):
        for order in self:
            if order.plan_id and not order.end_date:
                end = order._visar_compute_end_date()
                if end:
                    order.end_date = end

    def action_confirm(self):
        for order in self.filtered(lambda o: o.is_subscription):
            order._visar_apply_combo_discount()
        res = super().action_confirm()
        for order in self.filtered(lambda o: o.is_subscription and not o.end_date):
            end = order._visar_compute_end_date()
            if end:
                order.end_date = end
        return res

    # ------------------------------------------------------------------
    # Descuento de combo para pólizas (Fase 4) — reusa visar.combo.rule
    # ------------------------------------------------------------------
    def _visar_apply_combo_discount(self):
        """Aplica el descuento de combo a las líneas de una póliza que incluye varios
        servicios base cuyas dimensiones cumplen una regla de visar.combo.rule. Así
        una 'póliza combo' (p.ej. Fumigación + Corte) recibe el mismo descuento que
        en el flujo de booking. Idempotente: fija el mismo % si se re-ejecuta."""
        self.ensure_one()
        if not self._visar_is_poliza():
            return
        lines = self.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id.visar_generates_visit
            and l.product_id.product_tmpl_id.visar_dimension_id)
        dim_ids = lines.mapped('product_id.product_tmpl_id.visar_dimension_id').ids
        if len(set(dim_ids)) < 2:
            return
        rules = self.env['visar.combo.rule'].sudo().search(
            [('active', '=', True)], order='sequence')
        for rule in rules:
            if not rule._visar_applies_to_items(dim_ids):
                continue
            pct = rule._visar_discount_percent()
            for line in lines:
                if line.product_id.product_tmpl_id.visar_dimension_id.id in rule.discount_dimension_ids.ids:
                    line.discount = pct
            break  # primera regla aplicable por secuencia

    def action_visar_apply_combo_discount(self):
        """Botón para previsualizar el descuento de combo antes de confirmar."""
        for order in self:
            order._visar_apply_combo_discount()
        return True

    # ------------------------------------------------------------------
    # Bloqueo de cambio de dirección de servicio en pólizas (Fase 3)
    # ------------------------------------------------------------------
    def write(self, vals):
        if 'partner_shipping_id' in vals:
            new_id = vals.get('partner_shipping_id')
            for order in self:
                if (order.state == 'sale' and order._visar_is_poliza()
                        and order.partner_shipping_id.id != new_id):
                    raise UserError(_(
                        "No se puede cambiar la dirección de servicio de una "
                        "póliza confirmada (%s).", order.name))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Cobro inicial de N periodos (primera factura) — Fase 1
    # ------------------------------------------------------------------
    def _visar_first_invoice_periods(self):
        """Nº de mensualidades cobradas en la primera factura (y nº de visitas del
        primer ciclo). 1 = normal; pólizas usan 2."""
        self.ensure_one()
        n = self.plan_id.visar_first_invoice_periods if self.plan_id else 1
        return n if n and n > 0 else 1

    def _visar_is_first_poliza_invoice(self):
        """True cuando estamos por facturar la PRIMERA factura de una póliza nueva
        (no renovación/upsell). Se evalúa antes de postear la factura, cuando
        last_invoice_date todavía es falsy."""
        self.ensure_one()
        return bool(
            self.is_subscription
            and self.subscription_state == '3_progress'
            and not self.origin_order_id            # excluye renovaciones/hijos
            and not self.last_invoice_date          # aún no hay factura posteada
            and self._visar_is_poliza()
        )

    # ------------------------------------------------------------------
    # Generación de visitas — gatada al PAGO de la factura (Fase 1)
    # (disparada desde account.move._invoice_paid_hook)
    # ------------------------------------------------------------------
    def _visar_generate_period_visit(self, invoice):
        self.ensure_one()
        if not self.is_subscription or self.subscription_state != '3_progress':
            return
        if not invoice or invoice.move_type != 'out_invoice':
            return
        Task = self.env['project.task']
        first_invoice = self.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice').sorted('id')[:1]
        # 1ª factura del contrato → N visitas; siguientes → 1.
        n = self._visar_first_invoice_periods() if invoice == first_invoice else 1
        for line in self.order_line:
            tmpl = line.product_id.product_tmpl_id
            if not tmpl.visar_generates_visit or not tmpl.visar_fsm_project_id:
                continue
            # Idempotencia por (orden, factura, línea): crear las que falten.
            existing = Task.search_count([
                ('visar_subscription_order_id', '=', self.id),
                ('visar_source_invoice_id', '=', invoice.id),
                ('visar_source_line_id', '=', line.id),
                ('visar_is_warranty', '=', False),
            ])
            for _i in range(max(0, n - existing)):
                Task.create(self._visar_visit_vals(
                    line, tmpl.visar_fsm_project_id, invoice))

    def _visar_visit_vals(self, line, project, invoice, warranty=False):
        self.ensure_one()
        period = invoice.invoice_date if invoice else fields.Date.context_today(self)
        label = _("Garantía") if warranty else _("Visita")
        return {
            'name': _("%(label)s póliza %(period)s — %(product)s",
                      label=label, period=period, product=line.product_id.name),
            'project_id': project.id,
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'visar_subscription_order_id': self.id,
            'visar_source_invoice_id': False if warranty else (invoice.id if invoice else False),
            'visar_source_line_id': False if warranty else line.id,
            'visar_is_warranty': warranty,
        }

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_visar_add_warranty_visit(self):
        """Crea una visita de garantía (sin costo) ligada a la póliza."""
        self.ensure_one()
        line = self.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id.visar_generates_visit
            and l.product_id.product_tmpl_id.visar_fsm_project_id
        )[:1]
        if not line:
            return False
        tmpl = line.product_id.product_tmpl_id
        task = self.env['project.task'].create(
            self._visar_visit_vals(line, tmpl.visar_fsm_project_id, invoice=False, warranty=True)
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'project.task',
            'res_id': task.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_visar_view_visits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Visitas de la póliza"),
            'res_model': 'project.task',
            'domain': [('visar_subscription_order_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_visar_subscription_order_id': self.id},
        }
