from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visar_visit_ids = fields.One2many(
        'project.task', 'visar_subscription_order_id',
        string="Visitas de la póliza", copy=False,
    )
    visar_visit_count = fields.Integer(
        string="Nº de visitas", compute='_compute_visar_visit_count',
    )

    def _compute_visar_visit_count(self):
        data = self.env['project.task']._read_group(
            [('visar_subscription_order_id', 'in', self.ids)],
            ['visar_subscription_order_id'], ['__count'],
        )
        counts = {order.id: count for order, count in data}
        for order in self:
            order.visar_visit_count = counts.get(order.id, 0)

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
        res = super().action_confirm()
        for order in self.filtered(lambda o: o.is_subscription and not o.end_date):
            end = order._visar_compute_end_date()
            if end:
                order.end_date = end
        return res

    # ------------------------------------------------------------------
    # Generación de visitas por periodo facturado
    # ------------------------------------------------------------------
    def _post_invoice_hook(self):
        """Hook nativo de sale_subscription: se ejecuta por suscripción después de
        facturar un periodo. Aprovechamos para generar la visita FSM del periodo."""
        res = super()._post_invoice_hook()
        for order in self:
            order._visar_generate_period_visit()
        return res

    def _visar_generate_period_visit(self):
        self.ensure_one()
        if not self.is_subscription or self.subscription_state != '3_progress':
            return
        Task = self.env['project.task']
        invoice = self.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice'
        ).sorted('id')[-1:]
        if not invoice:
            return
        for line in self.order_line:
            tmpl = line.product_id.product_tmpl_id
            if not tmpl.visar_generates_visit or not tmpl.visar_fsm_project_id:
                continue
            # Idempotencia: una visita (no garantía) por periodo/factura.
            already = Task.search_count([
                ('visar_subscription_order_id', '=', self.id),
                ('visar_source_invoice_id', '=', invoice.id),
                ('visar_is_warranty', '=', False),
            ])
            if already:
                continue
            Task.create(self._visar_visit_vals(line, tmpl.visar_fsm_project_id, invoice))

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
            'visar_is_warranty': warranty,
        }

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_visar_add_warranty_visit(self):
        """Crea una visita de garantía (sin costo) ligada a la póliza (punto 4)."""
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
