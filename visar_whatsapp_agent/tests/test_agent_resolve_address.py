# -*- coding: utf-8 -*-
"""`agent_resolve_address` y el aviso `cp_lookup` del paso de dirección (7-oct-2026).

El agente puede dejar de pedir el código postal solo si Odoo puede proponerlo.
Se fijan las dos mitades de ese acuerdo: lo que contesta el RPC, y que el paso
de la dirección le diga al runtime si la función está encendida.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

CP = '64700'


@tagged('post_install', '-at_install')
class TestAgentResolveAddress(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Param = cls.env['ir.config_parameter'].sudo()
        ZoneCp = cls.env['visar.zone.cp'].sudo()
        zona = cls.env['visar.zone'].search([], limit=1)
        registro = ZoneCp._get_cp_record(CP)
        if registro:
            registro.write({'zone_id': zona.id, 'municipality': 'Monterrey'})
        else:
            ZoneCp.create({'name': CP, 'zone_id': zona.id, 'municipality': 'Monterrey'})

    def _mapbox(self, features):
        return patch.object(type(self.env['visar.mapbox.service']),
                            '_visar_mapbox_geocode_features', return_value=features)

    def test_apagado_contesta_disabled(self):
        self.Param.set_param('visar.address_cp.enabled', '0')
        with self._mapbox([]) as llamada:
            resultado = self.Tools.agent_resolve_address(
                {'street': 'Filosofos', 'ext_num': '315', 'neighborhood': 'Tecnológico'})
        self.assertEqual(resultado, {'status': 'disabled'})
        llamada.assert_not_called()

    def test_encendido_propone_el_cp_sin_revelar_la_zona(self):
        self.Param.set_param('visar.address_cp.enabled', '1')
        feature = {'text': 'Calle Filósofos', 'address': '315', 'relevance': 0.95,
                   'context': [{'id': 'postcode.9', 'text': CP}]}
        with self._mapbox([feature]):
            resultado = self.Tools.agent_resolve_address(
                {'street': 'Filosofos', 'ext_num': '315', 'neighborhood': 'Tecnológico'})
        self.assertEqual(resultado['status'], 'found')
        self.assertEqual(resultado['zip'], CP)
        self.assertEqual(resultado['street'], 'Calle Filósofos')
        self.assertFalse({'zone_id', 'zone_code', 'zone_name', 'relevance'} & set(resultado))

    def test_el_paso_de_direccion_avisa_si_puede_proponer_el_cp(self):
        Flow = self.env['appointment.type']
        for encendido in ('0', '1'):
            self.Param.set_param('visar.address_cp.enabled', encendido)
            opciones = Flow._visar_wizard_step_options({}, 'address')
            campo_cp = [f for f in opciones['fields'] if f['name'] == 'zip'][0]
            self.assertEqual(opciones['cp_lookup'], encendido == '1')
            self.assertEqual(campo_cp['required'], encendido != '1')

    def test_el_ajuste_de_la_pantalla_es_el_unico_interruptor(self):
        """Encender en Ajustes → Visar llega al paso, al RPC y al runtime."""
        Ajustes = self.env['res.config.settings']
        for valor in (True, False):
            Ajustes.create({'visar_address_cp_enabled': valor}).execute()
            self.assertEqual(
                self.env['visar.zone.cp'].sudo()._visar_address_cp_enabled(), valor)
            self.assertEqual(self.Tools.agent_runtime_config()['address_cp'], valor)
            opciones = self.env['appointment.type']._visar_wizard_step_options(
                {}, 'address')
            self.assertEqual(opciones['cp_lookup'], valor)

    def test_los_ajustes_rechazan_valores_que_romperian_la_busqueda(self):
        from odoo.exceptions import ValidationError
        Ajustes = self.env['res.config.settings']
        with self.assertRaises(ValidationError):
            Ajustes.create({'visar_address_cp_min_relevance': 1.5})
        self.assertNotIn('visar_address_cp_bbox', Ajustes._fields,
                         "el área de búsqueda se deriva, no se captura")
