# -*- coding: utf-8 -*-
"""Vendedor como empleado en la cotización, al lado del Vendedor estándar.

El requerimiento tiene una condición que pesa más que el campo: el `user_id`
estándar NO se toca, porque lo usan `sale_commission` y las reglas de "solo mis
documentos". Por eso la mitad de estas pruebas son negativas: el campo nuevo no
mueve al estándar, ni al revés, y la cotización se confirma igual.
"""
from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestVendedorEmpleado(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # El caso que motiva el campo: un vendedor SIN usuario de Odoo.
        cls.empleado = cls.env['hr.employee'].create({'name': 'Vendedora Sin Usuario'})
        # Un vendedor normal, sin ningún permiso de RH. Si él no puede elegir un
        # empleado, el campo no le sirve a quien lo va a capturar.
        cls.vendedor = new_test_user(
            cls.env, login='vendedor_sin_rh', groups='sales_team.group_sale_salesman')
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente Cotización'})
        cls.producto = cls.env['product.product'].create({
            'name': 'Producto de prueba', 'type': 'consu', 'list_price': 100.0})

    def _cotizacion(self, env=None, **valores):
        return (env or self.env)['sale.order'].create(dict({
            'partner_id': self.cliente.id,
            'order_line': [(0, 0, {'product_id': self.producto.id,
                                   'product_uom_qty': 1})],
        }, **valores))

    def test_se_asigna_un_empleado_sin_usuario(self):
        self.assertFalse(self.empleado.user_id)
        orden = self._cotizacion(visar_salesperson_employee_id=self.empleado.id)
        self.assertEqual(orden.visar_salesperson_employee_id, self.empleado)

    def test_no_mueve_al_vendedor_estandar_ni_al_reves(self):
        orden = self._cotizacion(user_id=self.vendedor.id)
        orden.visar_salesperson_employee_id = self.empleado
        self.assertEqual(orden.user_id, self.vendedor)

        orden.user_id = self.env.user
        self.assertEqual(orden.visar_salesperson_employee_id, self.empleado)

    def test_un_vendedor_sin_permisos_de_rh_lo_captura_y_lo_ve(self):
        env = self.env(user=self.vendedor)
        self.assertFalse(self.vendedor.has_group('hr.group_hr_user'))
        encontrados = env['hr.employee'].name_search('Vendedora Sin')
        self.assertIn(self.empleado.id, [i for i, _n in encontrados])

        orden = self._cotizacion(env=env, user_id=self.vendedor.id)
        orden.visar_salesperson_employee_id = self.empleado.id
        self.assertEqual(orden.visar_salesperson_employee_id.display_name,
                         'Vendedora Sin Usuario')

    def test_la_cotizacion_se_confirma_igual(self):
        orden = self._cotizacion(user_id=self.vendedor.id,
                                 visar_salesperson_employee_id=self.empleado.id)
        orden.action_confirm()
        self.assertEqual(orden.state, 'sale')
        self.assertEqual(orden.user_id, self.vendedor)
        self.assertEqual(orden.visar_salesperson_employee_id, self.empleado)

    def test_es_opcional(self):
        orden = self._cotizacion()
        orden.action_confirm()
        self.assertEqual(orden.state, 'sale')
        self.assertFalse(orden.visar_salesperson_employee_id)

    def test_esta_en_otra_informacion_debajo_del_vendedor_estandar(self):
        arch = etree.fromstring(
            self.env['sale.order'].get_view(view_type='form')['arch'])
        grupo = arch.xpath("//page[@name='other_information']"
                           "//group[@name='sales_person']")
        self.assertTrue(grupo)
        nombres = [f.get('name') for f in grupo[0].xpath('./field')]
        self.assertIn('user_id', nombres, "el Vendedor estándar sigue visible")
        self.assertEqual(
            nombres[nombres.index('user_id') + 1], 'visar_salesperson_employee_id')
        estandar = grupo[0].xpath("./field[@name='user_id']")[0]
        self.assertNotIn(estandar.get('invisible'), ('1', 'True', 'true'))

    def test_el_cambio_queda_en_el_chatter(self):
        """Va a ser la base de una comision: quien lo cambio y cuando tiene que
        quedar a la vista."""
        orden = self._cotizacion()
        # Odoo no rastrea cambios de un registro creado en la misma transaccion:
        # se cierra la de la creacion, como pasa en la vida real.
        self.env.cr.precommit.run()
        orden.visar_salesperson_employee_id = self.empleado
        self.env.cr.precommit.run()
        cambios = orden.message_ids.tracking_value_ids.filtered(
            lambda t: t.field_id.name == 'visar_salesperson_employee_id')
        self.assertEqual(cambios.new_value_char, 'Vendedora Sin Usuario')
