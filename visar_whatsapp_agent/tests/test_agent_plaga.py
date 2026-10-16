from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentPlaga(TransactionCase):
    """La plaga y el motivo se guardan en el lead de fumigacion en cuanto se
    dicen, y vuelven con el contexto del cliente (Visar, 16-oct-2026)."""

    WA = '5219990007788'
    NAT = '9990007788'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.team = cls.env.ref('visar_crm.crm_team_whatsapp')
        cls.cerrado = cls.env.ref('visar_crm.crm_stage_wa_cerrado')
        cls.fum = cls.env['visar.service.group'].search(
            [('code', '=', 'fumigacion')], limit=1)
        Plaga = cls.env['visar.plaga']
        cls.rastreros = Plaga.search([('code', '=', 'rastreros')], limit=1) \
            or Plaga.create({'code': 'rastreros', 'name': 'Rastreros',
                             'description': 'Cucarachas, alacranes'})
        cls.roedores = Plaga.search([('code', '=', 'roedores')], limit=1) \
            or Plaga.create({'code': 'roedores', 'name': 'Roedores'})

    def _lead(self):
        return self.Lead.search([('visar_wa_phone_norm', '=', self.NAT),
                                 ('visar_service_group_id', '=', self.fum.id)])

    def test_guarda_en_el_lead_de_fumigacion(self):
        res = self.Tools.agent_save_pest({
            'phone': self.WA, 'motivo': 'correctivo',
            'plagas': ['rastreros', 'roedores', 'inventada']})
        self.assertTrue(res['saved'])
        lead = self._lead()
        self.assertEqual(len(lead), 1)
        self.assertEqual(lead.team_id, self.team)
        self.assertEqual(lead.visar_wa_motivo, 'correctivo')
        self.assertEqual(set(lead.visar_wa_plaga_ids.mapped('code')),
                         {'rastreros', 'roedores'})
        self.assertTrue(lead.visar_wa_plaga_at)

    def test_el_payload_es_el_estado_completo(self):
        """Una correccion no deja pegada la plaga de antes."""
        self.Tools.agent_save_pest({'phone': self.WA, 'motivo': 'correctivo',
                                    'plagas': ['rastreros']})
        self.Tools.agent_save_pest({'phone': self.WA, 'motivo': 'correctivo',
                                    'valoracion': 'termitas'})
        lead = self._lead()
        self.assertEqual(len(lead), 1, 'el mismo lead, no otro')
        self.assertFalse(lead.visar_wa_plaga_ids)
        self.assertEqual(lead.visar_wa_plaga_valoracion, 'termitas')

    def test_sin_datos_o_telefono_malo_no_guarda(self):
        self.assertEqual(self.Tools.agent_save_pest(
            {'phone': self.WA, 'motivo': 'otro'})['skipped_reason'], 'empty')
        self.assertEqual(self.Tools.agent_save_pest(
            {'phone': '123', 'motivo': 'correctivo'})['skipped_reason'],
            'invalid_phone')
        self.assertFalse(self._lead())

    def test_vuelve_con_el_contexto_aunque_no_sea_cliente(self):
        self.Tools.agent_save_pest({'phone': self.WA, 'motivo': 'correctivo',
                                    'plagas': ['rastreros']})
        datos = self.Tools.agent_partner_facts({'phone': self.WA})
        self.assertEqual(datos['plaga']['motivo'], 'correctivo')
        self.assertEqual([p['code'] for p in datos['plaga']['plagas']],
                         ['rastreros'])
        self.assertIn('CONVERSACION ANTERIOR', datos['block'])
        self.assertIn('registrar_plaga', datos['block'])

    def test_un_lead_cerrado_no_se_recuerda(self):
        self.Tools.agent_save_pest({'phone': self.WA, 'motivo': 'preventivo'})
        self._lead().stage_id = self.cerrado
        datos = self.Tools.agent_partner_facts({'phone': self.WA})
        self.assertNotIn('plaga', datos)

    def test_el_catalogo_dice_lo_que_se_atiende_con_valoracion(self):
        datos = self.Tools.agent_catalog_snapshot()
        self.assertEqual([p['code'] for p in datos['plagas_valoracion']],
                         ['termitas', 'chinches'])
