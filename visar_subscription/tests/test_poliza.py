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
        # Plan anual de un SOLO pago con visitas incluidas: una factura al año, pero
        # el cliente tiene derecho a 12 visitas. La unidad 'year' es la del plan real
        # que motivó el campo, y la que el guardia de la migración 19.0.1.3.0 (que
        # filtra por unidad 'month') no alcanza.
        cls.plan_anual = cls.env['sale.subscription.plan'].create({
            'name': 'Plan Anual Test',
            'billing_period_value': 1, 'billing_period_unit': 'year',
            'visar_first_invoice_periods': 1, 'visar_commitment_months': 0,
            'visar_included_visits': 12})
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

    # ------------------------------------------------------------------
    # Visitas incluidas en el plan (REQ-2732): el nº de visitas deja de derivarse
    # del nº de periodos cobrados por adelantado.
    # ------------------------------------------------------------------
    def test_16_included_visits_single_payment(self):
        """Plan anual de un pago: 12 visitas sin cobrar 12 años."""
        order = self._make_poliza([self.service], plan=self.plan_anual)
        self.assertEqual(order.visar_included_visits, 12,
                         "la orden hereda las visitas incluidas del plan")
        self.assertFalse(self._anticipo_lines(order),
                         "las visitas incluidas no cobran periodos extra")
        self.assertAlmostEqual(order.amount_total, 100.0, places=2,
                               msg="se cobra un solo periodo")

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 12, "12 visitas contra una única factura")
        self.assertEqual(order.next_invoice_date, date(2027, 1, 1),
                         "no se adelantaron periodos de facturación")

    def test_17_included_visits_do_not_change_total(self):
        """El importe es idéntico con y sin visitas incluidas (RF-03)."""
        plan_sin = self.env['sale.subscription.plan'].create({
            'name': 'Plan Anual Sin Visitas Test',
            'billing_period_value': 1, 'billing_period_unit': 'year',
            'visar_first_invoice_periods': 1, 'visar_commitment_months': 0,
            'visar_included_visits': 0})
        con = self._make_poliza([self.service], plan=self.plan_anual)
        sin = self._make_poliza([self.service], plan=plan_sin)
        self.assertAlmostEqual(con.amount_total, sin.amount_total, places=2)
        self.assertAlmostEqual(con.recurring_monthly, sin.recurring_monthly, places=2,
                               msg="el MRR tampoco se mueve")

    def test_18_included_visits_manual_override(self):
        """El valor puesto a mano en la póliza manda y no altera el plan (RF-04)."""
        order = self._make_poliza([self.service], plan=self.plan_anual)
        order.visar_included_visits = 10

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 10, "manda el valor de la póliza, no el del plan")
        self.assertEqual(self.plan_anual.visar_included_visits, 12,
                         "el plan no se modifica desde la orden")

    def test_19_included_visits_per_service_line(self):
        """Con 2 servicios salen 2 lotes, uno por tablero (RF-10)."""
        service2 = self._make_service('Servicio Anual Test 2', self.project2, 200.0)
        order = self._make_poliza([self.service, service2], plan=self.plan_anual)

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 24, "12 visitas por cada línea de servicio")
        self.assertEqual(len(visits.filtered(lambda t: t.project_id == self.project)), 12)
        self.assertEqual(len(visits.filtered(lambda t: t.project_id == self.project2)), 12)
        # Sin consecutivo quedarían 12 tareas de título idéntico en el tablero.
        self.assertEqual(len(set(visits.mapped('name'))), 24,
                         "cada visita del lote se distingue por su consecutivo")

    def test_20_included_visits_on_every_invoice(self):
        """Cada factura genera su propio lote y no toca los anteriores (RF-12)."""
        order = self._make_poliza([self.service], plan=self.plan_anual)
        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        first_visits = order.visar_visit_ids.filtered(
            lambda t: not t.visar_is_warranty)
        self.assertEqual(len(first_visits), 12)

        # Traer la próxima fecha al pasado para poder facturar el periodo siguiente
        # dentro del test; es el mismo camino que recorre el cron de suscripciones.
        order.next_invoice_date = date(2026, 2, 1)
        inv2 = order._create_invoices()
        inv2.action_post()
        self._pay(inv2)

        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 24, "la 2ª factura genera otro lote de 12")
        self.assertEqual(first_visits.exists(), first_visits,
                         "las visitas del periodo anterior no se borran")

    def test_21_included_visits_from_web_cart(self):
        """Contratación desde el sitio web: la orden hereda las visitas del plan.

        El carrito **no** crea la orden con el plan: nace sin él y `_cart_add` lo
        resuelve después, a partir del pricing recurrente de la lista, con un `write`
        (`self.plan_id = pricing.plan_id`). Por eso el campo es un compute almacenado
        y no un onchange: los onchange no corren en el flujo web. Este test recorre ese
        camino real, no una simulación.
        """
        SaleOrder = self.env['sale.order']
        if not hasattr(SaleOrder, '_cart_add'):
            self.skipTest("website_sale no está instalado")
        company = self.env.company
        pricelist = self.env['product.pricelist'].create({
            'name': 'Lista Póliza Anual Test', 'company_id': company.id,
            'currency_id': company.currency_id.id})
        self.env['product.pricelist.item'].create({
            'pricelist_id': pricelist.id, 'applied_on': '1_product',
            'product_tmpl_id': self.service.id, 'compute_price': 'fixed',
            'fixed_price': 1200.0, 'plan_id': self.plan_anual.id})

        vals = {'partner_id': self.partner.id, 'pricelist_id': pricelist.id}
        website = self.env['website'].search([], limit=1)
        if website:
            vals['website_id'] = website.id
        order = SaleOrder.create(vals)
        self.assertEqual(order.visar_included_visits, 0, "el carrito nace sin plan")

        order.plan_id = False
        order._cart_add(product_id=self.service.product_variant_id.id, quantity=1,
                        plan_id=self.plan_anual.id, allow_one_time_sale=False)
        order.invalidate_recordset()

        self.assertEqual(order.plan_id, self.plan_anual, "el carrito resolvió el plan")
        self.assertEqual(order.visar_included_visits, 12,
                         "hereda las visitas aunque el plan llegue por write, no en create")
        self.assertFalse(order.order_line.filtered('visar_anticipo_for_line_id'),
                         "un solo pago: sin mensualidad adelantada")

        order.require_payment = False
        order.action_confirm()
        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)
        self.assertEqual(len(order.invoice_ids), 1, "una sola factura")
        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 12, "12 visitas contra esa única factura")

    # ------------------------------------------------------------------
    # Consolidación del combo en UNA visita
    #
    # La venta puntual ya lo hacía desde el 13-ago-2026 (`visar_fsm`); la póliza
    # seguía por su propio camino (`_visar_generate_period_visit`) y partía la visita
    # en dos. Estos tests fijan que las dos rutas obedecen la MISMA configuración.
    # ------------------------------------------------------------------
    def _combo_poliza_setup(self):
        """Dos servicios de proyectos distintos que comparten visita, con sus grupos.

        Es la configuración real de producción: cada proyecto de servicio apunta al
        proyecto anfitrión con `visar_fsm_combined_project_id`. Devuelve
        (proyecto combinado, segundo servicio).
        """
        combined = self.env['project.project'].create({
            'name': 'FSM Combinados Póliza Test', 'is_fsm': True,
            'company_id': self.env.company.id})
        (self.project | self.project2).write(
            {'visar_fsm_combined_project_id': combined.id})
        service2 = self._make_service(
            'Servicio Combo Visita Test', self.project2, 200.0)
        self.group_a = self.env['visar.service.group'].create(
            {'name': 'Fumigación Visita Test', 'code': 'GRPVA'})
        self.group_b = self.env['visar.service.group'].create(
            {'name': 'Áreas Verdes Visita Test', 'code': 'GRPVB'})
        self.env['visar.service.dimension'].create({
            'name': 'Dim Visita A Test', 'code': 'DIMVA',
            'group_id': self.group_a.id, 'product_tmpl_id': self.service.id})
        self.env['visar.service.dimension'].create({
            'name': 'Dim Visita B Test', 'code': 'DIMVB',
            'group_id': self.group_b.id, 'product_tmpl_id': service2.id})
        return combined, service2

    def test_22_combo_poliza_es_una_sola_visita(self):
        """Una póliza combo genera UNA visita por periodo, no una por servicio."""
        combined, service2 = self._combo_poliza_setup()
        order = self._make_poliza([self.service, service2])
        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)

        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 2,
                         "2 periodos pagados = 2 visitas, no 4 (una por servicio)")
        self.assertEqual(visits.mapped('project_id'), combined,
                         "la visita se presta en el proyecto anfitrión del combo")

        lines = self._service_lines(order)
        for visit in visits:
            self.assertEqual(visit.visar_source_line_ids, lines,
                             "la visita cubre las DOS líneas de la póliza")
            self.assertIn(visit.visar_source_line_id, lines,
                          "el m2o conserva una de ellas como representante")
            self.assertEqual(visit.visar_service_group_ids,
                             self.group_a | self.group_b,
                             "cuenta en las dos líneas de negocio")
            # El técnico lee este título en su tarjeta: tiene que nombrar el trabajo
            # completo, no el producto de una de las dos líneas.
            self.assertIn(' + ', visit.name)
            self.assertIn(self.group_a.name, visit.name)
            self.assertIn(self.group_b.name, visit.name)

        inv._invoice_paid_hook()
        self.assertEqual(
            len(order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)), 2,
            "idempotente por (orden, factura, grupo de líneas)")

    def test_23_conteos_distintos_no_consolidan(self):
        """Con distinto nº de visitas por servicio, mejor dos visitas separadas."""
        combined, service2 = self._combo_poliza_setup()
        order = self._make_poliza([self.service, service2])
        line_b = self._service_lines(order).filtered(
            lambda l: l.product_id.product_tmpl_id == service2)
        # El servicio B queda con un solo periodo pagado de entrada; el A con dos.
        self._anticipo_lines(order).filtered(
            lambda l: l.visar_anticipo_for_line_id == line_b
        ).visar_anticipo_periods = 0

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)

        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertFalse(visits.filtered(lambda t: t.project_id == combined),
                         "consolidar solo la parte que coincide dejaría al cliente "
                         "sin las visitas de la diferencia")
        self.assertEqual(
            len(visits.filtered(lambda t: t.project_id == self.project)), 2)
        self.assertEqual(
            len(visits.filtered(lambda t: t.project_id == self.project2)), 1)

    def test_24_un_servicio_solo_no_cae_en_combinados(self):
        """Un servicio solo se queda en su proyecto: la regla exige dos orígenes."""
        combined, _service2 = self._combo_poliza_setup()
        order = self._make_poliza([self.service])
        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)

        visits = order.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        self.assertEqual(len(visits), 2)
        self.assertEqual(visits.mapped('project_id'), self.project,
                         "una fumigación sola no debe recibir la hoja del combo")
        self.assertFalse(visits.filtered(lambda t: t.project_id == combined))

    def test_25_primera_visita_consolidada_hereda_cita(self):
        """La visita consolidada recoge el horario que el cliente eligió UNA vez."""
        if 'calendar_event_id' not in self.env['sale.order.line']._fields:
            self.skipTest("website_appointment_sale no está instalado")
        _combined, service2 = self._combo_poliza_setup()
        order = self._make_poliza([self.service, service2])
        start = fields.Datetime.to_datetime('2026-01-15 16:00:00')
        stop = fields.Datetime.to_datetime('2026-01-15 18:00:00')
        event = self.env['calendar.event'].create({
            'name': 'Cita Combo Póliza Test', 'start': start, 'stop': stop})
        self._service_lines(order).calendar_event_id = event.id

        inv = order._create_invoices()
        inv.action_post()
        self._pay(inv)

        visits = order.visar_visit_ids.filtered(
            lambda t: not t.visar_is_warranty).sorted('id')
        self.assertEqual(len(visits), 2)
        self.assertEqual(visits[0].planned_date_begin, start,
                         "la 1ª visita conserva el horario de la cita")
        self.assertEqual(visits[0].date_deadline, stop)
        self.assertFalse(visits[1].planned_date_begin,
                         "la 2ª visita del ciclo se agenda después")
