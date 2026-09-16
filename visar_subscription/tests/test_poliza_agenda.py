"""Rezago de visitas de póliza: tipo de visita, numeración y fecha propuesta.

Lo que se protege aquí es la cuenta de lo que Visar le debe a cada cliente. Los casos
no son hipótesis: salen de la base de producción, donde hay 149 visitas de póliza
abiertas sin fecha y NINGUNA visita posterior a la primera ha recibido fecha nunca.
"""
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPolizaAgenda(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente Agenda Test'})
        company = cls.env.company
        cls.project = cls.env['project.project'].create({
            'name': 'FSM Agenda Test', 'is_fsm': True, 'company_id': company.id})
        # Plan anual de un solo pago con 12 visitas: la forma más limpia de tener una
        # serie larga en un solo ciclo, y el plan real que motivó `visar_included_visits`.
        cls.plan = cls.env['sale.subscription.plan'].create({
            'name': 'Plan Agenda Test',
            'billing_period_value': 1, 'billing_period_unit': 'year',
            'visar_first_invoice_periods': 1, 'visar_commitment_months': 0,
            'visar_included_visits': 12})
        cls.service = cls.env['product.template'].create({
            'name': 'Servicio Agenda Test', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 100.0,
            'recurring_invoice': True, 'allow_one_time_sale': True,
            'visar_generates_visit': True,
            'visar_fsm_project_id': cls.project.id, 'taxes_id': [(6, 0, [])]})

    def _make_poliza(self, plan=None, start=date(2026, 1, 1)):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'plan_id': (plan or self.plan).id,
            'pricelist_id': self.env['product.pricelist'].create(
                {'name': 'Lista agenda test'}).id,
            'start_date': start,
            'order_line': [(0, 0, {'product_id': self.service.product_variant_id.id,
                                   'product_uom_qty': 1})]})
        order._visar_sync_anticipo_lines()
        order.require_payment = False
        order.action_confirm()
        return order

    def _con_visitas(self, plan=None):
        """Póliza con su primer ciclo pagado, es decir con sus 12 visitas creadas."""
        order = self._make_poliza(plan=plan)
        invoice = order._create_invoices()
        invoice.action_post()
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids
        ).create({})._create_payments()
        return order

    def _agendar(self, visita, cuando):
        """Agenda una visita como lo hace el despacho.

        Odoo DESCARTA un `planned_date_begin` que llegue sin su fecha de fin, sin decir
        nada: hay que escribir las dos o la visita se queda sin fecha y la serie sin
        ancla.
        """
        visita.write({'planned_date_begin': cuando,
                      'date_deadline': cuando + timedelta(hours=1)})
        return visita

    def _serie(self, order):
        return order.visar_visit_ids.filtered(
            lambda t: t.visar_visit_kind == 'preventiva').sorted('id')

    # ------------------------------------------------------------------
    def test_01_numeracion_en_campos(self):
        """El «3 de 12» deja de vivir solo dentro del título."""
        order = self._con_visitas()
        serie = self._serie(order)
        self.assertEqual(len(serie), 12)
        self.assertEqual(serie.mapped('visar_visit_seq'), list(range(1, 13)))
        self.assertEqual(set(serie.mapped('visar_visit_total')), {12})

    def test_02_tipo_manda_sobre_el_booleano_de_garantia(self):
        order = self._con_visitas()
        visita = self._serie(order)[0]
        self.assertFalse(visita.visar_is_warranty)

        order.action_visar_add_warranty_visit()
        garantia = order.visar_visit_ids.filtered('visar_is_warranty')
        self.assertEqual(len(garantia), 1)
        self.assertEqual(garantia.visar_visit_kind, 'garantia')
        self.assertFalse(garantia.visar_visit_seq,
                         "una visita adicional no lleva número de serie")

        # Escribir el booleano —lo que hace el código anterior a los tipos— sigue
        # moviendo el tipo, que es la fuente.
        garantia.visar_is_warranty = False
        self.assertEqual(garantia.visar_visit_kind, 'preventiva')

    def test_03_la_serie_se_ancla_en_la_primera_visita_real(self):
        """No en la factura ni en el pago: en el día que el cliente eligió."""
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))

        self.assertFalse(serie[0].visar_visit_due_date, "la agendada no necesita propuesta")
        self.assertEqual(serie[1].visar_visit_due_date, date(2026, 4, 20))
        self.assertEqual(serie[2].visar_visit_due_date, date(2026, 5, 20))
        self.assertEqual(serie[11].visar_visit_due_date, date(2027, 2, 20))

    def test_04_agendar_una_repropone_las_siguientes(self):
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))
        # El cliente agenda la segunda tarde, el día 28 en vez del 20.
        self._agendar(serie[1], datetime(2026, 4, 28, 16, 0))

        self.assertEqual(serie[2].visar_visit_due_date, date(2026, 5, 28),
                         "la siguiente cuelga de la fecha REAL, no de la propuesta")
        self.assertEqual(serie[3].visar_visit_due_date, date(2026, 6, 28))

    def test_05_la_correctiva_no_recorre_la_serie_ni_cuenta_como_garantia(self):
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))
        tasa_antes = order.visar_warranty_rate

        order.action_visar_add_corrective_visit()
        refuerzo = order.visar_visit_ids.filtered(
            lambda t: t.visar_visit_kind == 'correctiva')
        self.assertEqual(len(refuerzo), 1)
        self._agendar(refuerzo, datetime(2026, 3, 27, 16, 0))

        self.assertEqual(self._serie(order)[1].visar_visit_due_date, date(2026, 4, 20),
                         "el refuerzo no adelanta la serie que el cliente compró")
        self.assertFalse(refuerzo.visar_is_warranty)
        self.assertEqual(order.visar_warranty_rate, tasa_antes,
                         "un refuerzo no es un fallo: no sube la siniestralidad")
        self.assertFalse(refuerzo.visar_visit_due_date)

    def test_06_lo_que_no_cabe_en_la_vigencia_se_marca_no_se_fecha(self):
        order = self._con_visitas()
        order.end_date = date(2026, 6, 30)
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))

        self.assertEqual(serie[3].visar_visit_due_date, date(2026, 6, 20))
        self.assertFalse(serie[4].visar_visit_due_date)
        self.assertTrue(serie[4].visar_visit_due_out_of_term)
        self.assertTrue(serie[11].visar_visit_due_out_of_term)

    def test_07_sin_ancla_no_se_inventa_origen(self):
        order = self._con_visitas()
        serie = self._serie(order)
        self.assertFalse(any(serie.mapped('planned_date_begin')))
        self.assertFalse(any(serie.mapped('visar_visit_due_date')))
        self.assertFalse(any(serie.mapped('visar_visit_due_out_of_term')))

    def test_08_una_fecha_escrita_a_mano_manda_y_ancla(self):
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))

        serie[2].visar_visit_due_date = date(2026, 7, 1)
        self.assertTrue(serie[2].visar_visit_due_manual)

        order.action_visar_recompute_visit_due_dates()
        self.assertEqual(serie[2].visar_visit_due_date, date(2026, 7, 1),
                         "el recálculo no pisa lo que puso una persona")
        self.assertEqual(serie[3].visar_visit_due_date, date(2026, 8, 1),
                         "y las siguientes cuelgan de ella")

    def test_09_los_meses_entre_visitas_salen_del_plan(self):
        plan = self.plan.copy({'name': 'Plan Agenda Bimestral Test',
                               'visar_visit_interval_months': 2})
        order = self._con_visitas(plan=plan)
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))
        self.assertEqual(serie[1].visar_visit_due_date, date(2026, 5, 20))

        plan.visar_visit_interval_months = 0
        order.action_visar_recompute_visit_due_dates()
        self.assertFalse(self._serie(order)[1].visar_visit_due_date,
                         "0 = este plan no propone fechas")

    def test_10_la_visita_cancelada_sale_de_la_serie(self):
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))
        serie[1].state = '1_canceled'
        order.action_visar_recompute_visit_due_dates()

        self.assertEqual(serie[2].visar_visit_due_date, date(2026, 4, 20),
                         "la cancelada no ocupa su turno en la serie")

    def test_11_arranque_correctivo_se_deduce_del_guion(self):
        """El par preventivo/correctivo solo existe como respuesta de la cita."""
        if 'appointment.answer.input' not in self.env:
            self.skipTest("El módulo appointment no está instalado")
        order = self._make_poliza()
        self.assertFalse(order.visar_corrective_start)

        pregunta = self.env['appointment.question'].create({
            'name': '¿Tienes plaga o es preventivo?', 'question_type': 'text'})
        tipo = self.env['appointment.type'].create({
            'name': 'Cita Agenda Test',
            'question_ids': [(6, 0, pregunta.ids)]})
        evento = self.env['calendar.event'].create({
            'name': 'Cita Agenda Test',
            'start': fields.Datetime.now(),
            'stop': fields.Datetime.now() + timedelta(hours=1)})
        self.env['appointment.answer.input'].create({
            'question_id': pregunta.id, 'calendar_event_id': evento.id,
            'appointment_type_id': tipo.id,
            'value_text_box': 'Correctivo (plaga activa)'})
        linea = order.order_line.filtered(lambda l: l.recurring_invoice)[:1]
        linea.calendar_event_id = evento.id
        order.invalidate_recordset(['visar_corrective_start'])

        self.assertTrue(order.visar_corrective_start)
        # Y se puede corregir a mano: el técnico encuentra otra cosa.
        order.visar_corrective_start = False
        order.invalidate_recordset(['visar_corrective_start'])
        self.assertFalse(order.visar_corrective_start,
                         "lo escrito a mano no lo pisa el siguiente recálculo")

    def test_12_recalcular_es_idempotente(self):
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))
        antes = serie.mapped('visar_visit_due_date')

        order.action_visar_recompute_visit_due_dates()
        order.action_visar_recompute_visit_due_dates()

        self.assertEqual(self._serie(order).mapped('visar_visit_due_date'), antes)
        self.assertEqual(
            serie[1].visar_visit_due_date,
            fields.Date.to_date(serie[0].planned_date_begin) + relativedelta(months=1))

    def test_13_una_serie_por_servicio_no_una_por_poliza(self):
        """Dos servicios que no se consolidan tienen dos series, no una cadena de 24."""
        proyecto2 = self.env['project.project'].create({
            'name': 'FSM Agenda Test 2', 'is_fsm': True,
            'company_id': self.env.company.id})
        servicio2 = self.env['product.template'].create({
            'name': 'Servicio Agenda Test 2', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 100.0,
            'recurring_invoice': True, 'allow_one_time_sale': True,
            'visar_generates_visit': True,
            'visar_fsm_project_id': proyecto2.id, 'taxes_id': [(6, 0, [])]})
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'plan_id': self.plan.id,
            'pricelist_id': self.env['product.pricelist'].create(
                {'name': 'Lista agenda test 2'}).id,
            'start_date': date(2026, 1, 1),
            'order_line': [
                (0, 0, {'product_id': self.service.product_variant_id.id,
                        'product_uom_qty': 1}),
                (0, 0, {'product_id': servicio2.product_variant_id.id,
                        'product_uom_qty': 1})]})
        order._visar_sync_anticipo_lines()
        order.require_payment = False
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids
        ).create({})._create_payments()

        serie = self._serie(order)
        self.assertEqual(len(serie), 24, "12 visitas por cada servicio")
        cadenas = {}
        for visita in serie:
            cadenas.setdefault(visita.visar_source_line_id.id, []).append(visita)
        self.assertEqual(len(cadenas), 2)

        for cadena in cadenas.values():
            self._agendar(cadena[0], datetime(2026, 3, 20, 16, 0))
        for cadena in cadenas.values():
            propuestas = [v.visar_visit_due_date for v in cadena[1:]]
            self.assertEqual(propuestas[0], date(2026, 4, 20))
            self.assertEqual(propuestas[-1], date(2027, 2, 20),
                             "11 pendientes = 11 meses, no 23")
            self.assertEqual(len(set(propuestas)), len(propuestas))

    def test_14_la_serie_no_retrocede_ni_propone_dos_veces_el_mismo_mes(self):
        """Una visita de más adelante agendada antes no hace retroceder la cadena."""
        order = self._con_visitas()
        serie = self._serie(order)
        self._agendar(serie[0], datetime(2026, 3, 20, 16, 0))
        # El despacho agenda la séptima para abril, antes de que le toque.
        self._agendar(serie[6], datetime(2026, 4, 10, 16, 0))

        propuestas = [v.visar_visit_due_date for v in serie if v.visar_visit_due_date]
        self.assertEqual(len(set(propuestas)), len(propuestas),
                         "ninguna fecha propuesta se repite")
        self.assertEqual(propuestas, sorted(propuestas), "la serie va hacia adelante")
        self.assertEqual(serie[7].visar_visit_due_date, date(2026, 9, 20),
                         "sigue desde donde iba la cadena, no desde abril")
