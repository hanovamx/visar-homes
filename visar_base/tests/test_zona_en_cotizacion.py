# -*- coding: utf-8 -*-
"""La cotización enseña la Zona Visar del CP de la dirección de entrega."""
from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestZonaEnCotizacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zona = cls.env['visar.zone'].create({'name': 'Zona prueba', 'code': 'ZP'})
        cls.otra = cls.env['visar.zone'].create({'name': 'Zona otra', 'code': 'ZO'})
        Cp = cls.env['visar.zone.cp']
        Cp.search([('name', 'in', ('99001', '99002', '99003'))]).unlink()
        Cp.create({'name': '99001', 'zone_code': 'ZP'})
        Cp.create({'name': '99002', 'zone_code': 'ZO'})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente zona', 'zip': '99001'})
        cls.entrega = cls.env['res.partner'].create({
            'name': 'Casa de campo', 'type': 'delivery',
            'parent_id': cls.cliente.id, 'zip': '99002'})

    def _cotizacion(self, **valores):
        return self.env['sale.order'].create(dict(
            {'partner_id': self.cliente.id}, **valores))

    def test_zona_del_cp_de_la_direccion_de_entrega(self):
        orden = self._cotizacion(partner_shipping_id=self.cliente.id)
        self.assertEqual(orden.visar_zone_id, self.zona)

    def test_manda_la_entrega_no_el_cliente(self):
        orden = self._cotizacion(partner_shipping_id=self.entrega.id)
        self.assertEqual(orden.visar_zone_id, self.otra)

    def test_cambia_con_la_direccion_y_con_el_cp(self):
        orden = self._cotizacion(partner_shipping_id=self.cliente.id)
        orden.partner_shipping_id = self.entrega
        self.assertEqual(orden.visar_zone_id, self.otra)
        self.entrega.zip = ' 99001 '
        self.assertEqual(orden.visar_zone_id, self.zona)

    def test_sin_cp_o_sin_cobertura_queda_vacia(self):
        orden = self._cotizacion(partner_shipping_id=self.cliente.id)
        self.cliente.zip = False
        self.assertFalse(orden.visar_zone_id)
        self.cliente.zip = '99003'
        self.assertFalse(orden.visar_zone_id)

    def test_un_vendedor_sin_permisos_de_citas_la_ve(self):
        vendedor = self.env['res.users'].create({
            'name': 'Vendedor zona', 'login': 'vendedor_zona',
            'group_ids': [(6, 0, [self.env.ref('sales_team.group_sale_salesman').id])],
        })
        orden = self._cotizacion(partner_shipping_id=self.cliente.id,
                                 user_id=vendedor.id)
        self.assertEqual(orden.with_user(vendedor).visar_zone_id, self.zona)

    def test_el_campo_va_despues_de_la_direccion_de_entrega(self):
        arch = self.env['sale.order'].get_view(
            self.env.ref('sale.view_order_form').id, 'form')['arch']
        campos = [n.get('name') for n in etree.fromstring(arch).iter('field')]
        self.assertIn('visar_zone_id', campos)
        self.assertEqual(
            campos[campos.index('partner_shipping_id') + 1], 'visar_zone_id')
