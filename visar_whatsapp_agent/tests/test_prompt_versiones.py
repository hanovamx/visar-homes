# -*- coding: utf-8 -*-
"""Versiones del prompt base: preparar la siguiente sin perder la actual (23-oct-2026).

Sigue habiendo un solo prompt base activo (lo que se cerro el 29-sep); lo que
vuelve es poder escribir otra version aparte y cambiar de una a otra.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPromptVersiones(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Prompt = self.env['visar.agent.prompt'].with_context(active_test=False)
        self.Tools = self.env['visar.agent.tools']
        self.actual = self.Prompt.search([('ruta', '=', False), ('active', '=', True)])
        self.assertEqual(len(self.actual), 1)

    def _borrador(self, texto="VERSION NUEVA"):
        accion = self.actual.action_duplicar_version()
        borrador = self.Prompt.browse(accion['res_id'])
        borrador.body = texto
        return borrador

    def test_duplicar_crea_un_borrador_que_el_agente_no_ve(self):
        antes = self.Tools.agent_runtime_config()['prompt']
        borrador = self._borrador()
        self.assertFalse(borrador.active)
        self.assertEqual(borrador.version_estado, 'borrador')
        self.assertFalse(borrador.usado_desde)
        self.assertEqual(self.Tools.agent_runtime_config()['prompt'], antes)
        self.assertEqual(self.actual.version_estado, 'en_uso')

    def test_usar_version_cambia_una_por_otra(self):
        borrador = self._borrador()
        borrador.action_usar_version()
        self.assertTrue(borrador.active)
        self.assertEqual(borrador.version_estado, 'en_uso')
        self.assertTrue(borrador.usado_desde)
        self.assertFalse(self.actual.active)
        self.assertEqual(self.actual.version_estado, 'anterior')
        self.assertTrue(self.actual.retirado_el)
        self.assertEqual(self.Tools.agent_runtime_config()['prompt'], "VERSION NUEVA")

    def test_volver_a_la_anterior(self):
        texto_de_antes = self.actual.body.strip()
        borrador = self._borrador()
        borrador.action_usar_version()
        self.actual.action_usar_version()
        self.assertTrue(self.actual.active)
        self.assertFalse(borrador.active)
        self.assertEqual(borrador.version_estado, 'anterior')
        self.assertEqual(self.Tools.agent_runtime_config()['prompt'], texto_de_antes)

    def test_sigue_sin_poder_haber_dos_activas(self):
        borrador = self._borrador()
        with self.assertRaises(ValidationError):
            borrador.active = True

    def test_un_borrador_se_puede_borrar_una_usada_no(self):
        borrador = self._borrador()
        borrador.unlink()
        self.assertFalse(borrador.exists())
        with self.assertRaises(UserError):
            self.actual.unlink()
        otro = self._borrador()
        otro.action_usar_version()
        with self.assertRaises(UserError):
            self.actual.unlink()  # ya fue usada: es «anterior», no se borra

    def test_no_se_usa_una_version_vacia_ni_la_que_ya_esta(self):
        with self.assertRaises(UserError):
            self.actual.action_usar_version()
        borrador = self._borrador(texto="   ")
        with self.assertRaises(UserError):
            borrador.action_usar_version()
        self.assertTrue(self.actual.active)

    def test_las_memorias_de_ruta_no_tienen_versiones(self):
        memoria = self.Prompt.search([('ruta', '!=', False)], limit=1)
        if memoria:
            with self.assertRaises(UserError):
                memoria.action_duplicar_version()
