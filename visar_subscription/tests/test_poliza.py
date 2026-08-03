from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPoliza(TransactionCase):
    """Tests de la póliza (visar_subscription): cobro adelantado de N meses, visitas
    al pago, bloqueo de dirección, combo y garantía. Crean sus propios datos."""

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
        # Plan sin cobro adelantado: su propio periodo ya cubre dos meses.
        cls.plan_bimestral = cls.env['sale.subscription.plan'].create({
            'name': 'Plan Bimestral Test',
            'billing_period_value': 2, 'billing_period_unit': 'month',
            'visar_first_invoice_periods': 1, 'visar_commitment_months': 0})
        cls.service = cls._make_service('Servicio Póliza Test', cls.project, 100.0)

    @classmethod
    def _make_service(cls, name, project, price):
        return cls.env['product.template'].create({
            'name': name, 'type': 'service', 'invoice_policy': 'order',
            'list_price': price, 'recurring_invoice': True,
            'allow_one_time_sale': True, 'visar_generates_visit': True,
            'visar_fsm_project_id': project.id, 'taxes_id': [(6, 0, [])],
        })

    def _make_poliza(self, products, start=date(2026, 1, 1), plan=None):
        lines = [(0, 0, {'product_id': p.product_variant_id.id, 'product_uom_qty': 1})
                 for p in products]
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'plan_id': (plan or self.plan).id,
            'start_date': start, 'order_line': lines})
        # El cobro adelantado vive en el PEDIDO, no en la factura: el carrito web
        # añade la línea antes de pagar, y estos tests reproducen ese mismo estado.
        order._visar_sync_anticipo_lines()
        order.require_payment = False
        order.action_confirm()
        return order

    def _pay(self, invoice):
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids
        ).create({})._create_payments()

    def _service_lines(self, order):
        return order.order_line.filtered(
            lambda l: l.recurring_invoice and not l.visar_anticipo_for_line_id)

    def _anticipo_lines(self, order):
        return order.order_line.filtered('visar_anticipo_for_line_id')

    # ------------------------------------------------------------------
    def test_01_first_invoice_two_periods(self):
        order = self._make_poliza([self.service])
        self.assertEqual(order.subscription_state, '3_progress')

        service = self._service_lines(order)
        anticipo = self._anticipo_lines(order)
        self.assertEqual(len(anticipo), 1, "una línea de mensualidad adelantada")
        self.assertEqual(anticipo.visar_anticipo_for_line_id, service)
        self.assertEqual(anticipo.visar_anticipo_periods, 1)
        self.assertAlmostEqual(anticipo.price_unit, service.price_unit, places=2)
        self.assertEqual(anticipo.discount, service.discount)
        self.assertFalse(anticipo.recurring_invoice,
                         "el anticipo NO es recurrente: se cobra una sola vez")
        # Esto es exactamente lo que el sitio web va a cobrar.
        self.assertAlmostEqual(order.amount_total, 200.0, places=2)

        inv = order._create_invoices()
        inv.action_post()
        self.assertAlmostEqual(inv.amount_total, 200.0, places=2)
        # El anticipo cubre el mes 2, y de ahí sale next_invoice_date sin overrides.
        aml = inv.invoice_line_ids.filtered(
            lambda l: l.sale_line_ids.visar_anticipo_for_line_id)
        self.assertEqual(aml.deferred_start_date, date(2026, 2, 1))
        self.assertEqual(aml.deferred_end_date, date(2026, 2, 28))
        self.assertEqual(order.next_invoice_date, date(2026, 3, 1))

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
                               msg="MRR = 1x mensual; el anticipo no lo infla")

    def test_04_address_lock(self):
        order = self._make_poliza([self.service])
        with self.assertRaises(UserError):
            order.partner_shipping_id = self.partner2.id

    def test_05_renewal_has_no_anticipo(self):
        """La renovación cobra un periodo normal: el adelanto es solo del alta."""
        order = self._make_poliza([self.service])
        # Odoo no deja renovar una suscripción sin facturar.
        order._create_invoices().action_post()
        action = order.prepare_renewal_order()
        renewal = self.env['sale.order'].browse(action['res_id'])
        self.assertEqual(renewal.subscription_state, '2_renewal')
        self.assertFalse(self._anticipo_lines(renewal),
                         "la renovación NO lleva mensualidad adelantada")
        self.assertAlmostEqual(renewal.amount_total, 100.0, places=2)

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
            lambda l: l.product_id.product_tmpl_id == service2
            and not l.visar_anticipo_for_line_id)
        self.assertEqual(line_b.discount, 50.0, "combo aplica 50% al servicio B")
        self.assertEqual(len(self._anticipo_lines(order)), 2, "un anticipo por servicio")
        # El descuento de combo se espeja en el anticipo: si no, el segundo mes se
        # cobraría a precio de lista.
        anticipo_b = self._anticipo_lines(order).filtered(
            lambda l: l.visar_anticipo_for_line_id == line_b)
        self.assertEqual(len(anticipo_b), 1)
        self.assertEqual(anticipo_b.discount, 50.0)

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 4, "2 servicios x 2 periodos = 4 visitas")
        self.assertEqual(len(visits.filtered(lambda t: t.project_id == self.project)), 2)
        self.assertEqual(len(visits.filtered(lambda t: t.project_id == self.project2)), 2)

    # ------------------------------------------------------------------
    def test_09_anticipo_survives_recompute_prices(self):
        """`_recompute_prices` no debe tocar el anticipo.

        Se dispara solo al escribir la dirección en el checkout. Sin el filtro de
        `_get_update_prices_lines`, el anticipo caería al list_price del producto (0)
        y el descuento a 0: el carrito bajaría y el cliente pagaría de menos, sin que
        nada avise.
        """
        order = self._make_poliza([self.service])
        anticipo = self._anticipo_lines(order)
        before_price, before_total = anticipo.price_unit, order.amount_total

        order._recompute_prices()

        self.assertAlmostEqual(anticipo.price_unit, before_price, places=2)
        self.assertAlmostEqual(order.amount_total, before_total, places=2)

    def test_10_anticipo_invoiced_only_once(self):
        """El anticipo va en la 1ª factura y nunca vuelve a facturarse."""
        order = self._make_poliza([self.service])
        anticipo = self._anticipo_lines(order)

        first = order._create_invoices()
        first.action_post()
        self.assertTrue(anticipo.invoice_lines, "el anticipo va en la 1ª factura")
        self.assertAlmostEqual(anticipo.qty_invoiced, anticipo.product_uom_qty, places=2)
        self.assertAlmostEqual(anticipo.qty_to_invoice, 0.0, places=2,
                               msg="no queda nada por facturar del anticipo")
        self.assertNotIn(anticipo, order._get_invoiceable_lines(),
                         "no vuelve a entrar en las líneas facturables")

    def test_11_plan_without_prepay(self):
        """Bimestral: sin línea de anticipo, 1 visita, periodo de 2 meses."""
        order = self._make_poliza([self.service], plan=self.plan_bimestral)
        self.assertFalse(self._anticipo_lines(order),
                         "el plan bimestral no cobra periodos extra")
        self.assertAlmostEqual(order.amount_total, 100.0, places=2)

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        self.assertEqual(order.next_invoice_date, date(2026, 3, 1),
                         "un periodo bimestral = 2 meses")
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 1, "1 visita por periodo facturado")

    def test_12_full_payment_not_classified_partial(self):
        """El total del pedido debe coincidir con lo que la suscripción espera cobrar.

        `_get_partial_payment_subscription_transaction` compara el importe autorizado
        contra `_next_billing_details()`. Si el anticipo quedara fuera de las líneas
        facturables, un pago COMPLETO se clasificaría como parcial: no se crearía
        factura, no se generarían visitas y el dinero quedaría sin aplicar, sin ningún
        error visible. Este es el invariante que lo impide.
        """
        order = self._make_poliza([self.service])
        details = order._next_billing_details()
        expected = details['tax_totals']['total_amount_currency']
        self.assertAlmostEqual(expected, order.amount_total, places=2)

    def test_13_first_visit_inherits_booking(self):
        """La 1ª visita hereda fecha y técnicos de la cita; las demás nacen libres."""
        if 'calendar_event_id' not in self.env['sale.order.line']._fields:
            self.skipTest("website_appointment_sale no está instalado")
        order = self._make_poliza([self.service])
        start = fields.Datetime.to_datetime('2026-01-15 16:00:00')
        stop = fields.Datetime.to_datetime('2026-01-15 18:00:00')
        event = self.env['calendar.event'].create({
            'name': 'Cita Póliza Test', 'start': start, 'stop': stop})
        self._service_lines(order).calendar_event_id = event.id

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)

        visits = order.visar_visit_ids.filtered(
            lambda t: not t.visar_is_warranty).sorted('id')
        self.assertEqual(len(visits), 2)
        self.assertEqual(visits[0].planned_date_begin, start,
                         "la 1ª visita conserva el horario que eligió el cliente")
        self.assertEqual(visits[0].date_deadline, stop)
        self.assertFalse(visits[1].planned_date_begin,
                         "la 2ª visita se agenda después")

    def test_14_anticipo_matches_service_taxes(self):
        """El anticipo lleva los mismos impuestos que el servicio que espeja.

        `tax_ids` de la línea se recalcula desde `product.taxes_id` al cambiar el
        partner; si el producto de anticipo no coincidiera con el servicio, el total
        del carrito se movería a mitad del checkout.
        """
        order = self._make_poliza([self.service])
        service = self._service_lines(order)
        anticipo = self._anticipo_lines(order)
        self.assertEqual(anticipo.tax_ids, service.tax_ids)
        self.assertAlmostEqual(
            anticipo.price_total, service.price_total, places=2,
            msg="el anticipo cobra exactamente un periodo más")

    def test_15_sync_is_idempotent_and_cleans_up(self):
        """Re-sincronizar no duplica; quitar el servicio se lleva su anticipo."""
        order = self._make_poliza([self.service])
        order._visar_sync_anticipo_lines()
        self.assertEqual(len(self._anticipo_lines(order)), 1, "no duplica")

        # Cascade: al borrar el servicio, su anticipo se va con él.
        order.action_cancel()
        service = self._service_lines(order)
        anticipo_ids = self._anticipo_lines(order).ids
        service.unlink()
        self.assertFalse(self.env['sale.order.line'].browse(anticipo_ids).exists(),
                         "el anticipo huérfano no debe sobrevivir al servicio")
