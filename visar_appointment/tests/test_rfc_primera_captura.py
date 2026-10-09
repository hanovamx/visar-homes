# -*- coding: utf-8 -*-
"""El RFC se puede capturar por primera vez aunque el cliente ya tenga pedidos.

El paso "Información de facturación" del checkout deja el RFC de solo lectura
cuando `can_edit_vat` es falso. Un cliente que ya compró sin factura y ahora la
pide quedaba con el campo vacío y bloqueado.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestRfcPrimeraCaptura(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cliente = cls.env['res.partner'].create({'name': "Cliente RFC"})
        producto = cls.env['product.product'].create({
            'name': 'Servicio RFC Test', 'type': 'service', 'list_price': 100.0})
        orden = cls.env['sale.order'].create({
            'partner_id': cls.cliente.id,
            'order_line': [(0, 0, {'product_id': producto.id, 'product_uom_qty': 1})]})
        # El candado del core solo mira el estado; confirmar de verdad exige la
        # lista de precios de la zona y aquí no viene al caso.
        orden.state = 'sale'

    def test_sin_rfc_se_puede_capturar_aunque_ya_compro(self):
        self.assertTrue(self.cliente.can_edit_vat())

    def test_con_rfc_y_pedidos_sigue_bloqueado(self):
        """Cambiar un RFC con documentos emitidos sigue siendo cosa de Visar."""
        self.cliente.vat = 'XAXX010101000'
        self.assertFalse(self.cliente.can_edit_vat())

    def test_un_contacto_hijo_no_lo_captura(self):
        hijo = self.env['res.partner'].create({
            'name': "Facturación", 'type': 'invoice', 'parent_id': self.cliente.id})
        self.assertFalse(hijo.can_edit_vat())

    def test_un_cliente_nuevo_sigue_pudiendo(self):
        nuevo = self.env['res.partner'].create({'name': "Nuevo", 'vat': 'XAXX010101000'})
        self.assertTrue(nuevo.can_edit_vat())
