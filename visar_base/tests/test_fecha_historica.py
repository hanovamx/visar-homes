# -*- coding: utf-8 -*-
"""Pedidos históricos: la fecha de la orden es la real (27-oct-2026, S00002)."""
from datetime import datetime, timedelta

from lxml import etree

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestFechaHistorica(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'America/Monterrey'
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente histórico'})
        cls.producto = cls.env['product.product'].create({
            'name': 'Servicio histórico', 'type': 'service', 'list_price': 100,
            'invoice_policy': 'order'})
        cls.lista = cls.env['product.pricelist'].create({'name': 'Lista histórica'})

    def _pedido(self, **valores):
        return self.env['sale.order'].create(dict({
            'partner_id': self.cliente.id, 'pricelist_id': self.lista.id,
            'order_line': [(0, 0, {'product_id': self.producto.id})]}, **valores))

    def _facturar(self, orden, dia):
        factura = orden._create_invoices()
        factura.invoice_date = dia
        factura.action_post()
        return factura

    def test_confirmar_hoy_pone_la_fecha_de_hoy(self):
        orden = self._pedido(date_order=fields.Datetime.now() - timedelta(hours=1))
        orden.action_confirm()
        self.assertEqual(orden.date_order.date(), fields.Datetime.now().date())

    def test_una_fecha_historica_se_respeta_al_confirmar(self):
        junio = datetime(2026, 6, 10, 18, 0)
        orden = self._pedido(date_order=junio)
        orden.action_confirm()
        self.assertEqual(orden.state, 'sale')
        self.assertEqual(orden.date_order, junio)

    def test_la_factura_anterior_lleva_la_orden_a_su_fecha(self):
        """El caso de S00002: capturada hoy, factura del 10 de junio."""
        orden = self._pedido()
        orden.action_confirm()
        self._facturar(orden, fields.Date.to_date('2026-06-10'))
        # Mediodía de Monterrey = 18:00 UTC, el mismo día.
        self.assertEqual(orden.date_order, datetime(2026, 6, 10, 18, 0))
        self.assertEqual(orden.state, 'sale')

    def test_una_factura_posterior_no_la_mueve(self):
        orden = self._pedido()
        orden.action_confirm()
        antes = orden.date_order
        self._facturar(orden, fields.Date.context_today(orden) + timedelta(days=3))
        self.assertEqual(orden.date_order, antes)

    def test_el_formulario_ensena_solo_el_dia(self):
        arch = etree.fromstring(self.env['sale.order'].get_view(
            self.env.ref('sale.view_order_form').id, 'form')['arch'])
        campos = arch.xpath("//group[@name='order_details']/field[@name='date_order']")
        self.assertEqual(len(campos), 2)
        self.assertTrue(all(c.get('widget') == 'date' for c in campos))
