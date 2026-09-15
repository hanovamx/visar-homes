# -*- coding: utf-8 -*-
"""REQ-004: fuera del sitio web, un cliente sin lista propia no "tiene" ninguna.

Odoo calculaba la lista del cliente y, sin una propia, le ponía la primera lista
activa ("VISAR Zona A" en producción). Cada prueba crea su propia "primera lista"
para no depender del catálogo de la base.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

WEBSITE = 'odoo.addons.website.models.ir_http.get_request_website'


@tagged('post_install', '-at_install')
class TestSinListaPorDefecto(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('product.group_product_pricelist')
        # La que Odoo tomaría como default: la de menor sequence.
        cls.primera = cls.env['product.pricelist'].create({
            'name': 'Primera REQ-004', 'sequence': 1})
        cls.zona = cls.env['product.pricelist'].create({
            'name': 'Zona REQ-004', 'sequence': 50,
            'item_ids': [(0, 0, {'compute_price': 'fixed', 'fixed_price': 777.0,
                                 'applied_on': '3_global'})]})
        cls.producto = cls.env['product.product'].create({
            'name': 'Servicio REQ-004', 'type': 'service', 'list_price': 100.0})

    def test_un_cliente_nuevo_no_trae_lista(self):
        cliente = self.env['res.partner'].create({'name': 'Nuevo REQ-004'})
        self.assertFalse(cliente.property_product_pricelist)

    def test_la_cotizacion_nueva_tampoco(self):
        cliente = self.env['res.partner'].create({'name': 'Nuevo REQ-004'})
        orden = self.env['sale.order'].create({'partner_id': cliente.id})
        self.assertFalse(orden.pricelist_id)

    def test_la_lista_elegida_se_respeta_aunque_sea_la_primera(self):
        """El nativo no guarda la lista si coincide con el default: sin default,
        elegir la primera a propósito se perdía."""
        cliente = self.env['res.partner'].create({
            'name': 'Con lista REQ-004', 'property_product_pricelist': self.primera.id})
        cliente.invalidate_recordset()
        self.assertEqual(cliente.property_product_pricelist, self.primera)
        orden = self.env['sale.order'].create({'partner_id': cliente.id})
        self.assertEqual(orden.pricelist_id, self.primera)

    def test_quitarle_la_lista_a_un_cliente_la_deja_vacia(self):
        cliente = self.env['res.partner'].create({
            'name': 'Con lista REQ-004', 'property_product_pricelist': self.zona.id})
        cliente.property_product_pricelist = False
        cliente.invalidate_recordset()
        self.assertFalse(cliente.property_product_pricelist)

    def test_en_el_sitio_web_se_conserva_el_comportamiento_de_odoo(self):
        """La tienda y el wizard necesitan una lista; ahí la zona pone la buena."""
        cliente = self.env['res.partner'].create({'name': 'Web REQ-004'})
        website = self.env['website'].search([], limit=1)
        with patch(WEBSITE, return_value=website):
            cliente.invalidate_recordset()
            self.assertTrue(cliente.property_product_pricelist)

    def test_de_punta_a_punta(self):
        """Cliente nuevo → cotización → confirmar falla → elegir lista →
        actualizar precios → confirmar."""
        cliente = self.env['res.partner'].create({'name': 'Punta a punta REQ-004'})
        orden = self.env['sale.order'].create({
            'partner_id': cliente.id,
            'order_line': [(0, 0, {'product_id': self.producto.id})]})
        self.assertFalse(orden.pricelist_id)
        self.assertEqual(orden.order_line.price_unit, 100.0)

        with self.assertRaisesRegex(UserError, "lista de precios"):
            orden.action_confirm()

        orden.pricelist_id = self.zona
        orden.action_update_prices()
        self.assertEqual(orden.order_line.price_unit, 777.0)

        orden.action_confirm()
        self.assertEqual(orden.state, 'sale')
