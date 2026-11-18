# -*- coding: utf-8 -*-
"""Descartadas desde el lado del agente (19-nov-2026).

* Si el cliente vuelve, el agente NO reusa la descartada: abre otra en Nuevo con
  la anterior enlazada.
* En WhatsApp, lo que cuenta es el último mensaje DEL CLIENTE, no `write_date`.
* Descartar cancela el recontacto pendiente.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestDescartadasAgente(TransactionCase):

    RAW = '9990007788'
    WA = '5219990007788'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')
        cls.descartadas = cls.env.ref('visar_crm.crm_stage_descartadas')
        cls.env['visar.followup.config'].search([]).unlink()
        cls.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 0, 'window_end_hour': 24})
        cls.env['ir.config_parameter'].sudo().set_param('visar.crm.descartar_dias', '15')

    def _envejecer(self, lead, dias):
        antes = fields.Datetime.now() - timedelta(days=dias)
        self.env.cr.execute(
            "UPDATE crm_lead SET create_date = %s, write_date = %s WHERE id = %s",
            (antes, antes, lead.id))
        lead.invalidate_recordset(['create_date', 'write_date'])

    def _contacto(self, hace_dias):
        Contacto = self.env['visar.agent.contacto'].sudo()
        contacto = Contacto.search([('name', '=', self.RAW)]) or Contacto.create(
            {'name': self.RAW, 'phone': self.WA})
        contacto.write({'ultimo_mensaje': fields.Datetime.now() - timedelta(days=hace_dias)})
        return contacto

    def test_si_vuelve_abre_otra_con_la_anterior_enlazada(self):
        primera = self.Lead.browse(self.Tools.agent_track_interest({'phone': self.WA})['lead_id'])
        primera.stage_id = self.descartadas
        res = self.Tools.agent_track_interest({'phone': self.WA})
        self.assertTrue(res['created'])
        nueva = self.Lead.browse(res['lead_id'])
        self.assertNotEqual(nueva, primera)
        self.assertEqual(nueva.stage_id, self.nuevo)
        self.assertEqual(nueva.visar_descartada_previa_id, primera)

    def test_cuenta_el_ultimo_mensaje_del_cliente(self):
        lead = self.Lead.browse(self.Tools.agent_track_interest({'phone': self.WA})['lead_id'])
        self._envejecer(lead, 30)
        self._contacto(2)  # escribió hace 2 días: la ficha vieja sigue viva
        self.Lead._visar_crm_descartar_inactivas()
        self.assertEqual(lead.stage_id, self.nuevo)
        self._contacto(20)
        self.Lead._visar_crm_descartar_inactivas()
        self.assertEqual(lead.stage_id, self.descartadas)

    def test_descartar_cancela_el_recontacto(self):
        lead = self.Lead.browse(self.Tools.agent_track_interest({'phone': self.WA})['lead_id'])
        self.assertEqual(lead.visar_wa_followup_state, 'scheduled')
        self._envejecer(lead, 30)
        self._contacto(20)
        self.Lead._visar_crm_descartar_inactivas()
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')
        self.assertEqual(lead.visar_wa_followup_skip_code, 'descartada')
