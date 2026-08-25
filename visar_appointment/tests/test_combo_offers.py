# -*- coding: utf-8 -*-
"""El combo que la canasta NO alcanza, y qué le falta para alcanzarlo.

El fallo que motiva esto: la ruta de Información cotizaba fumigación de solo
interior + áreas verdes en 1,800 y el cuestionario web cobraba 2,200 con el corte
al 50%. Ninguno de los dos se equivocaba — el motor de precio es el mismo y cada
total era correcto **para su canasta**. Lo que fallaba era que nadie le decía al
cliente que por añadir Exterior el corte bajaba a la mitad, porque la condición
del combo vive en `visar.combo.rule` y no salía por ninguna parte.

Estas pruebas no crean productos: la aritmética del ahorro con precios de zona ya
la cubre `test_poliza.test_08_combo_two_visits_and_discount` sobre el mismo
`visar.combo.rule`. Lo que se fija aquí es a QUIÉN se le ofrece el combo y con qué
le falta, que es donde estaba el agujero.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestComboOffers(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AptType = cls.env['appointment.type']
        grupo = cls.env['visar.service.group'].create(
            {'name': 'Grupo Combo Oferta', 'code': 'CMBOFR'})
        cls.dim_a = cls.env['visar.service.dimension'].create(
            {'name': 'Interior Test', 'code': 'OFR_INT', 'group_id': grupo.id})
        cls.dim_b = cls.env['visar.service.dimension'].create(
            {'name': 'Exterior Test', 'code': 'OFR_EXT', 'group_id': grupo.id})
        cls.dim_c = cls.env['visar.service.dimension'].create(
            {'name': 'Corte Test', 'code': 'OFR_COR', 'group_id': grupo.id})
        # Las reglas preexistentes de la base real ensuciarían el conteo.
        cls.env['visar.combo.rule'].search([]).write({'active': False})
        cls.rule = cls.env['visar.combo.rule'].create({
            'name': 'Combo Oferta Test', 'sequence': 1, 'discount_factor': 0.5,
            'required_dimension_ids': [(6, 0, [cls.dim_a.id, cls.dim_b.id, cls.dim_c.id])],
            'discount_dimension_ids': [(6, 0, [cls.dim_c.id])]})

    def _items(self, *dimensiones):
        return [{'dimension_id': dim.id} for dim in dimensiones]

    # ------------------------------------------------------------------
    # La regla sabe lo que le falta
    # ------------------------------------------------------------------

    def test_lo_que_falta_son_dimensiones_no_un_booleano(self):
        """"¿Aplica?" no vende nada; "te falta Exterior" sí."""
        faltan = self.rule._visar_missing_dimensions([self.dim_a.id, self.dim_c.id])
        self.assertEqual(faltan, self.dim_b)

    def test_una_regla_cumplida_no_echa_nada_de_menos(self):
        self.assertFalse(self.rule._visar_missing_dimensions(
            [self.dim_a.id, self.dim_b.id, self.dim_c.id]))

    def test_sin_dimensiones_requeridas_no_falta_nada(self):
        """Una regla sin requisitos no puede estar "a una dimensión"."""
        suelta = self.env['visar.combo.rule'].create(
            {'name': 'Sin requisitos', 'discount_factor': 0.5})
        self.assertFalse(suelta._visar_missing_dimensions([]))

    # ------------------------------------------------------------------
    # A quién se le ofrece
    # ------------------------------------------------------------------

    def test_el_combo_empezado_se_ofrece_con_lo_que_falta(self):
        """El caso real: interior + corte, sin exterior."""
        ofertas = self.AptType._visar_combo_offers(
            self._items(self.dim_a, self.dim_c), self.env['visar.zone'])
        self.assertEqual(len(ofertas), 1)
        oferta = ofertas[0]
        self.assertEqual(oferta['name'], 'Combo Oferta Test')
        self.assertEqual(oferta['discount_percent'], 50.0)
        self.assertEqual([m['service_code'] for m in oferta['missing']], ['OFR_EXT'])

    def test_el_descuento_dice_SOBRE_QUE_aplica(self):
        """"50% de descuento" sin sujeto se lee como "50% del total".

        Pasó en una prueba real contra la copia: el agente anunció "un combo que
        te da 50% de descuento en todo", y el 50% es solo del corte de pasto. El
        sujeto viaja siempre, haya precios que enseñar o no.
        """
        ofertas = self.AptType._visar_combo_offers(
            self._items(self.dim_a, self.dim_b), self.env['visar.zone'])
        self.assertEqual([d['service_code'] for d in ofertas[0]['discount_services']],
                         ['OFR_COR'])

    def test_un_combo_ya_alcanzado_no_se_ofrece(self):
        """Ya está aplicado en el total: volver a ofrecerlo sería mentir."""
        self.assertEqual(self.AptType._visar_combo_offers(
            self._items(self.dim_a, self.dim_b, self.dim_c),
            self.env['visar.zone']), [])

    def test_un_combo_sin_empezar_no_se_ofrece(self):
        """Si faltan TODAS las requeridas, el cliente no está cerca: es publicidad.

        Cotizar un servicio de otro grupo no puede convertir la respuesta en un
        catálogo de promociones.
        """
        otro = self.env['visar.service.dimension'].create({
            'name': 'Ajena Test', 'code': 'OFR_AJE',
            'group_id': self.dim_a.group_id.id})
        self.assertEqual(self.AptType._visar_combo_offers(
            self._items(otro), self.env['visar.zone']), [])

    def test_una_regla_inactiva_no_se_ofrece(self):
        self.rule.active = False
        self.assertEqual(self.AptType._visar_combo_offers(
            self._items(self.dim_a, self.dim_c), self.env['visar.zone']), [])

    def test_sin_items_no_hay_nada_que_ofrecer(self):
        self.assertEqual(
            self.AptType._visar_combo_offers([], self.env['visar.zone']), [])

    def test_el_ahorro_es_de_las_lineas_no_del_total(self):
        """Lo que falta se cobra aparte: prometer que el total baja es mentir.

        En el caso real el corte pasa de 1,200 a 600 y el total SUBE de 1,800 a
        2,200, porque Exterior cuesta. La oferta viaja como antes/después por
        línea justamente para que nadie pueda leerla como una rebaja del total.
        """
        ofertas = self.AptType._visar_combo_offers(
            self._items(self.dim_a, self.dim_c), self.env['visar.zone'])
        self.assertEqual(ofertas[0]['discounts'], [],
                         "sin productos no hay antes/despues que dar")
        self.assertEqual(ofertas[0]['saving'], 0.0)

    def test_sin_ahorro_calculable_la_oferta_no_inventa_una_cifra(self):
        """La línea que se abarata no está en la canasta: se dice qué falta y ya.

        `saving` en 0.0 y no una estimación: los metros de lo que aún no se ha
        pedido no los sabe nadie, y un número inventado aquí es un número que el
        cliente lee como promesa.
        """
        ofertas = self.AptType._visar_combo_offers(
            self._items(self.dim_a, self.dim_b), self.env['visar.zone'])
        self.assertEqual(len(ofertas), 1)
        self.assertEqual(ofertas[0]['saving'], 0.0)
        self.assertEqual([m['service_code'] for m in ofertas[0]['missing']], ['OFR_COR'])
