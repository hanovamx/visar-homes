# -*- coding: utf-8 -*-
"""«Finalizar compra» pide la dirección de FACTURACIÓN, ya llena y diciéndolo.

Recorre el checkout de verdad, por HTTP y como visitante sin sesión: es la
única forma de ver lo que ve el cliente. Las pruebas a nivel de dato daban esto
por bueno mientras el formulario seguía saliendo (el estado nunca se guardaba).
"""
from odoo import http
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestCheckoutFacturacion(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env['website'].search([], limit=1)
        cls.cliente = cls.env['res.partner'].create({
            'name': "Clienta Facturación", 'email': 'facturacion@example.com',
            'phone': '8112345678'})
        cls.producto = cls.env['product.product'].create({
            'name': 'Servicio Facturación Test', 'type': 'service',
            'list_price': 1000.0, 'taxes_id': [(6, 0, [])],
            'sale_ok': True, 'website_published': True})
        cls.orden = cls.env['sale.order'].create({
            'partner_id': cls.cliente.id, 'website_id': cls.website.id,
            'order_line': [(0, 0, {'product_id': cls.producto.id,
                                   'product_uom_qty': 1})]})
        # Lo mismo que hace el wizard al guardar la dirección del servicio.
        cls.servicio = cls.orden._visar_apply_delivery_address({
            'street': 'Calle de Prueba', 'ext_num': '456',
            'neighborhood': 'Colonia Prueba', 'zip': '64000',
            'city': 'Monterrey'})
        cls.orden._visar_prefill_billing_address()

    def _visitante_con_carrito(self):
        session = self.authenticate(None, None)
        session['sale_order_id'] = self.orden.id
        http.root.session_store.save(session)

    # -- el dato -------------------------------------------------------

    def test_la_direccion_de_servicio_se_guarda_con_estado(self):
        self.assertEqual(self.servicio.state_id, self.env.ref('base.state_mx_nl'))

    def test_el_cliente_recibe_la_direccion_completa(self):
        self.assertEqual(self.cliente.street, 'Calle de Prueba No. 456')
        self.assertEqual(self.cliente.zip, '64000')
        self.assertEqual(self.cliente.state_id, self.env.ref('base.state_mx_nl'))
        self.assertTrue(self.orden.visar_billing_assumed)

    def test_no_se_pisa_la_direccion_que_el_cliente_ya_tenia(self):
        otra = self.env['res.partner'].create({
            'name': "Con domicilio", 'email': 'con@example.com',
            'phone': '8100000000', 'street': 'Su Calle 1', 'city': 'Saltillo'})
        orden = self.env['sale.order'].create({
            'partner_id': otra.id, 'website_id': self.website.id})
        orden._visar_apply_delivery_address({
            'street': 'Otra', 'ext_num': '9', 'zip': '64000', 'city': 'Monterrey'})
        self.assertFalse(orden._visar_prefill_billing_address())
        self.assertEqual(otra.street, 'Su Calle 1')
        self.assertFalse(orden.visar_billing_assumed)

    # -- lo que ve el cliente -------------------------------------------

    def test_finalizar_compra_ensena_facturacion_llena_y_rotulada(self):
        self._visitante_con_carrito()
        respuesta = self.url_open('/shop/checkout?try_skip_step=true')
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('/shop/address', respuesta.url)
        self.assertIn('address_type=billing', respuesta.url)
        html = respuesta.text
        self.assertIn('Dirección de facturación', html)
        self.assertIn('no para el servicio', html)
        # La dirección de servicio, dicha en el aviso.
        self.assertIn('Calle de Prueba No. 456, Colonia Prueba, Monterrey, 64000', html)
        # Y el formulario ya trae lo que el cliente tecleó en la reserva.
        for dato in ('Clienta Facturación', 'facturacion@example.com',
                     '8112345678', 'Calle de Prueba No. 456', '64000'):
            self.assertIn('value="%s"' % dato, html, dato)
        self.assertNotIn('Editar dirección', html)
        self.assertNotIn('Edit address', html)

    def test_solo_se_ensena_una_vez(self):
        self._visitante_con_carrito()
        self.url_open('/shop/checkout?try_skip_step=true')
        self.orden.invalidate_recordset()
        self.assertFalse(self.orden.visar_billing_assumed)
        segunda = self.url_open('/shop/checkout?try_skip_step=true')
        self.assertNotIn('/shop/address', segunda.url)
