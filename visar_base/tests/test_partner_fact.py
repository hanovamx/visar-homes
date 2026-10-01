# -*- coding: utf-8 -*-
"""`visar.partner.fact`: lo que sabemos de un cliente y de cada domicilio.

Lo que se fija aquí, y por qué cada cosa importa:

  * **el ámbito se respeta.** Un dato del lugar NO puede colgarse del cliente. Es
    la regla que da sentido al modelo: un cliente con tres direcciones y
    «Departamento» en su ficha no tiene un dato incompleto, tiene uno FALSO, y el
    agente lo repetiría con la misma confianza que uno bueno;
  * un valor por ranura: dos filas serían dos verdades;
  * **una corrección a mano no se pisa.** La derivación desde la hoja corre en
    cada guardado, así que sin esa regla lo corregido duraría hasta el siguiente
    guardado del técnico — el fallo de «lo edité y se perdió», automatizado;
  * el escritor nunca levanta: un dato de contexto no puede costarle nada a nadie.
"""
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPartnerFact(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Fact = cls.env['visar.partner.fact']
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente con casas'})
        cls.casa = cls.env['res.partner'].create({
            'name': 'Casa', 'type': 'delivery', 'parent_id': cls.cliente.id,
            'street': 'Calle 1 No. 10', 'street2': 'Las Torres', 'zip': '64000'})
        cls.local = cls.env['res.partner'].create({
            'name': 'Local', 'type': 'delivery', 'parent_id': cls.cliente.id,
            'street': 'Calle 2 No. 20', 'street2': 'Centro', 'zip': '64010'})

    # ------------------------------------------------------------------
    # El ámbito: la regla que da sentido a todo
    # ------------------------------------------------------------------

    def test_un_dato_del_lugar_va_en_el_domicilio(self):
        fact = self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        self.assertTrue(fact)
        self.assertEqual(fact.ambito, 'domicilio')

    def test_un_dato_del_lugar_NO_se_puede_colgar_del_cliente(self):
        """El error que este modelo existe para no cometer."""
        with self.assertRaises(ValidationError):
            self.Fact.create({'partner_id': self.cliente.id,
                              'slot': 'tipo_inmueble', 'value': 'Departamento'})

    def test_un_dato_de_la_persona_va_en_el_cliente(self):
        fact = self.Fact._visar_fact_set(
            self.cliente, 'preferencia_horario', 'sábados')
        self.assertTrue(fact)
        self.assertEqual(fact.ambito, 'cliente')

    def test_un_dato_de_la_persona_NO_va_en_un_domicilio(self):
        with self.assertRaises(ValidationError):
            self.Fact.create({'partner_id': self.casa.id,
                              'slot': 'preferencia_horario', 'value': 'sábados'})

    def test_dos_domicilios_llevan_datos_DISTINTOS(self):
        """El caso que motivó el diseño: casa y local del mismo cliente."""
        self.Fact._visar_fact_set(self.casa, 'tipo_inmueble', 'Casa')
        self.Fact._visar_fact_set(self.local, 'tipo_inmueble', 'Local comercial')
        self.assertEqual(
            self.Fact._visar_facts_de(self.casa)['tipo_inmueble'], 'Casa')
        self.assertEqual(
            self.Fact._visar_facts_de(self.local)['tipo_inmueble'], 'Local comercial')

    # ------------------------------------------------------------------
    # Un valor por ranura
    # ------------------------------------------------------------------

    def test_poner_el_mismo_dato_dos_veces_lo_actualiza(self):
        primero = self.Fact._visar_fact_set(self.casa, 'mascotas', 'un perro')
        segundo = self.Fact._visar_fact_set(self.casa, 'mascotas', 'dos perros')
        self.assertEqual(primero, segundo)
        self.assertEqual(segundo.value, 'dos perros')

    def test_dos_filas_de_la_misma_ranura_no_se_guardan(self):
        self.Fact._visar_fact_set(self.casa, 'mascotas', 'un perro')
        with self.assertRaises(ValidationError):
            self.Fact.create({'partner_id': self.casa.id,
                              'slot': 'mascotas', 'value': 'un gato'})

    # ------------------------------------------------------------------
    # El código no pisa a una persona
    # ------------------------------------------------------------------

    def test_la_hoja_no_sobrescribe_lo_que_escribio_una_persona(self):
        """Sin esto, una corrección duraría hasta el siguiente guardado."""
        self.Fact._visar_fact_set(self.casa, 'acceso', 'Portón con candado',
                                  origen='persona')
        self.Fact._visar_fact_set(self.casa, 'acceso', 'nada', origen='hoja')
        self.assertEqual(
            self.Fact._visar_facts_de(self.casa)['acceso'], 'Portón con candado')

    def test_la_hoja_SI_actualiza_lo_que_puso_la_hoja(self):
        self.Fact._visar_fact_set(self.casa, 'acceso', 'viejo', origen='hoja')
        self.Fact._visar_fact_set(self.casa, 'acceso', 'nuevo', origen='hoja')
        self.assertEqual(self.Fact._visar_facts_de(self.casa)['acceso'], 'nuevo')

    def test_una_persona_SI_puede_corregir_lo_de_la_hoja(self):
        self.Fact._visar_fact_set(self.casa, 'acceso', 'de la hoja', origen='hoja')
        self.Fact._visar_fact_set(self.casa, 'acceso', 'a mano', origen='persona')
        self.assertEqual(self.Fact._visar_facts_de(self.casa)['acceso'], 'a mano')

    # ------------------------------------------------------------------
    # Robustez y lectura
    # ------------------------------------------------------------------

    def test_el_escritor_nunca_levanta(self):
        """Un dato de contexto no puede costarle nada a nadie."""
        self.assertFalse(self.Fact._visar_fact_set(self.casa, 'inventada', 'x'))
        self.assertFalse(self.Fact._visar_fact_set(self.casa, 'mascotas', '   '))
        self.assertFalse(self.Fact._visar_fact_set(
            self.env['res.partner'], 'mascotas', 'un perro'))

    def test_un_valor_mal_colocado_no_levanta_por_el_escritor(self):
        """`_visar_fact_set` traga la ValidationError del ámbito y devuelve vacío."""
        self.assertFalse(
            self.Fact._visar_fact_set(self.cliente, 'tipo_inmueble', 'Casa'))

    def test_archivar_lo_quita_de_la_lectura(self):
        fact = self.Fact._visar_fact_set(self.casa, 'mascotas', 'un perro')
        self.assertIn('mascotas', self.Fact._visar_facts_de(self.casa))
        fact.active = False
        self.assertNotIn('mascotas', self.Fact._visar_facts_de(self.casa))

    def test_los_domicilios_del_cliente_se_encuentran(self):
        self.assertEqual(
            self.cliente._visar_domicilios_de_servicio(), self.casa | self.local)

    def test_los_domicilios_se_encuentran_desde_un_contacto_hijo(self):
        """Quien escribe puede ser un contacto hijo: sus hermanos son las casas."""
        self.assertEqual(
            self.casa._visar_domicilios_de_servicio(), self.casa | self.local)
