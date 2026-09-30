# -*- coding: utf-8 -*-
"""`visar.agent.contacto`: una fila por telefono que le ESCRIBIO al agente.

Lo que se fija aqui, y por que cada cosa puede romperse sin que nadie lo note:

  * la clave es el `nat10`, la MISMA que `_agent_find_partner`. Con otra
    normalizacion esta lista diria que un contacto no es cliente mientras el
    agente lo saluda por su nombre;
  * `partner_id` vacio NO es un error: el agente crea el cliente solo al cerrar
    una reserva, asi que la mayoria de quien escribe no llega a tener ficha;
  * el cliente se vuelve a resolver, no se fija al crear. Quien escribio antes de
    ser cliente tiene que enlazarse despues, o la lista envejece mintiendo;
  * la AMBIGUEDAD no se adivina. Misma politica que el resto del modulo: dos
    partners con el mismo numero -> ninguno, porque equivocarse aqui es ensenar
    datos de otra persona;
  * los numeros internos se marcan. Los 177 telefonos de la suite de aceptacion
    empiezan con 999000: sin marcarlos, la lista nace con 177 contactos falsos;
  * el lector NUNCA levanta, igual que el resto de escrituras acotadas.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentContacto(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.C = cls.env['visar.agent.contacto']
        cls.Partner = cls.env['res.partner']

    def _tel(self, nat):
        """Un numero de WhatsApp (con lada) a partir de los 10 digitos."""
        return '52' + nat

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    def test_un_mensaje_crea_el_contacto(self):
        contacto = self.C._visar_registrar(self._tel('8110000001'))
        self.assertTrue(contacto)
        self.assertEqual(contacto.name, '8110000001')
        self.assertEqual(contacto.mensajes, 1)
        self.assertTrue(contacto.primer_mensaje)
        self.assertEqual(contacto.primer_mensaje, contacto.ultimo_mensaje)

    def test_el_segundo_mensaje_no_crea_otra_fila(self):
        primero = self.C._visar_registrar(self._tel('8110000002'))
        segundo = self.C._visar_registrar(self._tel('8110000002'))
        self.assertEqual(primero, segundo)
        self.assertEqual(segundo.mensajes, 2)

    def test_la_clave_es_el_numero_nacional(self):
        """Las dos formas del mismo telefono son UN contacto.

        Es la misma clave que `_agent_find_partner`. Si aqui se guardara el
        numero crudo, un cliente que escribe desde dos formatos apareceria dos
        veces y solo una estaria enlazada.
        """
        con_lada = self.C._visar_registrar('528110000003')
        sin_lada = self.C._visar_registrar('8110000003')
        self.assertEqual(con_lada, sin_lada)
        self.assertEqual(sin_lada.mensajes, 2)

    def test_sin_telefono_no_hay_contacto(self):
        self.assertFalse(self.C._visar_registrar(None))
        self.assertFalse(self.C._visar_registrar('   '))

    def test_se_guarda_la_ruta_y_el_numero_completo(self):
        contacto = self.C._visar_registrar('528110000004', ruta='existing')
        self.assertEqual(contacto.ultima_ruta, 'existing')
        self.assertEqual(contacto.phone, '528110000004')

    def test_una_ruta_inventada_no_se_guarda(self):
        """Degradar, no reventar: el runtime manda la ruta y puede cambiarla."""
        contacto = self.C._visar_registrar(self._tel('8110000005'), ruta='marte')
        self.assertFalse(contacto.ultima_ruta)

    def test_la_ruta_se_actualiza_con_el_ultimo_mensaje(self):
        self.C._visar_registrar(self._tel('8110000006'), ruta='reception')
        contacto = self.C._visar_registrar(self._tel('8110000006'), ruta='other')
        self.assertEqual(contacto.ultima_ruta, 'other')

    # ------------------------------------------------------------------
    # El cliente
    # ------------------------------------------------------------------

    def test_sin_cliente_no_es_un_error(self):
        contacto = self.C._visar_registrar(self._tel('8110000007'))
        self.assertFalse(contacto.partner_id)
        self.assertFalse(contacto.partner_ambiguo)

    def test_se_enlaza_al_cliente_que_ya_existe(self):
        partner = self.Partner.create(
            {'name': 'Cliente conocido', 'phone': '528110000008'})
        contacto = self.C._visar_registrar(self._tel('8110000008'))
        self.assertEqual(contacto.partner_id, partner)

    def test_el_enlace_se_rehace_en_el_siguiente_mensaje(self):
        """Quien escribio ANTES de ser cliente se enlaza cuando vuelve a escribir."""
        contacto = self.C._visar_registrar(self._tel('8110000009'))
        self.assertFalse(contacto.partner_id)
        partner = self.Partner.create(
            {'name': 'Ya reservo', 'phone': '528110000009'})
        contacto = self.C._visar_registrar(self._tel('8110000009'))
        self.assertEqual(contacto.partner_id, partner)

    def test_dos_clientes_con_el_mismo_numero_no_se_adivinan(self):
        """Equivocarse aqui significa ensenar los datos de otra persona."""
        self.Partner.create({'name': 'Uno', 'phone': '528110000010'})
        self.Partner.create({'name': 'Otro', 'phone': '528110000010'})
        contacto = self.C._visar_registrar(self._tel('8110000010'))
        self.assertFalse(contacto.partner_id)
        self.assertTrue(contacto.partner_ambiguo)

    def test_al_unir_los_duplicados_el_enlace_se_arregla(self):
        uno = self.Partner.create({'name': 'Uno', 'phone': '528110000011'})
        otro = self.Partner.create({'name': 'Otro', 'phone': '528110000011'})
        contacto = self.C._visar_registrar(self._tel('8110000011'))
        self.assertTrue(contacto.partner_ambiguo)
        otro.phone = '528119999999'
        self.C._visar_cron_resolver_partners()
        self.assertEqual(contacto.partner_id, uno)
        self.assertFalse(contacto.partner_ambiguo)

    # ------------------------------------------------------------------
    # Enlace tardio
    # ------------------------------------------------------------------

    def test_el_cron_enlaza_a_quien_ya_no_escribe(self):
        """El caso que el enlace por mensaje NO cubre.

        Alguien escribe una vez, reserva por la web y no vuelve a escribir: sin
        el cron se queda «sin cliente» para siempre, porque el enlace solo se
        rehace cuando entra otro mensaje suyo.
        """
        contacto = self.C._visar_registrar(self._tel('8110000012'))
        self.assertFalse(contacto.partner_id)
        partner = self.Partner.create(
            {'name': 'Reservo por la web', 'phone': '528110000012'})
        self.C._visar_cron_resolver_partners()
        self.assertEqual(contacto.partner_id, partner)

    def test_el_cron_no_pisa_un_enlace_puesto_a_mano(self):
        """Si alguien lo corrigio, esa correccion manda."""
        contacto = self.C._visar_registrar(self._tel('8110000013'))
        a_mano = self.Partner.create({'name': 'Puesto a mano'})
        contacto.partner_id = a_mano
        self.C._visar_cron_resolver_partners()
        self.assertEqual(contacto.partner_id, a_mano)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def test_los_numeros_de_prueba_se_marcan(self):
        """Los 177 telefonos de la suite empiezan con 999000."""
        contacto = self.C._visar_registrar('9990001234')
        self.assertTrue(contacto.interno)

    def test_un_cliente_normal_no_es_interno(self):
        self.assertFalse(self.C._visar_registrar(self._tel('8110000014')).interno)

    # ------------------------------------------------------------------
    # Robustez y RPC
    # ------------------------------------------------------------------

    def test_el_registro_nunca_levanta(self):
        """Una lista de contactos vale menos que la respuesta al cliente."""
        with patch.object(type(self.C), '_visar_registrar_ahora',
                          side_effect=RuntimeError("boom")):
            self.assertFalse(self.C._visar_registrar(self._tel('8110000015')))

    def test_el_rpc_devuelve_el_contrato(self):
        resultado = self.env['visar.agent.tools'].agent_track_inbound({
            'phone': self._tel('8110000016'), 'ruta': 'info'})
        self.assertEqual(set(resultado), {'contacto_id', 'partner_id', 'mensajes'})
        self.assertTrue(resultado['contacto_id'])
        self.assertEqual(resultado['mensajes'], 1)

    def test_el_rpc_sin_telefono_no_revienta(self):
        resultado = self.env['visar.agent.tools'].agent_track_inbound({})
        self.assertEqual(resultado,
                         {'contacto_id': None, 'partner_id': None, 'mensajes': 0})

    def test_el_nombre_visible_prefiere_el_del_cliente(self):
        self.Partner.create({'name': 'Con nombre', 'phone': '528110000017'})
        contacto = self.C._visar_registrar(self._tel('8110000017'))
        self.assertEqual(contacto.display_name, 'Con nombre')
        sin = self.C._visar_registrar(self._tel('8110000018'))
        self.assertEqual(sin.display_name, '8110000018')
