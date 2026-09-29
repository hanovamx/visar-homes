# -*- coding: utf-8 -*-
"""`visar.agent.correccion`: arreglos de conducta que ganan al prompt.

Lo que se fija aqui, y por que cada cosa puede romperse en silencio:

  * el ambito se resuelve EN ODOO. Si el filtrado se colara al runtime, el
    modelo recibiria correcciones de rutas donde no aplican y habria que
    explicarle que ignore la mitad de una lista — justo lo contrario de lo que
    se busca con un modelo pequenio;
  * una correccion global NO entra en el cuestionario salvo que se marque. Las
    reglas de `schedule` son de carga, y una correccion bienintencionada
    ("termina preguntando si necesita algo mas") reproduce el fallo de la doble
    pregunta que esa memoria dedica tres parrafos a evitar;
  * el TOPE existe de verdad. Sin el, esto se convierte en un segundo prompt que
    no mantiene nadie, y degrada tambien a las correcciones que ya funcionaban;
  * la cabecera va una sola vez y las reglas numeradas. El formato lo decide
    `_render`, y la vista previa pasa por el mismo sitio: una pantalla que
    ensenie algo distinto de lo que se manda no sirve para nada;
  * el lector NUNCA levanta. Si esta RPC falla y el runtime no tiene nada
    cacheado, `RuntimeConfigCache.refresh` re-lanza y el servicio deja de
    contestarle a todo el mundo.
"""
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_whatsapp_agent.models.visar_agent_correccion import (
    CABECERA,
    MAX_ACTIVAS,
)

TODAS = {'reception', 'info', 'schedule', 'existing', 'other'}


@tagged('post_install', '-at_install')
class TestAgentCorreccion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.C = cls.env['visar.agent.correccion']
        # El modulo no siembra ninguna, pero una base de prueba puede traerlas.
        cls.C.with_context(active_test=False).search([]).write({'active': False})

    def _corr(self, texto="No saludes con «Holi»", **kw):
        return self.C.create(dict({'texto': texto}, **kw))

    # ------------------------------------------------------------------
    # Ambito
    # ------------------------------------------------------------------

    def test_una_global_va_a_todas_menos_al_cuestionario(self):
        """El valor por defecto, y es el que protege el flujo de agendado."""
        self._corr()
        payload = self.C._agent_payload()
        self.assertEqual(set(payload), TODAS - {'schedule'})

    def test_una_global_marcada_si_entra_al_cuestionario(self):
        self._corr(en_cuestionario=True)
        self.assertEqual(set(self.C._agent_payload()), TODAS)

    def test_una_de_ruta_solo_va_a_su_ruta(self):
        """El modelo no ve las de otras rutas: ni siquiera se le mandan."""
        self._corr(texto="No prometas tiempos", ambito='ruta', ruta='other')
        payload = self.C._agent_payload()
        self.assertEqual(set(payload), {'other'})
        self.assertIn("No prometas tiempos", payload['other'])

    def test_una_de_ruta_puede_ser_del_cuestionario(self):
        """Ahi el ambito ya es explicito: no hace falta el interruptor."""
        self._corr(ambito='ruta', ruta='schedule')
        self.assertEqual(set(self.C._agent_payload()), {'schedule'})

    def test_el_ambito_de_ruta_exige_ruta(self):
        with self.assertRaises(ValidationError):
            self._corr(ambito='ruta')

    # ------------------------------------------------------------------
    # El bloque que recibe el modelo
    # ------------------------------------------------------------------

    def test_la_cabecera_va_una_sola_vez_y_las_reglas_numeradas(self):
        self._corr("Primera", sequence=10)
        self._corr("Segunda", sequence=20)
        bloque = self.C._agent_payload()['reception']
        self.assertEqual(bloque.count(CABECERA), 1)
        self.assertIn("1. Primera", bloque)
        self.assertIn("2. Segunda", bloque)
        self.assertLess(bloque.index("1. Primera"), bloque.index("2. Segunda"))

    def test_el_orden_lo_manda_la_secuencia(self):
        self._corr("La que va despues", sequence=50)
        self._corr("La que va primero", sequence=1)
        bloque = self.C._agent_payload()['reception']
        self.assertLess(bloque.index("La que va primero"),
                        bloque.index("La que va despues"))

    def test_la_vista_previa_es_lo_que_se_manda(self):
        """La pantalla no puede decir una cosa y el agente recibir otra."""
        uno = self._corr("Primera", sequence=10)
        self._corr("Segunda", sequence=20)
        self.assertEqual(uno.vista_previa, self.C._agent_payload()['reception'])

    def test_la_vista_previa_incluye_la_que_se_esta_escribiendo(self):
        """Es el unico momento en que alguien la mira: tiene que salir ahi.

        Un registro sin guardar no esta en la busqueda, asi que una vista previa
        que solo leyera de la BD ensenaria el bloque SIN la correccion que se
        acaba de teclear.
        """
        nueva = self.C.new({'texto': "Recien tecleada", 'ambito': 'global'})
        self.assertIn("Recien tecleada", nueva.vista_previa)

    def test_una_archivada_no_viaja(self):
        corr = self._corr()
        self.assertTrue(self.C._agent_payload())
        corr.active = False
        self.assertEqual(self.C._agent_payload(), {})

    def test_sin_correcciones_el_payload_es_un_dict_vacio(self):
        """Nunca None: el runtime hace `.get(ruta)` sobre esto."""
        self.assertEqual(self.C._agent_payload(), {})

    # ------------------------------------------------------------------
    # Las reglas que mantienen esto usable
    # ------------------------------------------------------------------

    def test_una_correccion_es_una_linea(self):
        with self.assertRaises(ValidationError):
            self._corr("Primera cosa\nSegunda cosa")

    def test_el_tope_se_respeta(self):
        for i in range(MAX_ACTIVAS):
            self._corr("Correccion %d" % i)
        with self.assertRaises(ValidationError):
            self._corr("La que sobra")

    def test_archivar_libera_hueco(self):
        """La salida del tope: retirar una es lo que obliga a revisarlas."""
        primeras = self.C
        for i in range(MAX_ACTIVAS):
            primeras |= self._corr("Correccion %d" % i)
        primeras[0].active = False
        self._corr("La que entra en su lugar")  # ya no levanta
        self.assertEqual(self.C.search_count([]), MAX_ACTIVAS)

    def test_las_archivadas_no_cuentan_para_el_tope(self):
        for i in range(MAX_ACTIVAS + 5):
            self._corr("Vieja %d" % i).active = False
        self._corr("La nueva")
        self.assertEqual(self.C.search_count([]), 1)

    # ------------------------------------------------------------------
    # Robustez y consola
    # ------------------------------------------------------------------

    def test_el_lector_nunca_levanta(self):
        """Degradar a {} es aceptable; tumbar `agent_runtime_config`, no."""
        with patch.object(type(self.C), 'search', side_effect=RuntimeError("boom")):
            self.assertEqual(self.C._agent_payload(), {})

    def test_viaja_en_la_config_del_runtime(self):
        self._corr()
        config = self.env['visar.agent.tools'].agent_runtime_config()
        self.assertIn('correcciones', config)
        self.assertIsInstance(config['correcciones'], dict)
        self.assertIn('reception', config['correcciones'])

    def test_las_rutas_donde_aplica_se_dicen_con_palabras(self):
        corr = self._corr()
        self.assertIn("Recepcion", corr.rutas_donde_aplica)
        self.assertNotIn("Agendar", corr.rutas_donde_aplica)

    def test_los_dias_cuentan_desde_que_se_creo(self):
        self.assertEqual(self._corr().dias, 0)

    def test_el_motivo_no_se_le_manda_al_agente(self):
        """Es una nota para quien la lea en tres meses, no contexto del modelo."""
        self._corr(motivo="Abrio tres conversaciones con «Holi» el 29-sep.")
        self.assertNotIn("29-sep", self.C._agent_payload()['reception'])
