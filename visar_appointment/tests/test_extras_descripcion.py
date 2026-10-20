# -*- coding: utf-8 -*-
"""Paso de productos adicionales de la cita web: textos y descripción por producto."""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import format_amount


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

    def _oferta(self, descripcion):
        return {'description': descripcion, 'subtotal': 150.0, 'quantity': 1,
                'currency_id': self.env.company.currency_id.id}

    def test_el_agente_dice_que_es_y_luego_el_precio(self):
        flujo = self.env['appointment.type']
        oferta = self._oferta('Trampa de pegamento\npara cocina')
        self.assertEqual(
            flujo._visar_wizard_extra_description(oferta),
            'Trampa de pegamento para cocina. %s'
            % flujo._visar_wizard_extra_price_text(oferta))

    def test_el_agente_sin_descripcion_solo_dice_el_precio(self):
        flujo = self.env['appointment.type']
        oferta = self._oferta('')
        self.assertEqual(flujo._visar_wizard_extra_description(oferta),
                         flujo._visar_wizard_extra_price_text(oferta))

    def test_el_paso_del_agente_usa_los_textos_de_la_web(self):
        paso = self.env['appointment.type']._visar_wizard_step_options(
            {'zone_id': self.zona.id,
             'items': [{'product_tmpl_id': self.servicio.id}]}, 'extras')
        self.assertEqual(paso['title'], 'Productos adicionales recomendados')
        self.assertEqual(paso['hint'], 'Te sugerimos agregar estos productos '
                                       'para complementar el servicio.')
        por_nombre = {o['label']: o for o in paso['options']}
        self.assertTrue(por_nombre['Adicional con texto']['description']
                        .startswith('Trampa de pegamento para cocina. '))

    # --- Precio del servicio antes de la oferta (20-oct-2026) ---------------
    # La cotizacion se sustituye: armar items que el motor sepa cotizar exige
    # tiers, variantes y listas (ver test_solo_exterior); aqui se prueba la
    # frase y QUE se cotiza, no el motor.

    def _precio(self, cifra):
        return format_amount(self.env, cifra, self.env.company.currency_id)

    def _booking(self, **extra):
        return dict({'zone_id': self.zona.id,
                     'items': [{'product_tmpl_id': self.servicio.id}]}, **extra)

    def _cotizando(self, total=500.0):
        return patch.object(
            type(self.env['appointment.type']), '_visar_quote_booking',
            autospec=True, return_value={
                'total': total, 'currency_id': self.env.company.currency_id.id})

    def test_el_precio_es_el_del_servicio_sin_los_extras(self):
        with self._cotizando() as cotiza:
            intro = self.env['appointment.type']._visar_wizard_extras_intro(
                self._booking(extras_accepted=[
                    {'product_id': self.con_texto.product_variant_id.id, 'quantity': 1}]))
        self.assertEqual(intro, 'Tu servicio queda en %s, IVA incluido.'
                         % self._precio(500.0))
        # Ni los extras ya aceptados ni un plan: el precio de contado del servicio.
        kwargs = cotiza.call_args.kwargs
        self.assertNotIn('extra_addons', kwargs)
        self.assertNotIn('plan', kwargs)

    def test_el_precio_nombra_el_servicio_elegido(self):
        grupo = self.env['visar.service.group'].create(
            {'name': 'Fumigación', 'code': 'grupo_extras_prueba'})
        with self._cotizando():
            intro = self.env['appointment.type']._visar_wizard_extras_intro(
                self._booking(selections={'group_ids': [grupo.id]}))
        self.assertEqual(intro, 'Tu servicio de fumigación queda en %s, IVA incluido.'
                         % self._precio(500.0))

    def test_el_paso_del_agente_empieza_por_el_precio(self):
        with self._cotizando():
            paso = self.env['appointment.type']._visar_wizard_step_options(
                self._booking(), 'extras')
        self.assertEqual(paso['title'], 'Tu servicio queda en %s, IVA incluido.'
                         % self._precio(500.0))
        self.assertEqual(paso['hint'], 'Te sugerimos agregar estos productos '
                                       'para complementar el servicio.')

    def test_sin_cifra_el_paso_queda_como_antes(self):
        flujo = self.env['appointment.type']
        self.assertEqual(flujo._visar_wizard_extras_intro({'items': []}), '')
        with self._cotizando(total=0.0):
            self.assertEqual(flujo._visar_wizard_extras_intro(self._booking()), '')
        paso = flujo._visar_wizard_step_options(self._booking(), 'extras')
        self.assertEqual(paso['title'], 'Productos adicionales recomendados')

    def test_la_web_pinta_el_precio_antes_de_la_oferta(self):
        arch = self.env.ref('visar_appointment.visar_wizard_extras').arch_db
        self.assertIn('extras_intro', arch)
        self.assertLess(arch.index('extras_intro'),
                        arch.index('Te sugerimos agregar'))
