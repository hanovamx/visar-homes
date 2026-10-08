# -*- coding: utf-8 -*-
"""Add-ons atados a una plaga, y el catálogo de plagas que los gobierna.

Lo que se fija aquí, y por qué cada cosa importa:

  * **una línea sin plagas se comporta como siempre.** Es la promesa de la
    columna: dejarla vacía no cambia nada;
  * una línea con plagas solo aparece —o solo se añade, si es obligatoria—
    cuando el cliente eligió alguna de ellas. Es lo que sustituye a la casilla
    «Producto control de roedores»;
  * **la pregunta de la cita y la columna leen la MISMA lista.** Una plaga dada
    de alta en Odoo tiene que salir en la pregunta sin tocar código: si las dos
    listas se separan, se puede atar un add-on a una plaga que nadie puede elegir.
"""
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAddonsPorPlaga(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tmpl = cls.env['product.template']
        cls.AptType = cls.env['appointment.type']
        cls.Plaga = cls.env['visar.plaga']
        cls.roedores = cls.env.ref('visar_base.plaga_roedores')
        cls.rastreros = cls.env.ref('visar_base.plaga_rastreros')
        cls.voladores = cls.env.ref('visar_base.plaga_voladores')
        cls.lista = cls.env['product.pricelist'].create({'name': 'Lista plagas'})
        cls.zona = cls.env['visar.zone'].create({
            'name': 'Zona plagas', 'code': 'ZP', 'pricelist_id': cls.lista.id})
        cls.estacion = Tmpl.create({'name': 'Estación de prueba', 'list_price': 100.0})
        cls.trampa = Tmpl.create({'name': 'Trampa de prueba', 'list_price': 80.0})
        cls.libre = Tmpl.create({'name': 'Adicional libre', 'list_price': 50.0})
        cls.servicio = Tmpl.create({
            'name': 'Fumigación de prueba', 'list_price': 500.0,
            'optional_product_ids': [
                (6, 0, (cls.estacion | cls.trampa | cls.libre).ids)]})
        cls.lineas = {
            l.optional_product_id: l for l in cls.servicio.visar_optional_line_ids}
        cls.lineas[cls.estacion].plaga_ids = cls.roedores
        cls.lineas[cls.trampa].plaga_ids = cls.rastreros | cls.voladores
        cls.items = [{'product_tmpl_id': cls.servicio.id}]

    def _ofrecidos(self, plagas=None):
        ofertas = self.AptType._visar_offered_addons(self.items, self.zona, plagas=plagas)
        return self.env['product.template'].browse([o['template_id'] for o in ofertas])

    # ------------------------------------------------------------------
    # Recomendados (opcionales)
    # ------------------------------------------------------------------

    def test_sin_plaga_en_la_linea_se_ofrece_siempre(self):
        for plagas in (None, [], ['roedores'], ['voladores']):
            self.assertIn(self.libre, self._ofrecidos(plagas))

    def test_con_plaga_solo_se_ofrece_si_el_cliente_la_eligio(self):
        self.assertEqual(self._ofrecidos(['roedores']), self.estacion | self.libre)
        self.assertEqual(self._ofrecidos(['voladores']), self.trampa | self.libre)
        self.assertEqual(self._ofrecidos([]), self.libre)

    def test_varias_plagas_en_la_linea_basta_con_una(self):
        self.assertIn(self.trampa, self._ofrecidos(['rastreros']))
        self.assertIn(self.trampa, self._ofrecidos(['voladores']))
        self.assertNotIn(self.trampa, self._ofrecidos(['roedores']))

    def test_proteccion_general_cuenta_como_todas(self):
        """El wizard la desglosa en todas las del catálogo antes de guardarla."""
        booking, error = self.AptType._visar_wizard_answer_plagas(
            {'selections': {'motivo': 'preventivo'}},
            {'servicio_plaga': ['proteccion_general']})
        self.assertIsNone(error)
        plagas = self.AptType._visar_wizard_plagas(booking)
        self.assertEqual(self._ofrecidos(plagas),
                         self.estacion | self.trampa | self.libre)

    # ------------------------------------------------------------------
    # Obligatorios
    # ------------------------------------------------------------------

    def test_obligatorio_con_plaga_solo_entra_con_esa_plaga(self):
        self.lineas[self.estacion].write({'is_mandatory': True, 'quantity': 3})
        variante = self.estacion.product_variant_id
        self.assertEqual(
            self.servicio._visar_get_mandatory_addon_map(self.zona, plagas=['roedores']),
            {variante.id: 3})
        self.assertEqual(
            self.servicio._visar_get_mandatory_addon_map(self.zona, plagas=['voladores']),
            {})
        # Como obligatorio ya no es una recomendación que aceptar.
        self.assertNotIn(self.estacion, self._ofrecidos(['roedores']))

    def test_obligatorio_con_plaga_no_entra_en_un_pedido_del_backoffice(self):
        """Ahí nadie contestó la pregunta de plagas: no hay de dónde saberlo."""
        self.lineas[self.estacion].is_mandatory = True
        self.lineas[self.libre].write({'is_mandatory': True, 'quantity': 2})
        self.assertEqual(
            self.servicio._visar_get_mandatory_addon_map(),
            {self.libre.product_variant_id.id: 2})

    # ------------------------------------------------------------------
    # El catálogo es la pregunta
    # ------------------------------------------------------------------

    def _opciones(self, motivo):
        paso = self.AptType._visar_wizard_step_options(
            {'selections': {'motivo': motivo}}, 'plagas')
        return paso['options']

    def test_la_pregunta_ofrece_las_plagas_del_catalogo(self):
        valores = [o['value'] for o in self._opciones('preventivo')]
        self.assertEqual(
            valores,
            self.Plaga.search([]).mapped('code') + ['proteccion_general'])

    def test_una_plaga_nueva_sale_en_la_pregunta_y_se_puede_contestar(self):
        aves = self.Plaga.create({
            'name': 'Aves y murciélagos', 'description': 'Palomas', 'sequence': 99})
        self.assertEqual(aves.code, 'aves_y_murcielagos')
        opcion = next(o for o in self._opciones('correctivo') if o['value'] == aves.code)
        self.assertEqual(opcion['label'], 'Aves y murciélagos')
        self.assertEqual(opcion['description'], 'Palomas')

        booking, error = self.AptType._visar_wizard_answer_plagas(
            {'selections': {'motivo': 'correctivo'}},
            {'servicio_plaga': [aves.code]})
        self.assertIsNone(error)
        self.assertEqual(booking['selections']['servicio_plaga'], [aves.code])

        self.lineas[self.libre].plaga_ids = aves
        self.assertIn(self.libre, self._ofrecidos([aves.code]))
        self.assertNotIn(self.libre, self._ofrecidos(['roedores']))

        general = next(o for o in self._opciones('preventivo')
                       if o['value'] == 'proteccion_general')
        self.assertTrue(general['description'].startswith('Todas: '))
        self.assertIn('aves y murciélagos', general['description'])

    def test_una_plaga_archivada_deja_de_ofrecerse(self):
        self.voladores.active = False
        self.assertNotIn(
            'voladores', [o['value'] for o in self._opciones('correctivo')])

    def test_la_descripcion_de_proteccion_general_de_fabrica(self):
        """Con las tres de fábrica el cliente lee lo mismo que antes."""
        catalogo = self.rastreros | self.voladores | self.roedores
        self.assertEqual(
            self.AptType._visar_proteccion_general_descripcion(catalogo),
            'Las tres: rastreros, voladores y roedores')

    def test_codigo_reservado_o_repetido_no_se_admite(self):
        with self.assertRaises(ValidationError):
            self.Plaga.create({'name': 'Termitas', 'code': 'termitas'})
        with self.assertRaises(Exception), self.cr.savepoint():
            self.Plaga.create({'name': 'Otros roedores', 'code': 'roedores'})
            self.env.flush_all()

    def test_la_plaga_nueva_admite_vocabulario(self):
        self.Plaga.create({'name': 'Aves', 'sequence': 99})
        self.assertIn('aves', self.AptType._visar_vocabulario_claves('plagas'))
