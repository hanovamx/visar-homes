# -*- coding: utf-8 -*-
"""El recontacto sin contestar viaja con el contexto del cliente (21-oct-2026).

El cliente contestó el recontacto diez horas después; el runtime ya lo había
olvidado y lo saludó como a alguien nuevo. Odoo sí lo recuerda: estas pruebas
cuidan cuándo se le devuelve al runtime y cuándo ya no.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestRecontactoVigente(TransactionCase):

    WA = '5219990006611'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.env['visar.followup.config'].search([]).unlink()
        cls.config = cls.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 0, 'window_end_hour': 24})

    def _enviado(self, horas_atras=10, texto="Hola, ¿seguimos con la fumigación?"):
        res = self.Tools.agent_track_interest({
            'phone': self.WA,
            'context': {'etapa': 'cuestionario', 'wa_id': self.WA,
                        'servicio': 'Fumigación interior', 'colonia': 'Tecnológico',
                        'memoria': {'cp': '64700'}}})
        lead = self.Lead.browse(res['lead_id'])
        lead.write({
            'visar_wa_followup_state': 'sent',
            'visar_wa_followup_sent_at':
                fields.Datetime.now() - timedelta(hours=horas_atras),
            'visar_wa_followup_texto': texto,
        })
        return lead

    def _recontacto(self):
        return self.Tools.agent_partner_facts({'phone': self.WA}).get('recontacto')

    def test_viaja_con_el_texto_y_la_foto(self):
        lead = self._enviado()
        recontacto = self._recontacto()
        self.assertEqual(recontacto['lead_id'], lead.id)
        self.assertEqual(recontacto['texto'], "Hola, ¿seguimos con la fumigación?")
        self.assertEqual(recontacto['contexto']['colonia'], 'Tecnológico')
        self.assertEqual(recontacto['contexto']['memoria'], {'cp': '64700'})
        self.assertNotIn('wa_id', recontacto['contexto'])

    def test_pasada_la_vigencia_ya_no(self):
        self._enviado(horas_atras=49)
        self.assertIsNone(self._recontacto())

    def test_la_vigencia_se_cambia_en_odoo(self):
        self._enviado(horas_atras=49)
        self.config.vigencia_horas = 72
        self.assertTrue(self._recontacto())

    def test_la_vigencia_no_puede_ser_cero(self):
        with self.assertRaises(ValidationError):
            self.config.vigencia_horas = 0

    def test_si_ya_se_reprogramo_no_hay_que_retomar(self):
        """Volvió a escribir con interés: hay conversación nueva, no pendiente."""
        lead = self._enviado()
        lead.visar_wa_followup_state = 'scheduled'
        self.assertIsNone(self._recontacto())

    def test_si_el_lead_avanzo_no(self):
        lead = self._enviado()
        lead.stage_id = self.env.ref('visar_crm.crm_stage_wa_programado')
        self.assertIsNone(self._recontacto())

    def test_si_un_asesor_lo_tomo_despues_no(self):
        lead = self._enviado()
        lead.visar_wa_handoff_at = fields.Datetime.now()
        self.assertIsNone(self._recontacto())

    def test_sin_texto_guardado_va_el_de_respaldo(self):
        """Recontactos mandados antes de guardar el texto."""
        self._enviado(texto=False)
        self.assertIn("Nos quedamos a medias", self._recontacto()['texto'])

    def test_otro_telefono_no(self):
        self._enviado()
        self.assertNotIn('recontacto', self.Tools.agent_partner_facts(
            {'phone': '5219990006699'}))


@tagged('post_install', '-at_install')
class TestRecontactoTextoGuardado(TransactionCase):
    """El texto que redactó el modelo vuelve en la respuesta y se guarda."""

    WA = '5219990006622'

    def setUp(self):
        super().setUp()
        self.env['visar.followup.config'].search([]).unlink()
        self.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 0, 'window_end_hour': 24})
        res = self.env['visar.agent.tools'].agent_track_interest({
            'phone': self.WA, 'context': {'etapa': 'pregunto', 'wa_id': self.WA}})
        self.lead = self.env['crm.lead'].browse(res['lead_id'])
        self.lead.visar_wa_followup_due = fields.Datetime.from_string(
            '2020-01-01 00:00:00')
        self.env['crm.lead']._visar_wa_cron_followup()
        self.mensaje = self.env['visar.wa.lead.message'].search(
            [('lead_id', '=', self.lead.id)])

    def _enviar(self, respuesta):
        response = MagicMock(status_code=200)
        response.json.return_value = respuesta
        with patch('odoo.addons.visar_base.models.wa_outbox_mixin.requests.post',
                   return_value=response), \
                patch.object(type(self.mensaje), '_visar_wa_config',
                             return_value=('http://runtime.test', 'x')):
            return self.mensaje._visar_attempt_send()

    def test_se_guarda_lo_que_se_mando(self):
        self.assertTrue(self._enviar(
            {'sent': True, 'mode': 'free', 'text': "Hola, ¿seguimos?"}))
        self.assertEqual(self.lead.visar_wa_followup_state, 'sent')
        self.assertEqual(self.lead.visar_wa_followup_texto, "Hola, ¿seguimos?")

    def test_un_runtime_viejo_sin_texto_no_rompe(self):
        self.assertTrue(self._enviar({'sent': True, 'mode': 'free'}))
        self.assertEqual(self.lead.visar_wa_followup_state, 'sent')
        self.assertFalse(self.lead.visar_wa_followup_texto)
