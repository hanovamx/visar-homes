from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPoliza(TransactionCase):
    """Tests de la póliza (visar_subscription): cobro inicial de N meses, visitas al
    pago, bloqueo de dirección, combo y garantía. Crean sus propios datos."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente Póliza Test'})
        cls.partner2 = cls.env['res.partner'].create({'name': 'Otra dirección Test'})
        company = cls.env.company
        cls.project = cls.env['project.project'].create({
            'name': 'FSM Póliza Test', 'is_fsm': True, 'company_id': company.id})
        cls.project2 = cls.env['project.project'].create({
            'name': 'FSM Póliza Test 2', 'is_fsm': True, 'company_id': company.id})
        cls.plan = cls.env['sale.subscription.plan'].create({
            'name': 'Plan Póliza Test',
            'billing_period_value': 1, 'billing_period_unit': 'month',
            'visar_first_invoice_periods': 2, 'visar_commitment_months': 12})
        cls.service = cls._make_service('Servicio Póliza Test', cls.project, 100.0)

    @classmethod
    def _make_service(cls, name, project, price):
        return cls.env['product.template'].create({
            'name': name, 'type': 'service', 'invoice_policy': 'order',
            'list_price': price, 'recurring_invoice': True,
            'allow_one_time_sale': True, 'visar_generates_visit': True,
            'visar_fsm_project_id': project.id, 'taxes_id': [(6, 0, [])],
        })

    def _make_poliza(self, products, start=date(2026, 1, 1)):
        lines = [(0, 0, {'product_id': p.product_variant_id.id, 'product_uom_qty': 1})
                 for p in products]
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'plan_id': self.plan.id,
            'start_date': start, 'order_line': lines})
        order.require_payment = False
        order.action_confirm()
        return order

    def _pay(self, invoice):
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids
        ).create({})._create_payments()

    # ------------------------------------------------------------------
    def test_01_first_invoice_two_periods(self):
        order = self._make_poliza([self.service])
        self.assertEqual(order.subscription_state, '3_progress')
        self.assertEqual(len(order.order_line), 1, "sin línea de depósito")
        line = order.order_line
        _s, stop, ratio, _d = line._get_invoice_line_parameters()
        self.assertEqual(ratio, 2)
        self.assertEqual(stop, date(2026, 1, 1) + relativedelta(months=2) - relativedelta(days=1))
        inv = order._create_invoices()
        inv.action_post()
        self.assertAlmostEqual(inv.amount_total, 200.0, places=2)
        self.assertEqual(order.next_invoice_date, date(2026, 3, 1))
        self.assertFalse(order._visar_is_first_poliza_invoice(),
                         "el flag de 1ª factura debe apagarse")

    def test_02_visits_on_payment_idempotent(self):
        order = self._make_poliza([self.service])
        inv = order._create_invoices()
        inv.action_post()
        self.assertEqual(order.visar_visit_count, 0, "sin pagar, sin visitas")
        self._pay(inv)
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 2, "2 visitas en el primer ciclo")
        inv._invoice_paid_hook()
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 2, "idempotente")

    def test_03_mrr_is_single_period(self):
        order = self._make_poliza([self.service])
        self.assertAlmostEqual(order.recurring_monthly, 100.0, places=2,
                               msg="MRR = 1x mensual, no 2x")

    def test_04_address_lock(self):
        order = self._make_poliza([self.service])
        with self.assertRaises(UserError):
            order.partner_shipping_id = self.partner2.id

    def test_05_renewal_not_doubled(self):
        order = self._make_poliza([self.service])
        # Simular hijo/renovación: origin_order_id apuntando a un primer contrato.
        origin = self.env['sale.order'].create({'partner_id': self.partner.id})
        order.origin_order_id = origin.id
        self.assertFalse(order._visar_is_first_poliza_invoice())
        _s, _stop, ratio, _d = order.order_line._get_invoice_line_parameters()
        self.assertEqual(ratio, 1, "renovación NO se cobra 2x")

    def test_06_warranty_eligibility(self):
        order = self._make_poliza([self.service])
        # Sin servicio previo -> no elegible
        with self.assertRaises(UserError):
            order.action_visar_add_warranty_visit()
        # Con servicio pagado -> elegible
        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        order.action_visar_add_warranty_visit()
        self.assertEqual(order.visar_warranty_count, 1)
        self.assertAlmostEqual(order.visar_warranty_rate, 50.0, places=1)
        self.assertEqual(order.visar_last_warranty_date,
                         fields.Date.context_today(order))

    def test_07_warranty_out_of_window(self):
        order = self._make_poliza([self.service], start=date(2025, 1, 1))
        inv = order._create_invoices()
        inv.invoice_date = fields.Date.context_today(order) - timedelta(days=40)
        inv.action_post()  # sin pagar -> sin visitas; ancla = factura hace 40 días
        with self.assertRaises(UserError):
            order.action_visar_add_warranty_visit()

    def test_08_combo_two_visits_and_discount(self):
        service2 = self._make_service('Servicio Combo Test', self.project2, 200.0)
        # Dimensiones (requieren grupo) + regla de combo
        group = self.env['visar.service.group'].create(
            {'name': 'Grupo Combo Test', 'code': 'CMBTEST'})
        dim_a = self.env['visar.service.dimension'].create({
            'name': 'Dim A Test', 'code': 'DIMA', 'group_id': group.id})
        dim_b = self.env['visar.service.dimension'].create({
            'name': 'Dim B Test', 'code': 'DIMB', 'group_id': group.id})
        self.service.visar_dimension_id = dim_a.id
        service2.visar_dimension_id = dim_b.id
        self.env['visar.combo.rule'].create({
            'name': 'Combo Test', 'sequence': 1, 'discount_factor': 0.5,
            'required_dimension_ids': [(6, 0, [dim_a.id, dim_b.id])],
            'discount_dimension_ids': [(6, 0, [dim_b.id])]})

        order = self._make_poliza([self.service, service2])
        line_b = order.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id == service2)
        self.assertEqual(line_b.discount, 50.0, "combo aplica 50% al servicio B")
        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 4, "2 servicios x 2 periodos = 4 visitas")
        self.assertEqual(len(visits.filtered(lambda t: t.project_id == self.project)), 2)
        self.assertEqual(len(visits.filtered(lambda t: t.project_id == self.project2)), 2)
