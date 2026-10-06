# -*- coding: utf-8 -*-
"""Paso de productos adicionales de la cita web: textos y descripción por producto."""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestExtrasDescripcion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tmpl = cls.env['product.template']
        cls.lista = cls.env['product.pricelist'].create({'name': 'Lista extras'})
        cls.zona = cls.env['visar.zone'].create({
            'name': 'Zona extras', 'code': 'ZX', 'pricelist_id': cls.lista.id})
        cls.con_texto = Tmpl.create({
            'name': 'Adicional con texto', 'list_price': 150.0,
            'visar_addon_description': '  Trampa de pegamento para cocina.  '})
        cls.sin_texto = Tmpl.create({
            'name': 'Adicional sin texto', 'list_price': 90.0})
        cls.servicio = Tmpl.create({
            'name': 'Servicio con adicionales', 'list_price': 500.0,
            'optional_product_ids': [(6, 0, (cls.con_texto | cls.sin_texto).ids)]})
        cls.servicio.visar_optional_line_ids.write({'is_mandatory': False})

    def _ofertas(self):
        ofertas = self.env['appointment.type']._visar_offered_addons(
            [{'product_tmpl_id': self.servicio.id}], self.zona)
        return {o['template_id']: o for o in ofertas}

    def test_la_oferta_lleva_la_descripcion_del_producto(self):
        ofertas = self._ofertas()
        self.assertEqual(ofertas[self.con_texto.id]['description'],
                         'Trampa de pegamento para cocina.')

    def test_sin_descripcion_queda_vacia(self):
        self.assertEqual(self._ofertas()[self.sin_texto.id]['description'], '')

    def test_textos_del_paso(self):
        arch = self.env.ref('visar_appointment.visar_wizard_extras').arch_db
        self.assertIn('Productos adicionales recomendados', arch)
        self.assertIn('Te sugerimos agregar estos productos para complementar '
                      'el servicio.', arch)
        self.assertNotIn('¿Deseas agregar algo más?', arch)
        self.assertNotIn('Con base en', arch)
        self.assertIn("offer.get('description')", arch)
