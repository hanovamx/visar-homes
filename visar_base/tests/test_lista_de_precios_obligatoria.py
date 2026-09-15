# -*- coding: utf-8 -*-
"""REQ-004: no se confirma una cotización sin lista de precios.

Y la excepción que importa tanto como la regla: un PAGO en línea sí confirma. El
nativo llama a `action_confirm` desde el pago sin atrapar errores; si la regla
saltara ahí, el cliente quedaría cobrado y sin pedido (REQ-002).
"""
from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestListaDePreciosObligatoria(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente REQ-004'})
        cls.producto = cls.env['product.product'].create({
            'name': 'Producto REQ-004', 'type': 'consu', 'list_price': 100.0})
        cls.lista = cls.env['product.pricelist'].create({'name': 'Lista REQ-004'})

    def _cotizacion(self, **valores):
        orden = self.env['sale.order'].create(dict({
            'partner_id': self.cliente.id,
            'order_line': [(0, 0, {'product_id': self.producto.id})],
        }, **valores))
        # El default se quita en `visar_appointment`; aquí se prueba la regla
        # sola, así que se vacía a mano.
        if 'pricelist_id' not in valores:
            orden.pricelist_id = False
        return orden

    def test_sin_lista_no_se_confirma(self):
        orden = self._cotizacion()
        with self.assertRaisesRegex(UserError, "lista de precios"):
            orden.action_confirm()
        self.assertEqual(orden.state, 'draft')

    def test_con_lista_se_confirma(self):
        orden = self._cotizacion(pricelist_id=self.lista.id)
        orden.action_confirm()
        self.assertEqual(orden.state, 'sale')

    def test_un_pago_en_linea_si_confirma_y_deja_nota(self):
        orden = self._cotizacion()
        proveedor = self.env['payment.provider'].search([('code', '=', 'demo')], limit=1)
        if not proveedor:
            self.skipTest("sin proveedor de pago demo en esta base")
        metodo = self.env['payment.method'].search([('code', '=', 'demo')], limit=1) \
            or proveedor.payment_method_ids[:1]
        pago = self.env['payment.transaction'].create({
            'provider_id': proveedor.id,
            'payment_method_id': metodo.id,
            'amount': orden.amount_total,
            'currency_id': orden.currency_id.id,
            'partner_id': self.cliente.id,
            'reference': 'REQ-004-%s' % orden.id,
            'sale_order_ids': [(6, 0, orden.ids)],
        })
        pago._set_done()

        pago._check_amount_and_confirm_order()

        self.assertEqual(orden.state, 'sale')
        self.assertTrue(orden.message_ids.filtered(
            lambda m: 'sin lista de precios' in str(m.body)))

    def test_el_boton_de_actualizar_precios_avisa_y_no_se_esconde(self):
        arch = etree.fromstring(
            self.env['sale.order'].get_view(view_type='form')['arch'])
        boton = arch.xpath("//button[@name='action_update_prices']")[0]
        self.assertIn('descuentos', boton.get('confirm'))
        self.assertNotIn('show_update_pricelist', boton.get('invisible'))
