# -*- coding: utf-8 -*-
"""`agent_partner_facts`: lo que el agente sabe, y lo que NO puede hacer con eso.

Lo que se fija aquí:

  * el bloque trae los datos del cliente **y de cada domicilio**, separados — es
    la razón de ser del diseño: un cliente agenda para su casa y para su local;
  * **la regla de nivel va DENTRO del bloque**, no en un comentario. El modelo
    tiene que leer que esto informa y no contesta, y que los metros, el
    interior/exterior y el CP se preguntan siempre. Si esa frase desaparece, el
    nivel 1 deja de existir y nada más falla;
  * con varias direcciones se le dice que **pregunte cuál**. Eso es lo que gana
    el nivel 1: preguntar mejor en vez de suponer;
  * la **ambigüedad** no manda nada. Un fact equivocado no es un dato que falta:
    es el agente hablándole a alguien de la casa de otra persona;
  * la dirección se nombra por colonia o calle, nunca completa: leerle su calle y
    su número a quien solo preguntó un precio suena a vigilancia;
  * nunca levanta.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentFacts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.T = cls.env['visar.agent.tools']
        cls.Fact = cls.env['visar.partner.fact']
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente de facts', 'phone': '528119990001'})
        cls.casa = cls.env['res.partner'].create({
            'name': 'Casa', 'type': 'delivery', 'parent_id': cls.cliente.id,
            'street': 'Calle 1 No. 10', 'street2': 'Las Torres', 'zip': '64000'})
        cls.local = cls.env['res.partner'].create({
            'name': 'Local', 'type': 'delivery', 'parent_id': cls.cliente.id,
            'street': 'Calle 2 No. 20', 'street2': 'Centro', 'zip': '64010'})

    def _facts(self, phone='528119990001'):
        return self.T.agent_partner_facts({'phone': phone})

    # ------------------------------------------------------------------
    # Qué llega
    # ------------------------------------------------------------------

    def test_con_varias_direcciones_hay_bloque_AUNQUE_no_sepamos_nada(self):
        """La corrección del 1-oct-2026, y la destapó el usuario preguntando.

        La primera versión solo listaba los domicilios CON datos, así que un
        cliente con cuatro direcciones y cero facts —el 100% de los casos al
        desplegar— recibía un bloque vacío y el agente arrancaba a ciegas. El
        valor de desambiguar no depende de saber algo del sitio: depende de que
        haya varios.
        """
        datos = self._facts()
        self.assertTrue(datos['block'])
        self.assertIn('Las Torres', datos['block'])
        self.assertIn('Centro', datos['block'])
        self.assertEqual(datos['cliente'], {})

    def test_un_telefono_desconocido_no_devuelve_nada(self):
        datos = self._facts('529999999999')
        self.assertEqual(datos['block'], '')
        self.assertIsNone(datos['partner_id'])

    def test_los_datos_del_cliente_entran(self):
        self.Fact._visar_fact_set(self.cliente, 'preferencia_horario', 'sábados')
        datos = self._facts()
        self.assertIn('sábados', datos['block'])
        self.assertEqual(datos['cliente'], {'preferencia_horario': 'sábados'})

    def test_los_datos_de_cada_domicilio_van_SEPARADOS(self):
        """La razón de ser del diseño."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        self.Fact._visar_fact_set(self.local, 'tipo_inmueble', 'Local comercial')
        datos = self._facts()
        por_nombre = {d['nombre']: d['facts'] for d in datos['domicilios']}
        self.assertEqual(por_nombre['Las Torres']['tipo_inmueble'], 'Casa')
        self.assertEqual(por_nombre['Centro']['tipo_inmueble'], 'Local comercial')
        self.assertIn('Las Torres', datos['block'])
        self.assertIn('Centro', datos['block'])

    def test_la_direccion_se_nombra_por_colonia_no_completa(self):
        """Leerle su calle y su número a quien preguntó un precio suena a vigilancia."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        bloque = self._facts()['block']
        self.assertIn('Las Torres', bloque)
        self.assertNotIn('Calle 1 No. 10', bloque)

    # ------------------------------------------------------------------
    # La regla de nivel 1, DENTRO del bloque
    # ------------------------------------------------------------------

    def test_el_bloque_prohibe_contestar_el_cuestionario(self):
        """Si esta frase desaparece, el nivel 1 deja de existir y nada más falla."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        bloque = self._facts()['block']
        self.assertIn('NO contestes', bloque)

    def test_el_bloque_nombra_las_entradas_de_PRECIO(self):
        """Lo concreto es lo que un modelo pequeño sigue; «ten cuidado» no."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        bloque = self._facts()['block']
        for dato in ('metros', 'interior', 'codigo postal'):
            self.assertIn(dato, bloque.lower(), dato)

    def test_con_varias_direcciones_se_le_dice_que_pregunte(self):
        """Lo que gana el nivel 1: preguntar en vez de suponer."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        self.Fact._visar_fact_set(self.local, 'tipo_inmueble', 'Local comercial')
        self.assertIn('VARIAS', self._facts()['block'])

    def test_con_una_sola_direccion_no_se_le_dice_nada_de_eso(self):
        """No hay nada que desambiguar, y nombrársela invitaría a darla por buena."""
        self.local.unlink()
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        self.assertNotIn('VARIAS', self._facts()['block'])

    def test_una_sola_direccion_y_sin_datos_no_deja_bloque(self):
        self.local.unlink()
        self.assertEqual(self._facts()['block'], '')

    def test_SIEMPRE_se_ofrece_la_salida_de_una_direccion_nueva(self):
        """Tener tres casas con nosotros no impide mudarse ni pedirlo para otra.

        Sin esta salida el agente acorrala al cliente entre opciones que quizás
        no incluyen la que quiere.
        """
        bloque = self._facts()['block']
        self.assertIn('NUEVA', bloque)

    def test_el_bloque_recuerda_que_el_CP_se_pide_igual(self):
        """Tener direcciones registradas no es haber contestado el precio."""
        bloque = self._facts()['block'].lower()
        self.assertIn('codigo postal', bloque)
        self.assertIn('metros', bloque)

    def test_con_demasiadas_direcciones_no_se_enumeran(self):
        """En producción hay un cliente con 31 y otro con 13.

        Leerle trece colonias a alguien que preguntó un precio es peor que no
        decirle ninguna — pero el aviso de que NO suponga sigue haciendo falta.
        """
        for i in range(6):
            self.env['res.partner'].create({
                'name': 'Dir %d' % i, 'type': 'delivery',
                'parent_id': self.cliente.id,
                'street': 'Calle %d' % i, 'street2': 'Colonia %d' % i})
        bloque = self._facts()['block']
        self.assertIn('demasiadas', bloque)
        self.assertNotIn('Colonia 0', bloque)
        self.assertIn('NUEVA', bloque, "y la salida sigue ahí")

    def test_dos_direcciones_en_la_misma_colonia_se_desempatan(self):
        """Si no, el agente ofrece dos veces «Las Torres» y no se puede contestar."""
        self.env['res.partner'].create({
            'name': 'Otra en Las Torres', 'type': 'delivery',
            'parent_id': self.cliente.id,
            'street': 'Calle 9 No. 99', 'street2': 'Las Torres'})
        bloque = self._facts()['block']
        self.assertIn('Las Torres (Calle 1 No. 10)', bloque)
        self.assertIn('Las Torres (Calle 9 No. 99)', bloque)

    def test_el_aviso_de_varias_va_ANTES_de_los_datos(self):
        """Detrás ya habría leído un tipo de inmueble concreto como bueno."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        self.Fact._visar_fact_set(self.local, 'tipo_inmueble', 'Local comercial')
        bloque = self._facts()['block']
        self.assertLess(bloque.index('VARIAS'), bloque.index('Las Torres'))

    # ------------------------------------------------------------------
    # Privacidad y robustez
    # ------------------------------------------------------------------

    def test_dos_clientes_con_el_mismo_numero_no_devuelven_nada(self):
        """Un fact equivocado es hablarle a alguien de la casa de otra persona."""
        self.env['res.partner'].create({
            'name': 'Otro con el mismo numero', 'phone': '528119990001'})
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        datos = self._facts()
        self.assertEqual(datos['block'], '')
        self.assertIsNone(datos['partner_id'])

    def test_nunca_levanta(self):
        with patch.object(type(self.T), '_agent_partner_facts_ahora',
                          side_effect=RuntimeError("boom")):
            datos = self._facts()
        self.assertEqual(datos, {'block': '', 'cliente': {}, 'domicilios': [],
                                 'partner_id': None})

    def test_el_contrato_es_estable(self):
        self.assertEqual(set(self._facts()),
                         {'block', 'cliente', 'domicilios', 'partner_id'})
