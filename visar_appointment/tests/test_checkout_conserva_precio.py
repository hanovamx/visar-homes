# -*- coding: utf-8 -*-
"""El checkout de eCommerce no cambia el precio ni vuelve a pedir la dirección.

Visar reportó (5-oct-2026) que una póliza cotizada en el wizard en $3,990 subía
de precio al elegir "Finalizar compra" y llenar la dirección. La causa: cada vez
que el checkout escribe el cliente o la dirección de facturación, Odoo recalcula
la lista de precios DESDE EL PARTNER y reprecia. Un cliente sin lista propia cae
en la general del sitio y la póliza pierde su lista (zona × plan).

No se había visto porque las pruebas se pagaban con el botón exprés de
"Demostración", que se salta los pasos de dirección.

`_update_address` lee la sesión web, así que se llama dentro de un `MockRequest`:
es la misma llamada que hace `/shop/address/submit`.
"""
from odoo.addons.website_sale.tests.common import MockRequest
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCheckoutConservaPrecio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env['website'].search([], limit=1)
        cls.cliente = cls.env['res.partner'].create({
            'name': "Cliente Checkout", 'email': 'checkout@example.com',
            'phone': '8112345678'})
        cls.servicio = cls.env['res.partner'].create({
            'name': "Cliente Checkout", 'type': 'delivery',
            'parent_id': cls.cliente.id, 'street': 'Calle Falsa No. 123',
            'street2': 'Centro', 'zip': '64000', 'city': 'Monterrey',
            'state_id': cls.env.ref('base.state_mx_nl').id,
            'country_id': cls.env.ref('base.mx').id})
        cls.producto = cls.env['product.product'].create({
            'name': 'Servicio Checkout Test', 'type': 'service',
            'list_price': 1000.0, 'taxes_id': [(6, 0, [])]})
        # La lista "de la póliza": más barata que la general, como en producción.
        cls.lista_plan = cls.env['product.pricelist'].create({
            'name': 'Lista plan Checkout Test', 'website_id': cls.website.id,
            'item_ids': [(0, 0, {
                'applied_on': '0_product_variant', 'product_id': cls.producto.id,
                'compute_price': 'fixed', 'fixed_price': 800.0})]})

    def _orden(self, con_servicio=True):
        orden = self.env['sale.order'].create({
            'partner_id': self.cliente.id, 'website_id': self.website.id,
            'pricelist_id': self.lista_plan.id,
            'order_line': [(0, 0, {'product_id': self.producto.id,
                                   'product_uom_qty': 1})]})
        linea = orden.order_line
        # Descuento de combo escrito a mano, como hace el wizard.
        linea.write({'discount': 50.0})
        self.assertEqual(linea.price_unit, 800.0)
        if con_servicio:
            orden._visar_set_service_shipping(self.servicio)
        return orden

    def _checkout_escribe_el_cliente(self, orden, fnames):
        with MockRequest(self.env, website=self.website, sale_order_id=orden.id):
            orden._update_address(self.cliente.id, fnames)

    # ------------------------------------------------------------------
    # El precio
    # ------------------------------------------------------------------

    def test_sin_la_guardia_el_checkout_si_cambia_la_lista(self):
        """El fallo existe: si esto dejara de pasar, las otras no probarían nada."""
        orden = self._orden(con_servicio=False)
        self._checkout_escribe_el_cliente(orden, ['partner_id'])
        self.assertNotEqual(orden.pricelist_id, self.lista_plan)

    def test_llenar_la_direccion_no_cambia_la_lista_ni_el_precio(self):
        orden = self._orden()
        total = orden.amount_total
        self._checkout_escribe_el_cliente(orden, ['partner_id', 'partner_invoice_id'])
        self.assertEqual(orden.pricelist_id, self.lista_plan)
        self.assertEqual(orden.order_line.price_unit, 800.0)
        self.assertEqual(orden.amount_total, total)

    def test_el_descuento_de_combo_sobrevive(self):
        """Repreciar con la lista buena lo borraría: por eso se restaura la foto."""
        orden = self._orden()
        self._checkout_escribe_el_cliente(orden, ['partner_id'])
        self.assertEqual(orden.order_line.discount, 50.0)
        self.assertEqual(orden.amount_total, 400.0)

    def test_la_direccion_de_servicio_sigue_bloqueada(self):
        orden = self._orden()
        self._checkout_escribe_el_cliente(orden, ['partner_shipping_id'])
        self.assertEqual(orden.partner_shipping_id, self.servicio)

    # ------------------------------------------------------------------
    # La dirección de facturación
    # ------------------------------------------------------------------

    def test_el_cliente_sin_domicilio_recibe_el_de_servicio(self):
        orden = self._orden()
        self.assertTrue(orden._visar_prefill_billing_address())
        for campo in ('street', 'street2', 'zip', 'city', 'state_id', 'country_id'):
            self.assertEqual(self.cliente[campo], self.servicio[campo], campo)

    def test_un_domicilio_que_ya_existe_no_se_toca(self):
        self.cliente.write({'street': 'Av. Propia 1', 'city': 'Guadalupe'})
        orden = self._orden()
        self.assertFalse(orden._visar_prefill_billing_address())
        self.assertEqual(self.cliente.street, 'Av. Propia 1')
        self.assertEqual(self.cliente.city, 'Guadalupe')
        self.assertFalse(self.cliente.zip, "ni siquiera se completa lo que falta")

    def test_sin_direccion_de_servicio_no_hace_nada(self):
        orden = self._orden(con_servicio=False)
        self.assertFalse(orden._visar_prefill_billing_address())
        self.assertFalse(self.cliente.street)
