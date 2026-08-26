# -*- coding: utf-8 -*-
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentPromptRoutes(TransactionCase):
    """`visar.agent.prompt`: un prompt BASE + una memoria por ruta.

    Lo que se fija aqui:

      * El BASE se lee con `ruta` vacia, **pase lo que pase con las secuencias**.
        Es la prueba de regresion de todo el cambio: el lector de antes buscaba
        con dominio VACIO, asi que la primera memoria de ruta con secuencia baja
        se habria convertido en el prompt base — 1 KB sustituyendo a 20 000, sin
        excepcion y cacheado 15 minutos.
      * Los lectores **nunca levantan**. Si esta RPC falla y el runtime aun no
        tiene nada cacheado, `RuntimeConfigCache.refresh` re-lanza y el servicio
        no le contesta a nadie.
      * "Ausente" y "vacia" son lo mismo para el runtime: una ruta sin registro,
        archivada o en blanco no aparece en el dict.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Prompt = cls.env['visar.agent.prompt']
        # El modulo SIEMBRA cinco memorias (data/visar_agent_prompt_routes.xml),
        # y produccion tiene su base. Se archivan todas dentro de la transaccion
        # para que estas pruebas describan la REGLA y no la siembra.
        cls.Prompt.with_context(active_test=False).search([]).write({'active': False})
        cls.base = cls.Prompt.create({
            'name': "Base de prueba", 'body': "BASE" * 100, 'sequence': 10,
        })

    def _memoria(self, ruta, body="memoria", sequence=20):
        return self.Prompt.create({
            'name': f"Memoria {ruta}", 'ruta': ruta,
            'body': body, 'sequence': sequence,
        })

    # ------------------------------------------------------------------

    def test_el_base_ignora_las_memorias_de_ruta(self):
        """Una memoria con secuencia MENOR que el base no se vuelve el base.

        Es exactamente lo que hacia el `search([])` de antes.
        """
        self._memoria('info', body="memoria corta", sequence=1)
        self.assertEqual(self.Prompt._agent_active_body(), self.base.body)

    def test_memoria_por_ruta(self):
        self._memoria('info', body="lo de informacion")
        self.assertEqual(self.Prompt._agent_route_body('info'), "lo de informacion")
        self.assertIsNone(self.Prompt._agent_route_body('schedule'))

    def test_una_ruta_inventada_no_revienta(self):
        self.assertIsNone(self.Prompt._agent_route_body('no_existe'))

    def test_lo_archivado_no_viaja(self):
        memoria = self._memoria('info')
        memoria.active = False
        self.assertIsNone(self.Prompt._agent_route_body('info'))
        self.assertNotIn('info', self.Prompt._agent_route_memories())

    def test_cuerpo_en_blanco_no_viaja(self):
        """`body` es required, pero "   " si se puede guardar.

        Una memoria en blanco significa "ninguna", no "inyecta un bloque vacio".
        """
        self._memoria('info', body="   ")
        self.assertIsNone(self.Prompt._agent_route_body('info'))
        self.assertNotIn('info', self.Prompt._agent_route_memories())

    def test_duplicados_gana_el_de_menor_secuencia(self):
        self._memoria('info', body="la de secuencia 30", sequence=30)
        self._memoria('info', body="la de secuencia 5", sequence=5)
        self.assertEqual(self.Prompt._agent_route_body('info'), "la de secuencia 5")

    def test_a_igualdad_de_secuencia_gana_el_mas_antiguo(self):
        """El titular gana. En produccion el titular es siempre el de id menor."""
        primera = self._memoria('info', body="la primera", sequence=20)
        self._memoria('info', body="la segunda", sequence=20)
        self.assertEqual(self.Prompt._agent_route_body('info'), primera.body)

    def test_payload_de_runtime(self):
        self._memoria('info')
        self._memoria('schedule')
        payload = self.env['visar.agent.tools'].agent_runtime_config()
        self.assertEqual(
            set(payload), {'generated_at', 'prompt', 'route_prompts', 'llm'})
        self.assertIsInstance(payload['route_prompts'], dict)
        self.assertEqual(set(payload['route_prompts']), {'info', 'schedule'})
        # El base no se ve afectado por que existan memorias.
        self.assertEqual(payload['prompt'], self.base.body)

    def test_sin_ningun_registro_no_revienta(self):
        """El caso de una base recien instalada, clavado."""
        self.base.active = False
        payload = self.env['visar.agent.tools'].agent_runtime_config()
        self.assertIsNone(payload['prompt'])
        self.assertEqual(payload['route_prompts'], {})

    def test_lo_lee_el_usuario_del_agente(self):
        """Los metodos NO usan sudo(): la ACL de solo lectura es el limite real.

        Si el campo nuevo se quedara fuera de los permisos, el runtime dejaria de
        poder leer su propio prompt.
        """
        self._memoria('info', body="lo de informacion")
        usuario = self.env.ref('visar_whatsapp_agent.user_whatsapp_agent')
        payload = self.env['visar.agent.tools'].with_user(
            usuario).agent_runtime_config()
        self.assertEqual(payload['route_prompts'].get('info'), "lo de informacion")

    def test_es_vigente_marca_uno_por_ruta(self):
        gana = self._memoria('info', body="gana", sequence=5)
        pierde = self._memoria('info', body="pierde", sequence=30)
        self.assertTrue(gana.es_vigente)
        self.assertFalse(pierde.es_vigente)
        self.assertTrue(self.base.es_vigente)
