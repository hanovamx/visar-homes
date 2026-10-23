# -*- coding: utf-8 -*-
"""Base de conocimiento: separada del prompt y apagada hasta que se encienda (23-oct-2026)."""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_whatsapp_agent.models.visar_agent_conocimiento import (
    CONOCIMIENTO_PARAM,
)


@tagged('post_install', '-at_install')
class TestBaseConocimiento(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Conocimiento = self.env['visar.agent.conocimiento']
        self.param = self.env['ir.config_parameter'].sudo()

    def _config(self):
        return self.env['visar.agent.tools'].agent_runtime_config()

    def _encender(self, valor=True):
        self.env['res.config.settings'].create(
            {'visar_agente_conocimiento': valor}).execute()

    def test_nace_apagada_y_el_agente_no_la_recibe(self):
        self.assertFalse(self.param.get_param(CONOCIMIENTO_PARAM))
        self.assertIsNone(self._config()['conocimiento'])

    def test_trae_el_ejemplo_de_la_propuesta(self):
        nombres = self.Conocimiento.search([]).mapped('name')
        self.assertIn("Cobertura de plagas", nombres)
        self.assertIn("Preguntas frecuentes", nombres)
        self.assertEqual(len(nombres), 7)
        self.assertTrue(self.env.ref(
            'visar_whatsapp_agent.conocimiento_faq').pendientes)

    def test_encendida_llega_renderizada_y_con_la_marca(self):
        self._encender()
        bloque = self._config()['conocimiento']
        self.assertTrue(bloque.startswith("=== BASE DE CONOCIMIENTO ==="))
        self.assertIn("## Políticas de cita", bloque)
        self.assertIn("El técnico espera máximo 10 minutos", bloque)
        self.assertNotIn("{{marca}}", bloque)

    def test_apagarla_la_quita_otra_vez(self):
        self._encender()
        self._encender(False)
        self.assertIsNone(self._config()['conocimiento'])

    def test_solo_secciones_activas_y_en_orden(self):
        self._encender()
        self.Conocimiento.search([]).write({'active': False})
        self.Conocimiento.create({'name': "Segunda", 'body': "dos", 'sequence': 2})
        self.Conocimiento.create({'name': "Primera", 'body': "uno", 'sequence': 1})
        bloque = self._config()['conocimiento']
        self.assertLess(bloque.index("## Primera"), bloque.index("## Segunda"))
        self.assertNotIn("Cobertura de plagas", bloque)

    def test_encendida_pero_vacia_es_none(self):
        self._encender()
        self.Conocimiento.search([]).write({'active': False})
        self.assertIsNone(self._config()['conocimiento'])

    def test_la_vista_previa_es_lo_que_se_manda(self):
        self._encender()
        seccion = self.env.ref('visar_whatsapp_agent.conocimiento_valoracion')
        marca = self._config()['brand']
        self.assertEqual(seccion.vista_previa.replace('{{marca}}', marca),
                         self._config()['conocimiento'])
        self.assertTrue(seccion.encendida)
