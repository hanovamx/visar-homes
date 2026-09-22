# -*- coding: utf-8 -*-
"""El agente agenda y cobra una cotización ya hecha (22-sep-2026).

Corre contra una copia de producción: necesita la app de campo (las cotizaciones
viven ahí) y un tipo de cita de valoración con técnicos en alguna zona. Sin eso se
salta, en vez de fingir una agenda.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

TEL = '5218190001122'


@tagged('post_install', '-at_install')
class TestAgentQuote(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        if 'visar_quote_path' not in env['sale.order']._fields:
            cls.skip_all = "Sin visar_field_app no hay cotizaciones"
            return
        AptType = env['appointment.type'].sudo()
        apt = AptType._visar_get_valuation_appointment_type()
        cp = False
        for registro in env['visar.zone.cp'].sudo().search([], limit=200):
            if apt and apt._visar_eligible_resources(registro.zone_id):
                cp = registro.name
                break
        if not cp:
            cls.skip_all = "Sin tipo de cita de valoración con técnicos en alguna zona"
            return
        cls.skip_all = None
        cls.tools = env['visar.agent.tools']
        cls.cliente = env['res.partner'].create({
            'name': 'Cliente cotizacion agente', 'phone': TEL, 'zip': cp,
            'street': 'Calle 1', 'city': 'Monterrey'})
        cls.otro = env['res.partner'].create({
            'name': 'Otro cliente', 'phone': '5218190003344', 'zip': cp})
        producto = env['product.product'].create({
            'name': 'Tratamiento antitermita agente', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 0.0, 'taxes_id': [(6, 0, [])]})
        # Las cotizaciones reales copian la lista de precios del pedido de la
        # visita; sin ella REQ-004 no deja confirmar a mano (el pago sí).
        lista = env['product.pricelist'].create({'name': 'Lista cotizacion agente'})
        cls.cotizacion = env['sale.order'].create({
            'partner_id': cls.cliente.id,
            'pricelist_id': lista.id,
            'order_line': [(0, 0, {'product_id': producto.id, 'price_unit': 3700.0})],
        })
        cls.cotizacion.with_context(visar_quote_no_sync=True).write({
            'visar_quote_path': 'agendar', 'visar_quote_trigger': 'Termitas'})
        cls.cotizacion.action_quotation_sent()

    def setUp(self):
        super().setUp()
        if self.skip_all:
            self.skipTest(self.skip_all)

    def _primer_horario(self):
        dias = self.tools.agent_quote_days({'phone': TEL, 'quote_id': self.cotizacion.id})
        for dia in dias['days']:
            horarios = self.tools.agent_quote_slots({
                'phone': TEL, 'quote_id': self.cotizacion.id, 'date': dia['date']})
            if horarios['slots']:
                return horarios['slots']
        self.skipTest("La agenda de valoración no tiene horarios libres en esta copia")

    # ------------------------------------------------------------------
    def test_dias_de_la_cotizacion_con_su_total(self):
        dias = self.tools.agent_quote_days({'phone': TEL, 'quote_id': self.cotizacion.id})
        self.assertIsNone(dias['blocked'])
        self.assertEqual(dias['quote_id'], self.cotizacion.id)
        self.assertEqual(dias['total'], 3700.0)
        self.assertIn('antitermita', dias['service'].lower())

    def test_sin_id_encuentra_la_unica_cotizacion_lista(self):
        dias = self.tools.agent_quote_days({'phone': TEL})
        self.assertEqual(dias['quote_id'], self.cotizacion.id)

    def test_la_de_otro_cliente_no_existe(self):
        dias = self.tools.agent_quote_days({
            'phone': '5218190003344', 'quote_id': self.cotizacion.id})
        self.assertEqual(dias['blocked'], 'not_found')

    def test_sin_agendar_despues_no_esta_lista(self):
        self.cotizacion.with_context(visar_quote_no_sync=True).visar_quote_path = False
        dias = self.tools.agent_quote_days({'phone': TEL, 'quote_id': self.cotizacion.id})
        self.assertEqual(dias['blocked'], 'not_ready')

    def test_apartar_cuelga_la_reserva_de_la_cotizacion_y_da_la_liga(self):
        slot = self._primer_horario()[0]
        res = self.tools.agent_quote_prepare({
            'phone': TEL, 'quote_id': self.cotizacion.id,
            'slot': {'start': slot['start'], 'stop': slot['stop']}})
        self.assertTrue(res['prepared'], res)
        self.assertEqual(res['order_id'], self.cotizacion.id, "no se arma otro pedido")
        self.assertEqual(res['total'], 3700.0)
        self.assertTrue(res['payment_url'])
        reservas = self.cotizacion.order_line.calendar_booking_ids
        self.assertEqual(len(reservas), 1)
        self.assertTrue(res['hold_id'])

    def test_otro_horario_reemplaza_la_reserva_anterior(self):
        slots = self._primer_horario()
        for slot in slots[:2]:
            self.tools.agent_quote_prepare({
                'phone': TEL, 'quote_id': self.cotizacion.id,
                'slot': {'start': slot['start'], 'stop': slot['stop']}})
        self.assertEqual(len(self.cotizacion.order_line.calendar_booking_ids), 1)

    def test_soltar_borra_la_reserva_pero_no_la_cotizacion(self):
        slot = self._primer_horario()[0]
        self.tools.agent_quote_prepare({
            'phone': TEL, 'quote_id': self.cotizacion.id,
            'slot': {'start': slot['start'], 'stop': slot['stop']}})
        res = self.tools.agent_quote_release({'phone': TEL, 'quote_id': self.cotizacion.id})
        self.assertTrue(res['released'])
        self.assertFalse(self.cotizacion.order_line.calendar_booking_ids)
        self.assertEqual(self.cotizacion.state, 'sent')

    def test_pagada_se_confirma_con_su_cita(self):
        """Al confirmarse (lo hace el pago), la reserva se vuelve cita: es el
        camino nativo de `website_appointment_sale`."""
        slot = self._primer_horario()[0]
        self.tools.agent_quote_prepare({
            'phone': TEL, 'quote_id': self.cotizacion.id,
            'slot': {'start': slot['start'], 'stop': slot['stop']}})
        self.cotizacion.action_confirm()
        reserva = self.cotizacion.order_line.calendar_booking_ids
        self.assertTrue(reserva.calendar_event_id, "la reserva pagada es una cita")
        self.assertEqual(
            self.tools.agent_quote_days({'phone': TEL, 'quote_id': self.cotizacion.id})
            ['blocked'], 'paid')
