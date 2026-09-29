# -*- coding: utf-8 -*-
"""El vocabulario que un consultor añade desde Odoo llega al cuestionario.

Lo que se fija aquí es la promesa entera del modelo, y cada punto es una forma
concreta de romperla sin que nadie se entere:

  * la palabra nueva SALE en el paso. Si no, se guardó y no hizo nada, que es
    el peor final: parece que funciona;
  * **la fila MANDA** (desde el 29-sep-2026). Antes sumaba sobre el código y no
    había forma de quitar una palabra de fábrica; ahora sustituye, que es lo que
    permite corregir una que clasifica mal sin un despliegue;
  * el código sigue siendo el suelo RECUPERABLE: sin fila se usa él, y
    «Restaurar valores originales» lo devuelve. Eso es lo que hace reversible
    dejar que la pantalla mande;
  * no se repite. El puntaje cuenta keywords (`classify._scores`), así que una
    pista duplicada vale dos puntos por un solo dato y puede desempatar mal;
  * los pasos que MIDEN heredan lo de `cobertura`. Es la propiedad que costó el
    bug del 7-sep: "el patio son 27 metros" no contesta los metros de la casa,
    y solo lo sabe quien comparte el vocabulario de los lugares;
  * archivar vuelve a fábrica; una opción que no existe no se puede guardar, y
    dos filas para la misma opción tampoco.
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

    def _fila(self, paso, opcion):
        """La fila de esa opcion. Existe siempre: la siembra la migracion.

        Desde el 29-sep-2026 hay una fila por ranura, asi que el gesto real de
        un consultor es EDITAR, no crear —y crear una segunda ya ni se puede—.
        Se crea aqui solo por si la prueba corre sobre una base sin sembrar.
        """
        fila = self.Vocab.with_context(active_test=False).search(
            [('paso', '=', paso), ('opcion', '=', opcion)], limit=1)
        return fila or self.Vocab.create(
            {'paso': paso, 'opcion': opcion, 'palabras': ''})

    def _set(self, paso, opcion, palabras):
        fila = self._fila(paso, opcion)
        fila.write({'palabras': palabras, 'active': True})
        return fila

    def _keywords(self, step_key, valor):
        """Las `keywords` de una opción, tal y como se publican."""
        payload = self.Flow._visar_wizard_step_options({}, step_key)
        for opcion in payload['options']:
            if opcion['value'] == valor:
                return opcion.get('keywords') or []
        self.fail("no existe la opción %r en el paso %r" % (valor, step_key))

    # ------------------------------------------------------------------

    def test_la_palabra_nueva_llega_al_paso(self):
        self._set('cobertura', 'exterior', 'azotea\nla azotea')
        self.assertIn('azotea', self._keywords('cobertura', 'exterior'))

    def test_la_fila_sustituye_a_los_valores_de_fabrica(self):
        """Lo contrario de lo que valía hasta el 29-sep-2026, y a propósito.

        Mientras esto sumaba, una palabra del código que clasificara mal no se
        podía quitar sin desplegar. Ese era el motivo real de que la pantalla
        llevara meses con cero filas en producción.
        """
        antes = self._keywords('cobertura', 'exterior')
        self.assertIn('patio', antes, "el código debería traer 'patio'")
        self._set('cobertura', 'exterior', 'azotea')
        self.assertEqual(self._keywords('cobertura', 'exterior'), ['azotea'])

    def test_se_puede_quitar_una_palabra_de_fabrica(self):
        """El caso de uso entero: 'patio' clasificaba mal y se va, sin desplegar."""
        originales = self.Flow._visar_vocabulario_originales('cobertura', 'exterior')
        self.assertIn('patio', originales)
        self._set('cobertura', 'exterior',
                  '\n'.join(p for p in originales if p != 'patio'))
        vivas = self._keywords('cobertura', 'exterior')
        self.assertNotIn('patio', vivas)
        self.assertIn('jardin', vivas, "y las demás siguen ahí")

    def test_sin_fila_manda_el_codigo(self):
        """El suelo recuperable: una ranura sin sembrar sigue funcionando."""
        self.Vocab.with_context(active_test=False).search(
            [('paso', '=', 'cobertura')]).unlink()
        self.assertEqual(
            self._keywords('cobertura', 'exterior'),
            self.Flow._visar_vocabulario_originales('cobertura', 'exterior'))

    def test_una_palabra_que_ya_estaba_no_se_repite(self):
        # con acento y en mayúsculas: el runtime compara normalizado, así que
        # "Patío" es la misma pista y contaría dos veces.
        self._set('cobertura', 'exterior', 'Patío\npatio')
        self.assertEqual(self._keywords('cobertura', 'exterior'), ['Patío'])

    def test_restaurar_devuelve_los_valores_de_fabrica(self):
        """La marcha atrás. Sin esto, borrar de más sería irreparable."""
        originales = self.Flow._visar_vocabulario_originales('cobertura', 'exterior')
        fila = self._set('cobertura', 'exterior', 'azotea')
        self.assertFalse(fila.es_original)
        fila.action_restaurar_originales()
        self.assertTrue(fila.es_original)
        self.assertEqual(self._keywords('cobertura', 'exterior'), originales)

    def test_es_original_ignora_el_orden_y_los_acentos(self):
        """Reordenar o quitar un acento no es un cambio de vocabulario.

        Marcarlo como «modificado» mandaría a alguien a buscar una diferencia
        que no existe.
        """
        originales = self.Flow._visar_vocabulario_originales('cobertura', 'exterior')
        fila = self._set('cobertura', 'exterior',
                         '\n'.join(reversed([p.upper() for p in originales])))
        self.assertTrue(fila.es_original)

    def test_dos_filas_para_la_misma_opcion_no_se_guardan(self):
        """Con la fila mandando, dos filas serían dos verdades."""
        self._set('cobertura', 'exterior', 'azotea')
        with self.assertRaises(ValidationError):
            self.Vocab.create({
                'paso': 'cobertura', 'opcion': 'exterior', 'palabras': 'techo',
            })

    def test_los_pasos_que_miden_heredan_la_palabra(self):
        self._set('cobertura', 'exterior', 'azotea')
        payload = self.Flow._visar_wizard_step_options({}, 'interior')
        self.assertIn('azotea', payload['lugares']['exterior'])
        self.assertNotIn('azotea', payload['lugares']['interior'])

    def test_archivar_vuelve_a_fabrica(self):
        fila = self._set('cobertura', 'exterior', 'azotea')
        self.assertEqual(self._keywords('cobertura', 'exterior'), ['azotea'])
        fila.active = False
        self.assertEqual(
            self._keywords('cobertura', 'exterior'),
            self.Flow._visar_vocabulario_originales('cobertura', 'exterior'))
        # ...y el campo de la pantalla lo dice, en vez de seguir enseñando lo
        # que ya no se aplica.
        self.assertEqual(
            fila.palabras_efectivas.splitlines(),
            self.Flow._visar_vocabulario_originales('cobertura', 'exterior'))

    def test_una_opcion_que_no_existe_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            self.Vocab.create({
                'paso': 'cobertura', 'opcion': 'el_patio', 'palabras': 'azotea',
            })

    def test_la_salida_de_poliza_se_puede_ampliar(self):
        """Ampliar sigue siendo el caso normal: se parte de fábrica y se añade.

        Lo que cambia es que ahora hay que escribirlo entero —la fila manda—, y
        `palabras_originales` está al lado justo para poder copiarlo.
        """
        originales = self.Flow._visar_vocabulario_originales('poliza', 'no_gracias')
        self.assertIn('solo este servicio', originales)
        self._set('poliza', 'no_gracias',
                  '\n'.join(originales + ['nel', 'asi nomas']))
        overlay = self.Flow._visar_vocabulario_overlay()
        palabras = self.Flow._visar_vocabulario(overlay, 'poliza', 'no_gracias')
        self.assertIn('solo este servicio', palabras)
        self.assertEqual(palabras.count('nel'), 1)
        self.assertEqual(palabras[-1], 'asi nomas')

    def test_una_opcion_vaciada_no_reconoce_nada(self):
        """Vaciar es una respuesta legítima, y no puede resucitar el código.

        Si el overlay se saltara las listas vacías, vaciar una opción desde la
        pantalla devolvería en silencio las palabras de fábrica —guardas, no
        pasa nada, y el agente sigue haciendo lo de antes—.
        """
        self._set('poliza', 'no_gracias', '')
        overlay = self.Flow._visar_vocabulario_overlay()
        self.assertEqual(
            self.Flow._visar_vocabulario(overlay, 'poliza', 'no_gracias'), [])

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
        fila = self._set('motivo', 'correctivo', 'gusanos\ntengo\nTéngo')
        self.assertEqual(
            fila.palabras_efectivas.splitlines(),
            self._keywords('motivo', 'correctivo'))
        # Se pidió 'tengo' dos veces (una con acento distinto): aparece una.
        self.assertEqual(
            len([p for p in fila.palabras_efectivas.splitlines() if p == 'tengo']), 1)

    def test_las_opciones_validas_se_ensenan(self):
        fila = self.Vocab.new({'paso': 'cobertura'})
        self.assertEqual(fila.opciones_validas, 'interior, exterior, ambos')
