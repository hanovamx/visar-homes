# -*- coding: utf-8 -*-
"""El vocabulario que un consultor añade desde Odoo llega al cuestionario.

Lo que se fija aquí es la promesa entera del modelo, y cada punto es una forma
concreta de romperla sin que nadie se entere:

  * la palabra nueva SALE en el paso. Si no, se guardó y no hizo nada, que es
    el peor final: parece que funciona;
  * las del código NO se pierden. El overlay suma; si algún día restara, las
    pruebas seguirían verdes con el código y producción haría otra cosa;
  * no se repite. El puntaje cuenta keywords (`classify._scores`), así que una
    pista duplicada vale dos puntos por un solo dato y puede desempatar mal;
  * los pasos que MIDEN heredan lo de `cobertura`. Es la propiedad que costó el
    bug del 7-sep: "el patio son 27 metros" no contesta los metros de la casa,
    y solo lo sabe quien comparte el vocabulario de los lugares;
  * archivar apaga; una opción que no existe no se puede guardar.
"""
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentVocabulario(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Flow = cls.env['appointment.type'].sudo()
        cls.Vocab = cls.env['visar.agent.vocabulario'].sudo()

    def _keywords(self, step_key, valor):
        """Las `keywords` de una opción, tal y como se publican."""
        payload = self.Flow._visar_wizard_step_options({}, step_key)
        for opcion in payload['options']:
            if opcion['value'] == valor:
                return opcion.get('keywords') or []
        self.fail("no existe la opción %r en el paso %r" % (valor, step_key))

    # ------------------------------------------------------------------

    def test_la_palabra_nueva_llega_al_paso(self):
        self.Vocab.create({
            'paso': 'cobertura', 'opcion': 'exterior',
            'palabras': 'azotea\nla azotea',
        })
        self.assertIn('azotea', self._keywords('cobertura', 'exterior'))

    def test_las_del_codigo_no_se_pierden(self):
        antes = self._keywords('cobertura', 'exterior')
        self.Vocab.create({
            'paso': 'cobertura', 'opcion': 'exterior', 'palabras': 'azotea',
        })
        despues = self._keywords('cobertura', 'exterior')
        self.assertTrue(set(antes).issubset(set(despues)))
        self.assertEqual(len(despues), len(antes) + 1)

    def test_una_palabra_que_ya_estaba_no_se_repite(self):
        antes = self._keywords('cobertura', 'exterior')
        self.assertIn('patio', antes, "el código debería traer 'patio'")
        self.Vocab.create({
            # con acento y en mayúsculas: el runtime compara normalizado, así
            # que "Patío" es la misma pista y contaría dos veces.
            'paso': 'cobertura', 'opcion': 'exterior', 'palabras': 'Patío\npatio',
        })
        self.assertEqual(self._keywords('cobertura', 'exterior'), antes)

    def test_los_pasos_que_miden_heredan_la_palabra(self):
        self.Vocab.create({
            'paso': 'cobertura', 'opcion': 'exterior', 'palabras': 'azotea',
        })
        payload = self.Flow._visar_wizard_step_options({}, 'interior')
        self.assertIn('azotea', payload['lugares']['exterior'])
        self.assertNotIn('azotea', payload['lugares']['interior'])

    def test_archivar_la_apaga(self):
        fila = self.Vocab.create({
            'paso': 'cobertura', 'opcion': 'exterior', 'palabras': 'azotea',
        })
        self.assertIn('azotea', self._keywords('cobertura', 'exterior'))
        fila.active = False
        self.assertNotIn('azotea', self._keywords('cobertura', 'exterior'))

    def test_una_opcion_que_no_existe_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            self.Vocab.create({
                'paso': 'cobertura', 'opcion': 'el_patio', 'palabras': 'azotea',
            })

    def test_la_salida_de_poliza_se_puede_ampliar(self):
        """Nacio vacia y la lleno el 10-sep "Solo este servicio" (ver
        `_VISAR_POLIZA_KEYWORDS`). Sigue admitiendo lo que anada un consultor, y
        lo que ya estaba ("nel") no se repite."""
        self.Vocab.create({
            'paso': 'poliza', 'opcion': 'no_gracias', 'palabras': 'nel\nasi nomas',
        })
        overlay = self.Flow._visar_vocabulario_overlay()
        palabras = self.Flow._visar_vocabulario(overlay, 'poliza', 'no_gracias')
        self.assertIn('solo este servicio', palabras)
        self.assertEqual(palabras.count('nel'), 1)
        self.assertEqual(palabras[-1], 'asi nomas')

    def test_la_poliza_publica_la_salida_con_sus_frases(self):
        """El payload real del paso: la fila de salida lleva las frases.

        Es lo que evita que "Solo este servicio" contrate el plan de 3 servicios:
        el runtime las lee como frase antes que el conteo de raices.
        """
        planes = [{'plan_id': 7, 'name': 'Suscripcion anual', 'period_total': 7866.0,
                   'upfront_total': 7866.0, 'saving': 0.0}]
        with patch.object(type(self.Flow), '_visar_wizard_poliza_offers',
                          return_value=planes), \
                patch.object(type(self.Flow), '_visar_wizard_poliza_label',
                             return_value='Suscripcion anual'), \
                patch.object(type(self.Flow), '_visar_wizard_poliza_description',
                             return_value=''):
            payload = self.Flow._visar_wizard_step_options({}, 'poliza')
        salida = [o for o in payload['options'] if o['value'] == 0]
        self.assertTrue(salida)
        self.assertIn('solo este servicio', salida[0]['keywords'])

    def test_lo_efectivo_es_lo_que_se_publica(self):
        """El campo de la pantalla no puede decir una cosa y el agente otra."""
        fila = self.Vocab.create({
            'paso': 'motivo', 'opcion': 'correctivo', 'palabras': 'gusanos\ntengo',
        })
        self.assertEqual(
            fila.palabras_efectivas.splitlines(),
            self._keywords('motivo', 'correctivo'))
        # 'tengo' ya venía del código: se pidió dos veces y aparece una.
        self.assertEqual(fila.palabras_efectivas.count('\ngusanos'), 1)
        self.assertEqual(
            len([p for p in fila.palabras_efectivas.splitlines() if p == 'tengo']), 1)

    def test_las_opciones_validas_se_ensenan(self):
        fila = self.Vocab.new({'paso': 'cobertura'})
        self.assertEqual(fila.opciones_validas, 'interior, exterior, ambos')
