# -*- coding: utf-8 -*-
"""El botón "Aplicar ahora" cuenta lo que pasó, no lo que se esperaba.

Un botón que dijera "aplicado" pase lo que pase sería peor que no tenerlo: la
razón de existir es cerrar los 15 minutos de duda entre guardar un prompt y
verlo en el chat, y una confirmación falsa alarga esa duda en vez de cerrarla.
Por eso las pruebas son casi todas del camino malo.

`requests.post` va parcheado: una prueba que llame al runtime de verdad pasa o
falla según lo que esté corriendo en la máquina, que es lo contrario de una
prueba.
"""
from unittest.mock import patch

import requests

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_POST = 'odoo.addons.visar_whatsapp_agent.models.visar_agent_runtime.requests.post'


class _Respuesta:
    def __init__(self, codigo=200):
        self.status_code = codigo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError("%s" % self.status_code)


@tagged('post_install', '-at_install')
class TestAgentRuntimeRefresh(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registro = cls.env['visar.agent.prompt'].sudo().create({
            'name': "Prompt de prueba", 'body': "hola",
        })

    def test_refresca_las_dos_caches(self):
        """Prompt y catálogo: las dos cachés que separan a Odoo del chat."""
        with patch(_POST, return_value=_Respuesta()) as post:
            accion = self.registro.action_visar_aplicar_ahora()
        urls = [llamada.args[0] for llamada in post.call_args_list]
        self.assertEqual(urls, ['http://127.0.0.1:8000/debug/runtime/refresh',
                                'http://127.0.0.1:8000/debug/catalog/refresh'])
        self.assertEqual(accion['params']['type'], 'success')

    def test_si_el_runtime_esta_caido_lo_dice(self):
        error = requests.exceptions.ConnectionError("conexion rechazada")
        with patch(_POST, side_effect=error):
            with self.assertRaises(UserError) as capturado:
                self.registro.action_visar_aplicar_ahora()
        self.assertIn("no respondio", str(capturado.exception))

    def test_un_error_del_runtime_no_pasa_por_exito(self):
        with patch(_POST, return_value=_Respuesta(503)):
            with self.assertRaises(UserError):
                self.registro.action_visar_aplicar_ahora()

    def test_un_vencimiento_no_se_cuenta_como_fallo(self):
        """Pudo aplicarse y ser la respuesta la que no llegó: se dice eso."""
        with patch(_POST, side_effect=requests.exceptions.Timeout()):
            with self.assertRaises(UserError) as capturado:
                self.registro.action_visar_aplicar_ahora()
        self.assertIn("Puede que si se haya aplicado", str(capturado.exception))

    def test_si_falla_la_primera_no_dice_que_hizo_la_segunda(self):
        with patch(_POST, side_effect=requests.exceptions.ConnectionError()) as post:
            with self.assertRaises(UserError):
                self.registro.action_visar_aplicar_ahora()
        self.assertEqual(post.call_count, 1)

    def test_sin_direccion_no_intenta_nada(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'visar_whatsapp_agent.runtime_url', '   ')
        with patch(_POST) as post:
            with self.assertRaises(UserError):
                self.registro.action_visar_aplicar_ahora()
        post.assert_not_called()

    def test_la_config_del_llm_tiene_el_mismo_boton(self):
        """El otro modelo que el runtime cachea. Si no, se olvida uno de dos."""
        config = self.env['visar.llm.config'].sudo().create({'name': "Prueba"})
        with patch(_POST, return_value=_Respuesta()):
            self.assertTrue(config.action_visar_aplicar_ahora())
