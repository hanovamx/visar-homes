# -*- coding: utf-8 -*-
"""`agent_save_address`: la direccion se guarda al confirmarla (14-oct-2026).

Antes solo se guardaba al generar la liga de pago. Aqui se fija lo que cuesta
caro si se rompe: que no se cree un cliente sin nombre, que lo creado quede
marcado, que no se acumulen direcciones de prueba, y que la limpieza no archive
a nadie que si compro.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

CP = '64700'
NUEVO = '5219997776655'
DIRECCION = {'street': 'Filosofos', 'ext_num': '315', 'int_num': '',
             'neighborhood': 'Tecnologico', 'zip': CP, 'city': 'Monterrey'}


@tagged('post_install', '-at_install')
class TestAgentSaveAddress(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Partner = cls.env['res.partner'].sudo()
        cls.Param = cls.env['ir.config_parameter'].sudo()
        cls.Param.set_param('visar.chat_address.enabled', '1')
        ZoneCp = cls.env['visar.zone.cp'].sudo()
        zona = cls.env['visar.zone'].search([], limit=1)
        registro = ZoneCp._get_cp_record(CP)
        if registro:
            registro.write({'zone_id': zona.id})
        else:
            ZoneCp.create({'name': CP, 'zone_id': zona.id, 'municipality': 'Monterrey'})

    def _guardar(self, **cambios):
        payload = {'phone': NUEVO, 'name': 'Maria Lopez',
                   'delivery_address': dict(DIRECCION)}
        payload.update(cambios)
        return self.Tools.agent_save_address(payload)

    def _cliente(self):
        return self.Partner.search([('visar_phone_nat10', '=', NUEVO[-10:])])

    def test_apagado_no_guarda_nada(self):
        self.Param.set_param('visar.chat_address.enabled', '0')
        resultado = self._guardar()
        self.assertEqual((resultado['saved'], resultado['reason']), (False, 'disabled'))
        self.assertFalse(self._cliente())

    def test_un_numero_nuevo_sin_nombre_no_estrena_ficha(self):
        for nombre in (None, '', 'ok', '12345'):
            resultado = self._guardar(name=nombre)
            self.assertEqual(resultado['reason'], 'name_required', nombre)
        self.assertFalse(self._cliente())

    def test_un_numero_nuevo_con_nombre_estrena_ficha_y_direccion(self):
        resultado = self._guardar()
        self.assertTrue(resultado['saved'])
        self.assertTrue(resultado['partner_created'])
        self.assertTrue(resultado['address_created'])
        cliente = self._cliente()
        self.assertEqual(cliente.name, 'Maria Lopez')
        self.assertTrue(cliente.visar_chat_sin_reserva)
        domicilio = cliente._visar_domicilios_de_servicio()
        self.assertEqual(len(domicilio), 1)
        self.assertEqual(domicilio.street, 'Filosofos No. 315')
        self.assertEqual(domicilio.zip, CP)
        self.assertTrue(domicilio.visar_chat_sin_reserva)
        # Lo que importa al cliente: ya no se le pide el nombre, y la proxima
        # conversacion el agente recibe su direccion.
        self.assertFalse(self.Tools._agent_booking_needs_name(NUEVO))
        facts = self.Tools.agent_partner_facts({'phone': NUEVO})
        self.assertEqual(len(facts['domicilios']), 1)
        self.assertEqual(facts['domicilios'][0]['direccion']['zip'], CP)

    def test_a_un_cliente_que_ya_existe_no_se_le_marca_la_ficha(self):
        cliente = self.Partner.create({'name': 'Ya Cliente', 'phone': NUEVO})
        resultado = self._guardar(name=None)
        self.assertTrue(resultado['saved'])
        self.assertFalse(resultado['partner_created'])
        self.assertFalse(cliente.visar_chat_sin_reserva)
        self.assertEqual(cliente.name, 'Ya Cliente')
        self.assertTrue(cliente._visar_domicilios_de_servicio().visar_chat_sin_reserva)

    def test_repetir_la_misma_direccion_no_la_duplica(self):
        self._guardar()
        segundo = self._guardar()
        self.assertTrue(segundo['saved'])
        self.assertFalse(segundo['address_created'])
        self.assertEqual(len(self._cliente()._visar_domicilios_de_servicio()), 1)

    def test_solo_se_conserva_la_ultima_direccion_sin_reserva(self):
        self._guardar()
        self._guardar(delivery_address=dict(DIRECCION, street='Av. Filosofos'))
        domicilios = self._cliente()._visar_domicilios_de_servicio()
        self.assertEqual(domicilios.mapped('street'), ['Av. Filosofos No. 315'])

    def test_una_direccion_con_pedido_no_se_archiva_al_guardar_otra(self):
        self._guardar()
        cliente = self._cliente()
        primera = cliente._visar_domicilios_de_servicio()
        self.env['sale.order'].sudo().create({
            'partner_id': cliente.id, 'partner_shipping_id': primera.id})
        self._guardar(delivery_address=dict(DIRECCION, street='Otra Calle'))
        self.assertEqual(len(cliente._visar_domicilios_de_servicio()), 2)

    def test_direccion_incompleta_o_sin_cobertura(self):
        self.assertEqual(
            self._guardar(delivery_address=dict(DIRECCION, ext_num=''))['reason'],
            'incomplete')
        self.assertEqual(
            self._guardar(delivery_address=dict(DIRECCION, zip='6470'))['reason'],
            'incomplete')
        self.assertEqual(
            self._guardar(delivery_address=dict(DIRECCION, zip='00001'))['reason'],
            'out_of_coverage')
        self.assertFalse(self._cliente())

    def test_dos_fichas_con_el_mismo_numero_no_se_adivina(self):
        self.Partner.create({'name': 'Uno', 'phone': NUEVO})
        self.Partner.create({'name': 'Dos', 'phone': NUEVO})
        self.assertEqual(self._guardar()['reason'], 'phone_ambiguous')

    # -- limpieza ---------------------------------------------------------

    def _envejecer(self, fichas, dias):
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = %s WHERE id IN %s",
            (fields.Datetime.now() - timedelta(days=dias), tuple(fichas.ids)))
        fichas.invalidate_recordset(['create_date'])

    def test_la_limpieza_archiva_lo_viejo_sin_pedidos(self):
        self._guardar()
        cliente = self._cliente()
        domicilio = cliente._visar_domicilios_de_servicio()
        self.Partner._visar_cron_archivar_chat_sin_reserva()
        self.assertTrue(cliente.active, "reciente: no se toca")
        self._envejecer(cliente | domicilio, 61)
        self.Partner._visar_cron_archivar_chat_sin_reserva()
        self.assertFalse(cliente.active)
        self.assertFalse(domicilio.active)
        self.assertTrue(self.Tools._agent_booking_needs_name(NUEVO),
                        "archivado, vuelve a ser un numero nuevo")

    def test_la_limpieza_respeta_el_plazo_de_ajustes(self):
        self.Param.set_param('visar.chat_address.days', '90')
        self._guardar()
        cliente = self._cliente()
        self._envejecer(cliente | cliente._visar_domicilios_de_servicio(), 61)
        self.Partner._visar_cron_archivar_chat_sin_reserva()
        self.assertTrue(cliente.active)

    def test_la_limpieza_no_toca_a_quien_tiene_un_pedido(self):
        self._guardar()
        cliente = self._cliente()
        domicilio = cliente._visar_domicilios_de_servicio()
        self.env['sale.order'].sudo().create({
            'partner_id': cliente.id, 'partner_shipping_id': domicilio.id})
        self._envejecer(cliente | domicilio, 61)
        self.Partner._visar_cron_archivar_chat_sin_reserva()
        self.assertTrue(cliente.active)
        self.assertTrue(domicilio.active)

    def test_la_limpieza_no_archiva_al_cliente_que_volvio_a_escribir(self):
        self._guardar()
        cliente = self._cliente()
        self._envejecer(cliente | cliente._visar_domicilios_de_servicio(), 61)
        self._guardar(delivery_address=dict(DIRECCION, street='Otra Calle'))
        self.Partner._visar_cron_archivar_chat_sin_reserva()
        self.assertTrue(cliente.active)

    def test_la_direccion_vieja_de_un_cliente_de_verdad_se_archiva_sola(self):
        cliente = self.Partner.create({'name': 'Ya Cliente', 'phone': NUEVO})
        self._guardar(name=None)
        domicilio = cliente._visar_domicilios_de_servicio()
        self._envejecer(domicilio, 61)
        self.Partner._visar_cron_archivar_chat_sin_reserva()
        self.assertTrue(cliente.active)
        self.assertFalse(domicilio.active)
