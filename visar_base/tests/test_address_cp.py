# -*- coding: utf-8 -*-
"""`visar.zone.cp._visar_address_to_cp`: calle + número + colonia → CP (7-oct-2026).

Lo que se fija aquí:

  * **apagado no sale a la red.** La función nace apagada porque el CP de Mapbox
    acierta la mitad de las veces; encenderla es una decisión, no un despliegue;
  * **manda la cobertura, no el orden de Mapbox.** El primer candidato puede ser
    una calle homónima fuera del catálogo; se toma el primero que SÍ está;
  * un candidato de relevancia baja se descarta aunque esté cubierto;
  * sin nada utilizable se contesta `not_found`, nunca un CP inventado.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

CP_CUBIERTO = '64700'
CP_AJENO = '25000'


def _feature(cp, relevance=0.9, text='Calle Filósofos', number='315'):
    feature = {
        'text': text, 'relevance': relevance,
        'place_name': '%s %s, %s' % (text, number or '', cp),
        'context': [{'id': 'postcode.1', 'text': cp},
                    {'id': 'place.1', 'text': 'Monterrey'}],
    }
    if number:
        feature['address'] = number
    return feature


@tagged('post_install', '-at_install')
class TestAddressCp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ZoneCp = cls.env['visar.zone.cp'].sudo()
        cls.Param = cls.env['ir.config_parameter'].sudo()
        cls.Param.set_param('visar.address_cp.enabled', '1')
        zona = cls.env['visar.zone'].search([], limit=1)
        registro = cls.ZoneCp._get_cp_record(CP_CUBIERTO)
        if registro:
            registro.write({'zone_id': zona.id, 'municipality': 'Monterrey'})
        else:
            cls.ZoneCp.create({'name': CP_CUBIERTO, 'zone_id': zona.id,
                               'municipality': 'Monterrey'})
        cls.ZoneCp.search([('name', '=', CP_AJENO)]).unlink()

    def _resolver(self, features, **kwargs):
        Mapbox = type(self.env['visar.mapbox.service'])
        with patch.object(Mapbox, '_visar_mapbox_geocode_features',
                          return_value=features) as llamada:
            valores = dict(street='Filosofos', ext_num='315',
                           neighborhood='Tecnológico')
            valores.update(kwargs)
            return self.ZoneCp._visar_address_to_cp(**valores), llamada

    def test_apagado_no_sale_a_la_red(self):
        self.Param.set_param('visar.address_cp.enabled', '0')
        resultado, llamada = self._resolver([_feature(CP_CUBIERTO)])
        self.assertEqual(resultado, {'status': 'disabled'})
        llamada.assert_not_called()

    def test_sin_colonia_no_se_busca(self):
        resultado, llamada = self._resolver([_feature(CP_CUBIERTO)], neighborhood='')
        self.assertEqual(resultado['status'], 'incomplete')
        llamada.assert_not_called()

    def test_encuentra_el_cp_cubierto(self):
        resultado, llamada = self._resolver([_feature(CP_CUBIERTO)])
        self.assertEqual(resultado['status'], 'found')
        self.assertEqual(resultado['zip'], CP_CUBIERTO)
        self.assertEqual(resultado['municipality'], 'Monterrey')
        self.assertTrue(resultado['exact'])
        self.assertEqual(llamada.call_args.args[0], 'Filosofos 315, Tecnológico')
        self.assertTrue(llamada.call_args.kwargs.get('bbox'),
                        "la búsqueda va acotada a la caja de cobertura")

    def test_se_salta_el_candidato_fuera_de_cobertura(self):
        resultado, _llamada = self._resolver(
            [_feature(CP_AJENO, relevance=1.0), _feature(CP_CUBIERTO, relevance=0.8)])
        self.assertEqual(resultado['zip'], CP_CUBIERTO)

    def test_solo_candidatos_fuera_de_cobertura_es_not_found(self):
        resultado, _llamada = self._resolver([_feature(CP_AJENO)])
        self.assertEqual(resultado, {'status': 'not_found'})

    def test_relevancia_baja_se_descarta(self):
        resultado, _llamada = self._resolver([_feature(CP_CUBIERTO, relevance=0.3)])
        self.assertEqual(resultado['status'], 'not_found')

    def test_calle_sin_numero_se_marca_como_no_exacta(self):
        resultado, _llamada = self._resolver([_feature(CP_CUBIERTO, number=None)])
        self.assertEqual(resultado['status'], 'found')
        self.assertFalse(resultado['exact'])

    def test_limpia_el_ruido_de_numero_y_colonia(self):
        _resultado, llamada = self._resolver(
            [_feature(CP_CUBIERTO)], street='Filosofos', ext_num='No. 315',
            neighborhood='Col. Tecnológico', municipality='Monterrey')
        self.assertEqual(llamada.call_args.args[0],
                         'Filosofos 315, Tecnológico, Monterrey')

    def test_la_caja_sale_del_catalogo_e_ignora_centroides_descabellados(self):
        """Un CP de Linares con el centroide en Puebla no estira la búsqueda."""
        zona = self.env['visar.zone'].search([], limit=1)
        self.ZoneCp.search([]).write(
            {'visar_centroid_lat': 0.0, 'visar_centroid_lng': 0.0})
        self.assertEqual(self.ZoneCp._visar_address_cp_bbox(),
                         '-100.786,23.273,-99.030,27.403',
                         "sin centroides, el respaldo fijo")
        for i in range(12):
            self.ZoneCp.create({
                'name': '991%02d' % i, 'zone_id': zona.id,
                'visar_centroid_lat': 25.0 + i * 0.1,
                'visar_centroid_lng': -100.5 + i * 0.05})
        self.ZoneCp.create({'name': '99199', 'zone_id': zona.id,
                            'visar_centroid_lat': 19.07, 'visar_centroid_lng': -98.27})
        # Uno SIN zona, lejos pero creíble: no cuenta, no tiene cobertura.
        self.ZoneCp.create({'name': '99198', 'zone_id': False,
                            'visar_centroid_lat': 27.5, 'visar_centroid_lng': -99.0})
        self.assertEqual(self.ZoneCp._visar_address_cp_bbox(),
                         '-100.650,24.850,-99.800,26.250')
